#!/usr/bin/env python3
"""
verify_evidence.py — evidence-card P6 게이트

abstract-miner가 반환한 슬롯을 PubMed 초록과 대조해 검증한다.
LLM 판단은 이 단계에 개입하지 않는다.

검증 계약 (전부 통과해야 verified=true):
  C1. quote 가 PubMed 초록 원문에 부분문자열로 존재한다
  C2. value 가 quote 에 존재한다 — 숫자를 포함하면 수 경계까지 일치해야 한다
      — 부분문자열 검사는 "-0.34"를 "0.34"(부호 소실)·"34"(100배)로 통과시킨다
  C3. (effect 슬롯 한정) value 가 숫자 하나다 — 부호·소수점·% 까지만 허용
      — 단위는 unit, 지표명은 measure, p값은 significance 로 간다.
        "무엇이 효과 크기가 아닌가"를 열거하면 표현이 새 나올 때마다 구멍이
        생긴다. "무엇이 효과 크기인가"는 닫힌 형태라 열거가 끝난다
  C4. unit 이 quote 에 부분문자열로 존재한다
      — value만 검사하면 "17.36 min"을 "17.36 hours"로 바꿔 적어도 통과한다
  C5. (conclusion 슬롯 한정) quote 만 검사한다
      — value·unit이 없는 슬롯이다. 저자 결론 문장 자체가 산출물이다

무결성 정보(철회·정오표·이해충돌·연도·저널·PublicationType)는 검증이 아니라
같은 efetch 응답에서 기계적으로 읽어 옮긴 것이다. 추가 조회는 하지 않는다.
철회 논문은 슬롯이 전부 통과하더라도 팩에서 제외한다 — 검증은 "그 초록에
그 값이 있다"만 보증하므로 철회를 걸러내지 못한다.

C1은 초록을 스크립트가 독립적으로 재조회해 확인하므로,
에이전트가 quote를 지어내면 통과할 수 없다.
C3은 C1/C2를 통과하는 "원문에 실재하지만 효과 크기가 아닌" 문자열
(예: "p < 0.05")을 막는다. 프롬프트 규칙만으로는 새어 나온다.

의존성: 표준 라이브러리만 사용.

사용:
  python3 verify_evidence.py --input raw_slots.json --output evidence-pack.json
  python3 verify_evidence.py --input raw_slots.json --meta meta.json --output pack.json

종료 코드:
  0  팩 생성 완료 (슬롯 폐기는 정상 흐름이므로 0이다. 개수는 stdout·팩에 있다)
  2  팩을 신뢰할 수 없음 — 입력이 비었거나, 검증 불가 논문이 성공한 논문 이상
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from xml.etree import ElementTree as ET

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SLOTS = ("effect", "dose", "population", "form")
# value가 없고 quote만 있는 슬롯. 검증 계약이 C1 하나뿐이라 따로 돈다.
QUOTE_SLOTS = ("conclusion",)
COMPARATORS = frozenset({"placebo", "active", "baseline"})
# 지표명. value에서 분리해 두어야 value가 "숫자 하나"라는 닫힌 형태를 유지한다.
# "RR 0.78"을 통째로 value에 넣으면 C3가 프로즈를 허용해야 하고,
# 그 순간 "p for trend = 0.03"도 같이 들어온다.
MEASURES = frozenset({"rr", "or", "hr", "smd", "md"})

# NLM 색인자가 붙인 PublicationType은 miner의 design 판정과 독립적으로 만들어진다.
# 그래서 대조할 수 있다. 단 태그가 없는 레코드가 흔하므로(2013년 RCT가
# 'Journal Article'만 달고 있는 식) 부재를 불일치로 읽으면 안 된다.
PT_DESIGN = {
    "meta-analysis": "meta-analysis",
    "systematic review": "systematic-review",
    "randomized controlled trial": "rct",
    "observational study": "observational",
    "review": "review",
}
# 'Review'는 서술형 리뷰와 체계적 문헌고찰을 구분하지 못하므로 단독으로는
# 판정 근거가 약하다. 나머지만 설계를 특정한다.
STRONG_DESIGNS = frozenset({"meta-analysis", "systematic-review", "rct", "observational"})

# 값이 실재하지 않거나 효과 크기가 아니어서 버린 경우. 추출 자체가 없었던
# not_extracted / missing_value_or_quote / missing_quote 는 폐기가 아니라 정보다.
REJECT_REASONS = frozenset({
    "quote_not_in_abstract", "value_not_in_quote",
    "p_value_not_effect_size", "ci_only_not_effect_size",
    "no_numeric_effect", "effect_value_not_numeric", "unit_not_in_quote",
})

# 대조 전 정규화 대상: 유니코드 대시/공백/따옴표류
DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
QUOTES = dict.fromkeys(map(ord, "\u2018\u2019\u201b\u2032"), "'")
DQUOTES = dict.fromkeys(map(ord, "\u201c\u201d\u201f\u2033"), '"')


# C3의 판정. effect의 value는 숫자 하나여야 한다 — 부호·소수점·천단위·% 까지.
# 이전에는 "효과 크기가 아닌 것"(p값·신뢰구간·서술어)을 걷어낸 뒤 숫자가
# 남는지 보는 차감식이었다. 그 방식은 걷어낼 표현을 열거해야 하는데
# 자연어 표현은 열려 있어서 "p for trend", "p values ranged from" 처럼
# 새 표현이 나올 때마다 구멍이 생겼다. 반대로 "숫자 하나"는 닫힌 형태다.
EFFECT_VALUE = re.compile(r"^-?\d+(?:,\d{3})*(?:\.\d+)?%?$")

# 아래 둘은 이제 판정에 쓰이지 않는다. 폐기 사유를 사람이 읽을 수 있게
# 라벨을 붙이는 용도다 — 못 맞혀도 폐기 결정은 이미 EFFECT_VALUE가 내렸다.
CI_TOKEN = re.compile(
    r"\(?\s*(?:\d{2}\s*%?\s*)?(?:ci|confidence interval)\b"
    r"[\s:=]*[-\d.,\s]*(?:to\s*[-\d.]+)?\s*\)?"
)
# (?<![a-z]) 없이는 "sbp -5.2", "sleep -1.2"의 끝 p가 p값으로 오인되어
# 정상 효과 크기가 조용히 폐기된다. 문자클래스의 하이픈이 음수 부호를 먹는다.
P_TOKEN = re.compile(
    r"\(?\s*(?<![a-z])p[\s-]*(?:value|val)?\s*[-=<>\u2264\u2265]{1,2}\s*[.\d][\d.eE+-]*\s*\)?"
)
HAS_DIGIT = re.compile(r"\d")

# C2: 숫자가 든 값은 "다른 수의 일부"로 매칭되면 안 된다.
# 부분문자열 검사는 "-0.34"의 부호를 지운 "0.34", 자릿수를 자른 "34"·"17",
# 반올림한 "17"을 전부 통과시킨다 — C4가 unit 쪽에서 막은 것과 같은 유형의
# 구멍이 value 쪽에 남아 있었다. 부호 반전과 자릿수 변경은 카드에 실리는
# 순간 그대로 오정보가 되므로 수 경계로 본다.
NUM_EDGE_L = r"(?<![0-9.,\-])"
NUM_EDGE_R = r"(?![0-9]|[.,][0-9])"


def value_in_quote(value_norm: str, quote_norm: str) -> bool:
    """값이 quote 안에 있는가. 숫자를 포함하면 수 경계까지 맞아야 한다."""
    if not HAS_DIGIT.search(value_norm):
        return value_norm in quote_norm
    return re.search(NUM_EDGE_L + re.escape(value_norm) + NUM_EDGE_R, quote_norm) is not None


def effect_shape_reason(value_norm: str) -> str | None:
    """effect 값이 숫자 하나가 아니면 폐기 사유를, 맞으면 None을 반환한다.

    p값은 "우연이 아니다"라는 판정이지 크기가 아니므로 카드에 쓸 수 없다.
    신뢰구간도 점추정치 없이 구간만 있으면 카드에 쓸 숫자가 없는 것이다.
    단위는 unit, 지표명(RR·OR·HR·SMD·MD)은 measure, p값은 significance로
    간다 — 그래서 value에 남는 것은 숫자뿐이다.
    """
    if EFFECT_VALUE.match(value_norm):
        return None
    # 여기서부터는 폐기가 확정됐다. 아래는 사람이 읽을 라벨을 고르는 것뿐이라
    # 못 맞혀도 판정은 바뀌지 않는다. p값/CI를 걷어내고도 숫자가 남으면
    # "숫자는 맞는데 value에 다른 것이 섞였다"는 뜻이다.
    if not HAS_DIGIT.search(value_norm):
        return "no_numeric_effect"
    if HAS_DIGIT.search(P_TOKEN.sub(" ", CI_TOKEN.sub(" ", value_norm))):
        return "effect_value_not_numeric"
    if P_TOKEN.search(value_norm):
        return "p_value_not_effect_size"
    return "ci_only_not_effect_size"


def normalize(text: str) -> str:
    """비교용 정규화. 의미를 바꾸지 않는 표기 차이만 흡수한다.

    숫자와 단위는 절대 건드리지 않는다 — 그것을 검사하는 게 목적이므로.
    """
    if text is None:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(DASHES).translate(QUOTES).translate(DQUOTES)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def parse_record(root: ET.Element) -> dict:
    """efetch 응답에서 초록과 무결성 정보를 읽는다. 판단은 하지 않는다.

    전부 한 응답 안에 있으므로 추가 조회 비용이 없다. 여기서 읽지 않으면
    같은 정보를 사람이 PubMed에서 눈으로 다시 확인해야 한다.
    """
    parts = []
    for node in root.iter("AbstractText"):
        label = node.get("Label")
        text = "".join(node.itertext())
        parts.append(f"{label}: {text}" if label else text)
    if parts:
        # 초록이 있을 때만 제목을 붙인다. 제목만으로 대조하면 전부 환각으로 오분류된다
        for node in root.iter("ArticleTitle"):
            parts.append("".join(node.itertext()))
    abstract = " ".join(parts)

    pub_types = [t for t in ((e.text or "").strip() for e in root.iter("PublicationType")) if t]

    refs: dict[str, list[str]] = {}
    for node in root.iter("CommentsCorrections"):
        pmid = (node.findtext("PMID") or "").strip()
        if pmid:
            refs.setdefault(node.get("RefType") or "", []).append(pmid)

    # CoiStatement는 요소가 있어도 내용이 빈 레코드가 흔하다. 빈 값을
    # "이해충돌 없음"으로 읽으면 안 되므로 원문과 존재 여부를 따로 둔다.
    coi = " ".join((root.findtext(".//CoiStatement") or "").split())

    year = None
    pubdate = root.find(".//Article/Journal/JournalIssue/PubDate")
    if pubdate is not None:
        year = (pubdate.findtext("Year") or "").strip() or None
        if not year:
            m = re.search(r"\d{4}", pubdate.findtext("MedlineDate") or "")
            year = m.group() if m else None

    journal = (root.findtext(".//Article/Journal/ISOAbbreviation")
               or root.findtext(".//Article/Journal/Title") or "").strip()

    return {
        "abstract": abstract,
        # 초록 전문은 저장하지 않는다. 해시만 남기면 재검증 때 "그때 그 초록인가"를
        # 1회 조회로 판정할 수 있다. 조립 방식이 바뀌면 값도 바뀐다.
        "abstract_sha256": hashlib.sha256(abstract.encode("utf-8")).hexdigest() if abstract else None,
        "year": year,
        "journal": journal or None,
        "pub_types": pub_types,
        "integrity": {
            "retracted": "Retracted Publication" in pub_types or bool(refs.get("RetractionIn")),
            "retraction_pmids": refs.get("RetractionIn", []),
            "has_erratum": bool(refs.get("ErratumIn")),
            "erratum_pmids": refs.get("ErratumIn", []),
            "expression_of_concern": bool(refs.get("ExpressionOfConcernIn")),
            "coi_statement": coi or None,
            "coi_available": bool(coi),
        },
    }


def design_check(design: str | None, pub_types: list[str]) -> dict:
    """miner의 design 판정을 NLM PublicationType과 대조한다.

    태그가 없으면 `agrees: null`이다. 부재는 불일치가 아니다.
    """
    implied = {PT_DESIGN[t] for t in (p.lower() for p in pub_types) if t in PT_DESIGN}
    claimed = (design or "").strip().lower()
    if not implied:
        agrees = None
    elif implied & STRONG_DESIGNS:
        agrees = claimed in implied
    else:
        # 'Review' 태그뿐인 레코드. 원 시험 설계를 주장할 때만 불일치로 본다.
        agrees = claimed in {"review", "meta-analysis", "systematic-review"}
    return {"pub_types": pub_types, "implied": sorted(implied), "agrees": agrees}


def fetch_record(pmid: str, api_key: str | None = None, retries: int = 2) -> dict:
    """PubMed 레코드를 독립 조회한다. 에이전트 출력을 신뢰하지 않는다."""
    params = {"db": "pubmed", "id": pmid, "retmode": "xml", "rettype": "abstract"}
    if api_key:
        params["api_key"] = api_key
    url = f"{EUTILS}/efetch.fcgi?{urllib.parse.urlencode(params)}"

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "evidence-card/0.1 (pharmacy content tool)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml = resp.read()
            return parse_record(ET.fromstring(xml))
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"PMID {pmid} 레코드 조회 실패: {last_err}")


def verify_slot(slot_name: str, slot: dict | None, abstract_norm: str) -> dict:
    """단일 슬롯 검증. 통과 못하면 value를 null로 만들고 사유를 남긴다."""
    if slot is None:
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "not_extracted"}

    value = slot.get("value")
    quote = slot.get("quote")

    if not value or not quote:
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "missing_value_or_quote"}

    quote_norm = normalize(quote)
    value_norm = normalize(value)

    # C1: quote ⊂ abstract
    if quote_norm not in abstract_norm:
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "quote_not_in_abstract",
                "rejected_value": value, "rejected_quote": quote}

    # C2: value ⊂ quote (숫자는 수 경계까지)
    if not value_in_quote(value_norm, quote_norm):
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "value_not_in_quote",
                "rejected_value": value, "rejected_quote": quote}

    # C3: effect 슬롯만 — 원문에 있어도 효과 크기가 아니면 폐기
    if slot_name == "effect":
        reason = effect_shape_reason(value_norm)
        if reason:
            return {"slot": slot_name, "value": None, "verified": False,
                    "reason": reason,
                    "rejected_value": value, "rejected_quote": quote}

    # C4: unit ⊂ quote — value만 검사하면 무방비인 구멍.
    # 스키마가 value와 unit을 분리해 두었으므로 "17.36" + "hours"로
    # min을 시간으로 바꿔 적어도 C2는 통과한다. 카드에 찍히는 것은 둘의 합이다.
    # 부분문자열이 아니라 낱말 경계로 본다 — "magnesium"의 g에 unit="g"가 걸리면
    # 500 mg가 500 g(1000배)로 통과한다. C4가 막으려던 바로 그 유형이다.
    unit = slot.get("unit")
    if unit and not re.search(rf"(?<![a-z]){re.escape(normalize(unit))}(?![a-z])", quote_norm):
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "unit_not_in_quote",
                "rejected_value": value, "rejected_unit": unit,
                "rejected_quote": quote}

    out = {
        "slot": slot_name,
        "value": value,
        "unit": unit,
        "quote": quote,
        "verified": True,
    }
    if slot_name == "effect":
        # unit_type은 에이전트 판정을 받지 않고 값에서 파생시킨다.
        # 신뢰할 필요가 없는 것은 신뢰 표면에서 뺀다.
        out["unit_type"] = "percent" if "%" in f"{value}{unit or ''}" else "absolute"

        # comparator는 초록 문장이 아니라 판정이므로 부분문자열 검사가 불가능하다.
        # 열거값 밖이면 null — 위약 대비인지 복용 전후인지 단정하지 않는 쪽이 안전하다.
        comparator = slot.get("comparator")
        out["comparator"] = comparator if comparator in COMPARATORS else None

        # measure도 열거 검증만 한다. quote 대조는 하지 않는다 —
        # 저자가 "relative risk"로 풀어 쓰면 "rr"은 초록에 없고, 반대로
        # "or"은 영어 접속사라 아무 문장에나 걸린다. 어느 쪽도 검사가 못 된다.
        # 숫자가 아니므로 틀려도 자릿수 오류로는 번지지 않는다.
        measure = normalize(slot.get("measure"))
        out["measure"] = measure if measure in MEASURES else None

        # significance는 헤드라인 숫자가 아니라 부가 정보다.
        # quote 밖이면 슬롯을 죽이지 않고 이 필드만 버린다 —
        # p값이 효과 문장과 다른 문장에 있는 정상 케이스에서 멀쩡한 슬롯이 죽는다.
        significance = slot.get("significance")
        if significance and value_in_quote(normalize(significance), quote_norm):
            out["significance"] = significance
        else:
            out["significance"] = None
            if significance:
                out["dropped_significance"] = significance
    return out


def verify_quote_slot(slot_name: str, slot: dict | None, abstract_norm: str) -> dict:
    """value 없는 슬롯(conclusion) 검증. C1만 적용한다.

    저자 결론 문장은 숫자가 아니라 문장 자체가 산출물이므로 C2~C4가 없다.
    이 슬롯이 통과해야 `direction` 판정에 대조 가능한 근거가 생긴다 —
    지금까지 direction은 miner의 판정만 있고 원문이 남지 않았다.
    """
    if slot is None:
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "not_extracted"}
    # 문장 하나짜리 슬롯이라 에이전트가 dict 대신 문자열을 낼 수 있다.
    if isinstance(slot, str):
        slot = {"quote": slot}
    if not isinstance(slot, dict):
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "missing_quote"}

    quote = slot.get("quote")
    if not quote:
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "missing_quote"}

    if normalize(quote) not in abstract_norm:
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "quote_not_in_abstract", "rejected_quote": quote}

    return {"slot": slot_name, "quote": quote, "verified": True}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True,
                    help="abstract-miner 결과 배열 JSON")
    ap.add_argument("--output", required=True, help="evidence-pack.json 경로")
    ap.add_argument("--meta", help="정규화·scout 결과 JSON (선택)")
    args = ap.parse_args()

    api_key = os.environ.get("NCBI_API_KEY")

    with open(args.input, encoding="utf-8") as f:
        records = json.load(f)
    if isinstance(records, dict):
        records = [records]

    if not records:
        print("입력에 레코드가 없다. 검증할 것이 없으므로 팩을 만들지 않는다.",
              file=sys.stderr)
        return 2

    meta = {}
    if args.meta:
        with open(args.meta, encoding="utf-8") as f:
            meta = json.load(f)

    results = []
    excluded = []  # 팩에서 빠진 논문. 무엇이 빠졌는지가 남아야 팩을 읽을 수 있다
    rejected_count = 0
    fetch_failed = []
    no_abstract = []
    retracted = []

    for i, rec in enumerate(records):
        # 성공 경로에만 두면 조회가 연속 실패할 때 재시도 폭주가 된다.
        # E-utilities는 이럴 때 IP를 막는다.
        if i:
            time.sleep(0.35 if api_key else 0.4)
        pmid = rec.get("pmid")
        if not pmid or rec.get("error"):
            fetch_failed.append(pmid)
            excluded.append({"pmid": pmid, "reason": rec.get("error") or "no_pmid"})
            continue

        try:
            record = fetch_record(pmid, api_key)
        except RuntimeError as e:
            print(f"[FETCH FAIL] {e}", file=sys.stderr)
            fetch_failed.append(pmid)
            excluded.append({"pmid": pmid, "reason": "fetch_failed"})
            continue

        integrity = record["integrity"]
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        # 철회 논문은 슬롯이 전부 검증을 통과해도 카드에 쓸 수 없다.
        # C1~C4는 "그 초록에 그 값이 있다"만 보므로 철회를 걸러내지 못한다.
        # 초록 유무보다 먼저 본다 — 철회 사실이 더 중요한 정보다.
        if integrity["retracted"]:
            print(f"[RETRACTED] pmid={pmid} 철회된 논문이다. 슬롯 전체를 버린다.",
                  file=sys.stderr)
            retracted.append(pmid)
            excluded.append({"pmid": pmid, "reason": "retracted",
                             "retraction_pmids": integrity["retraction_pmids"],
                             "url": url})
            continue

        if not record["abstract"].strip():
            print(f"[NO ABSTRACT] pmid={pmid} 초록이 없는 레코드다. 검증 불가로 제외.",
                  file=sys.stderr)
            no_abstract.append(pmid)
            excluded.append({"pmid": pmid, "reason": "no_abstract", "url": url})
            continue

        abstract_norm = normalize(record["abstract"])
        slots_in = rec.get("slots", {})

        verified_slots = {}
        for name in SLOTS:
            outcome = verify_slot(name, slots_in.get(name), abstract_norm)
            if not outcome["verified"] and outcome["reason"] in REJECT_REASONS:
                rejected_count += 1
                print(
                    f"[REJECT] pmid={pmid} slot={name} reason={outcome['reason']} "
                    f"value={outcome.get('rejected_value')!r}",
                    file=sys.stderr,
                )
            verified_slots[name] = outcome
        for name in QUOTE_SLOTS:
            outcome = verify_quote_slot(name, slots_in.get(name), abstract_norm)
            if not outcome["verified"] and outcome["reason"] in REJECT_REASONS:
                rejected_count += 1
                print(f"[REJECT] pmid={pmid} slot={name} reason={outcome['reason']}",
                      file=sys.stderr)
            verified_slots[name] = outcome

        results.append({
            "pmid": pmid,
            "design": rec.get("design"),
            "direction": rec.get("direction"),
            "subgroup_only": rec.get("subgroup_only"),
            "year": record["year"],
            "journal": record["journal"],
            "integrity": integrity,
            "design_check": design_check(rec.get("design"), record["pub_types"]),
            "abstract_sha256": record["abstract_sha256"],
            "url": url,
            "slots": verified_slots,
        })

    excluded_count = len(fetch_failed) + len(no_abstract) + len(retracted)
    total = sum(1 for r in results for s in r["slots"].values())
    passed = sum(1 for r in results for s in r["slots"].values() if s["verified"])

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    warnings = [
        "이 팩은 근거 수집 결과일 뿐이며 광고법 검토를 거치지 않았다. "
        "성분 설명과 제품 효능 표방의 경계를 확인하기 전에 발행하지 말 것.",
        "verified=false 슬롯의 값은 의도적으로 제거되었다. 복원하지 말 것.",
        "이 게이트는 '이 값이 그 초록에 실재한다'만 보증한다. "
        "'이 값이 그 슬롯의 올바른 값이다'는 보증하지 않는다 — "
        "같은 문장의 용량 숫자가 효과 크기 자리에 들어가도 통과한다. "
        "조작(변환·반올림·창작)은 막지만 오배치는 사람이 봐야 한다.",
    ]

    # 효과 크기를 한 표에 나란히 놓으려면 축이 같아야 한다.
    # %와 절대량, 위약 대비와 복용 전후는 서로 비교할 수 없는 수치다.
    effects = [r["slots"]["effect"] for r in results if r["slots"]["effect"]["verified"]]
    for field, label in (("unit_type", "단위 기준"), ("comparator", "비교 대상")):
        kinds = {e.get(field) for e in effects if e.get(field)}
        if len(kinds) > 1:
            warnings.append(
                f"effect {label}이 논문마다 다르다 ({', '.join(sorted(kinds))}). "
                "한 표에 나란히 놓지 말고 슬라이드를 나눌 것."
            )
    if any(e.get("comparator") is None for e in effects):
        warnings.append(
            "comparator가 null인 effect가 있다. 위약 대비인지 복용 전후인지 "
            "초록에 없다는 뜻이므로, 카피에서 비교 대상을 단정하지 말 것."
        )

    if excluded_count:
        warnings.append(
            f"{excluded_count}편이 팩에서 제외되었다 "
            f"(조회 실패 {len(fetch_failed)}, 초록 없음 {len(no_abstract)}, "
            f"철회 {len(retracted)}). "
            "이 팩은 검색된 근거 전체가 아니라 검증에 성공한 일부다."
        )

    # ── 무결성: 판정하지 않고 사실만 올린다 ──
    if retracted:
        warnings.insert(0, (
            f"철회된 논문 {len(retracted)}편을 제외했다 (PMID {', '.join(retracted)}). "
            "이 PMID는 인용하지 말고, 이미 발행한 콘텐츠에 쓰이지 않았는지 확인할 것."
        ))
    eoc = [r["pmid"] for r in results if r["integrity"]["expression_of_concern"]]
    if eoc:
        warnings.append(
            f"우려 표명(Expression of Concern)이 붙은 논문이 있다 (PMID {', '.join(eoc)}). "
            "철회는 아니지만 인용 전 사유를 확인할 것."
        )
    errata = [r["pmid"] for r in results if r["integrity"]["has_erratum"]]
    if errata:
        warnings.append(
            f"정오표(erratum)가 있는 논문이 있다 (PMID {', '.join(errata)}). "
            "초록의 수치가 정정 대상인지 원문에서 확인할 것."
        )
    coi_yes = sum(1 for r in results if r["integrity"]["coi_available"])
    if results:
        warnings.append(
            f"이해충돌 진술: 있음 {coi_yes}편, 없음 {len(results) - coi_yes}편. "
            "진술 원문(coi_statement)을 읽고 제조사 관련 여부는 사람이 판단할 것 — "
            "이 팩은 판단하지 않는다. 미공개는 '이해충돌 없음'이 아니라 "
            "PubMed가 받지 못했다는 뜻이다."
        )
    mismatched = [r["pmid"] for r in results if r["design_check"]["agrees"] is False]
    if mismatched:
        warnings.append(
            f"design 판정이 NLM PublicationType과 어긋나는 논문이 있다 "
            f"(PMID {', '.join(mismatched)}). 톤 판정이 design 위에 서 있으므로 확인할 것."
        )
    warnings.append(
        f"철회·정오표·이해충돌 정보는 {generated_at} 시점의 PubMed 스냅샷이다. "
        "철회는 나중에 일어나므로, 오래된 팩을 재사용할 때는 다시 조회할 것 "
        "(abstract_sha256으로 초록 변경 여부를 대조할 수 있다)."
    )

    pack = {
        "schema": "evidence-pack/0.2",
        "topic": meta.get("topic"),
        "tone": meta.get("tone"),
        "tone_reason": meta.get("tone_reason"),
        "evidence_map": meta.get("evidence_map"),
        "guideline_hit": meta.get("guideline_hit"),
        "papers": results,
        "verification": {
            "slots_total": total,
            "slots_verified": passed,
            "slots_rejected": rejected_count,
            "fetch_failed_pmids": fetch_failed,
            "no_abstract_pmids": no_abstract,
            "retracted_pmids": retracted,
            "excluded": excluded,
        },
        "warnings": warnings,
        "generated_at": generated_at,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)

    print(f"검증 완료: {passed}/{total} 통과, {rejected_count} 폐기, "
          f"{excluded_count}편 제외(철회 {len(retracted)}) → {args.output}")

    # 팩에 들어가지 못한 논문이 들어간 논문보다 많으면 이 팩은 지형을 대표하지 못한다.
    # 8편 중 7편이 조회되지 않았는데 0을 반환하면 호출자는 정상으로 읽는다.
    # 반씩 갈린 경우(4/4)도 대표성이 없으므로 >= 로 둔다.
    if excluded_count >= len(results):
        print(f"제외 {excluded_count}편 ≥ 수록 {len(results)}편. 팩을 신뢰하지 말 것.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
