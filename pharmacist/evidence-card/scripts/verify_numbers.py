#!/usr/bin/env python3
"""
verify_numbers.py — evidence-card P6 게이트

abstract-miner가 반환한 슬롯을 PubMed 초록과 대조해 검증한다.
LLM 판단은 이 단계에 개입하지 않는다.

검증 계약 (전부 통과해야 verified=true):
  C1. quote 가 PubMed 초록 원문에 부분문자열로 존재한다
  C2. value 가 quote 에 부분문자열로 존재한다
  C3. (effect 슬롯 한정) value 가 효과 크기의 형태다
      — p값 단독·신뢰구간 단독·서술어는 효과 크기가 아니므로 폐기
  C4. unit 이 quote 에 부분문자열로 존재한다
      — value만 검사하면 "17.36 min"을 "17.36 hours"로 바꿔 적어도 통과한다

C1은 초록을 스크립트가 독립적으로 재조회해 확인하므로,
에이전트가 quote를 지어내면 통과할 수 없다.
C3은 C1/C2를 통과하는 "원문에 실재하지만 효과 크기가 아닌" 문자열
(예: "p < 0.05")을 막는다. 프롬프트 규칙만으로는 새어 나온다.

의존성: 표준 라이브러리만 사용.

사용:
  python3 verify_numbers.py --input raw_slots.json --output evidence-pack.json
  python3 verify_numbers.py --input raw_slots.json --meta meta.json --output pack.json

종료 코드:
  0  팩 생성 완료 (슬롯 폐기는 정상 흐름이므로 0이다. 개수는 stdout·팩에 있다)
  2  팩을 신뢰할 수 없음 — 입력이 비었거나, 검증 불가 논문이 성공한 논문 이상
"""

import argparse
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
COMPARATORS = frozenset({"placebo", "active", "baseline"})

# 값이 실재하지 않거나 효과 크기가 아니어서 버린 경우. 추출 자체가 없었던
# not_extracted / missing_value_or_quote 는 폐기가 아니라 정보다.
REJECT_REASONS = frozenset({
    "quote_not_in_abstract", "value_not_in_quote",
    "p_value_not_effect_size", "ci_only_not_effect_size",
    "no_numeric_effect", "unit_not_in_quote",
})

# 대조 전 정규화 대상: 유니코드 대시/공백/따옴표류
DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
QUOTES = dict.fromkeys(map(ord, "\u2018\u2019\u201b\u2032"), "'")
DQUOTES = dict.fromkeys(map(ord, "\u201c\u201d\u201f\u2033"), '"')


# C3: effect 값에서 "효과 크기가 아닌 것"을 걷어낸 뒤 숫자가 남는지 본다.
# 앵커(^...$) 매칭은 접두·접미어 한 글자에 무력화된다 —
# "significant at p<0.001"은 p값 패턴에도, 서술어 패턴에도 걸리지 않는다.
# 차감식은 걷어낸 뒤 남는 것을 보므로 붙어 있는 말에 영향을 받지 않는다.
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


def effect_shape_reason(value_norm: str) -> str | None:
    """effect 슬롯 값이 효과 크기가 아니면 사유를, 맞으면 None을 반환한다.

    효과 크기 = 변화율 / 변화량(단위 포함) / 군간 차이 / 위험도 비.
    p값은 "우연이 아니다"라는 판정이지 크기가 아니므로 카드에 쓸 수 없다.
    신뢰구간도 점추정치 없이 구간만 있으면 카드에 쓸 숫자가 없는 것이다.
    """
    remainder = P_TOKEN.sub(" ", CI_TOKEN.sub(" ", value_norm))
    if HAS_DIGIT.search(remainder):
        return None
    # 남은 숫자가 없다 = 이 값은 p값/신뢰구간/서술어뿐이다. 무엇이었는지만 구분한다.
    if P_TOKEN.search(value_norm):
        return "p_value_not_effect_size"
    if CI_TOKEN.search(value_norm):
        return "ci_only_not_effect_size"
    return "no_numeric_effect"


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


def fetch_abstract(pmid: str, api_key: str | None = None, retries: int = 2) -> str:
    """PubMed에서 초록을 독립 조회한다. 에이전트 출력을 신뢰하지 않는다."""
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
            root = ET.fromstring(xml)
            parts = []
            for node in root.iter("AbstractText"):
                label = node.get("Label")
                text = "".join(node.itertext())
                parts.append(f"{label}: {text}" if label else text)
            if not parts:
                return ""  # 초록이 없는 레코드. 제목만으로 대조하면 전부 환각으로 오분류된다
            for node in root.iter("ArticleTitle"):
                parts.append("".join(node.itertext()))
            return " ".join(parts)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"PMID {pmid} 초록 조회 실패: {last_err}")


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

    # C2: value ⊂ quote
    if value_norm not in quote_norm:
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

        # significance는 헤드라인 숫자가 아니라 부가 정보다.
        # quote 밖이면 슬롯을 죽이지 않고 이 필드만 버린다 —
        # p값이 효과 문장과 다른 문장에 있는 정상 케이스에서 멀쩡한 슬롯이 죽는다.
        significance = slot.get("significance")
        if significance and normalize(significance) in quote_norm:
            out["significance"] = significance
        else:
            out["significance"] = None
            if significance:
                out["dropped_significance"] = significance
    return out


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
    rejected_count = 0
    fetch_failed = []
    no_abstract = []

    for i, rec in enumerate(records):
        # 성공 경로에만 두면 조회가 연속 실패할 때 재시도 폭주가 된다.
        # E-utilities는 이럴 때 IP를 막는다.
        if i:
            time.sleep(0.35 if api_key else 0.4)
        pmid = rec.get("pmid")
        if not pmid or rec.get("error"):
            fetch_failed.append(pmid)
            continue

        try:
            abstract = fetch_abstract(pmid, api_key)
        except RuntimeError as e:
            print(f"[FETCH FAIL] {e}", file=sys.stderr)
            fetch_failed.append(pmid)
            continue

        if not abstract.strip():
            print(f"[NO ABSTRACT] pmid={pmid} 초록이 없는 레코드다. 검증 불가로 제외.",
                  file=sys.stderr)
            no_abstract.append(pmid)
            continue

        abstract_norm = normalize(abstract)
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

        results.append({
            "pmid": pmid,
            "design": rec.get("design"),
            "direction": rec.get("direction"),
            "subgroup_only": rec.get("subgroup_only"),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "slots": verified_slots,
        })

    unverifiable = len(fetch_failed) + len(no_abstract)
    total = sum(1 for r in results for s in r["slots"].values())
    passed = sum(1 for r in results for s in r["slots"].values() if s["verified"])

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

    if unverifiable:
        warnings.append(
            f"{unverifiable}편이 검증되지 않았다 "
            f"(조회 실패 {len(fetch_failed)}, 초록 없음 {len(no_abstract)}). "
            "이 팩은 검색된 근거 전체가 아니라 검증에 성공한 일부다."
        )

    pack = {
        "schema": "evidence-pack/0.1",
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
        },
        "warnings": warnings,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)

    print(f"검증 완료: {passed}/{total} 통과, {rejected_count} 폐기, "
          f"{unverifiable}편 검증불가 → {args.output}")

    # 검증에 실패한 논문이 통과한 논문보다 많으면 이 팩은 지형을 대표하지 못한다.
    # 8편 중 7편이 조회되지 않았는데 0을 반환하면 호출자는 정상으로 읽는다.
    # 반씩 갈린 경우(4/4)도 대표성이 없으므로 >= 로 둔다.
    if unverifiable >= len(results):
        print(f"검증 불가 {unverifiable}편 ≥ 검증 성공 {len(results)}편. 팩을 신뢰하지 말 것.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
