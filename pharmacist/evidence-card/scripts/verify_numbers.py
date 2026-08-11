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

C1은 초록을 스크립트가 독립적으로 재조회해 확인하므로,
에이전트가 quote를 지어내면 통과할 수 없다.
C3은 C1/C2를 통과하는 "원문에 실재하지만 효과 크기가 아닌" 문자열
(예: "p < 0.05")을 막는다. 프롬프트 규칙만으로는 새어 나온다.

의존성: 표준 라이브러리만 사용.

사용:
  python3 verify_numbers.py --input raw_slots.json --output evidence-pack.json
  python3 verify_numbers.py --input raw_slots.json --meta meta.json --output pack.json

종료 코드:
  0  전 슬롯 통과
  1  일부 슬롯 폐기 (정상 동작. 결과는 생성됨)
  2  초록 조회 실패 등 검증 불가
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

# 값이 실재하지 않거나 효과 크기가 아니어서 버린 경우. 추출 자체가 없었던
# not_extracted / missing_value_or_quote 는 폐기가 아니라 정보다.
REJECT_REASONS = frozenset({
    "quote_not_in_abstract", "value_not_in_quote",
    "p_value_not_effect_size", "ci_only_not_effect_size",
    "significance_word_not_effect_size", "no_numeric_effect",
})

# 대조 전 정규화 대상: 유니코드 대시/공백/따옴표류
DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
QUOTES = dict.fromkeys(map(ord, "\u2018\u2019\u201b\u2032"), "'")
DQUOTES = dict.fromkeys(map(ord, "\u201c\u201d\u201f\u2033"), '"')


# C3: effect 슬롯이 효과 크기가 아닌 것을 담는 흔한 형태.
# normalize() 통과 후(소문자·공백 정규화) 문자열에 적용한다.
P_VALUE_ONLY = re.compile(r"^\(?\s*p\s*[-=<>≤≥]{1,2}\s*\.?\d[\d.eE+-]*\s*\)?$")
CI_ONLY = re.compile(r"^\(?\s*\d{2}\s*%?\s*ci\b")
SIGNIF_WORD_ONLY = re.compile(
    r"^(statistically |clinically |highly |not |no )*(non-?)?significant(ly)?"
    r"( (difference|improvement|reduction|increase|decrease|effect|change"
    r"|reduced|increased|decreased|improved|lower|higher|greater))*\.?$"
)
HAS_DIGIT = re.compile(r"\d")


def effect_shape_reason(value_norm: str) -> str | None:
    """effect 슬롯 값이 효과 크기가 아니면 사유를, 맞으면 None을 반환한다.

    효과 크기 = 변화율 / 변화량(단위 포함) / 군간 차이 / 위험도 비.
    p값은 "우연이 아니다"라는 판정이지 크기가 아니므로 카드에 쓸 수 없다.
    """
    if SIGNIF_WORD_ONLY.match(value_norm):
        return "significance_word_not_effect_size"
    if P_VALUE_ONLY.match(value_norm):
        return "p_value_not_effect_size"
    if CI_ONLY.match(value_norm):
        return "ci_only_not_effect_size"
    if not HAS_DIGIT.search(value_norm):
        return "no_numeric_effect"
    return None


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

    out = {
        "slot": slot_name,
        "value": value,
        "unit": slot.get("unit"),
        "quote": quote,
        "verified": True,
    }
    if slot_name == "effect":
        out["unit_type"] = slot.get("unit_type")
        out["comparator"] = slot.get("comparator")
        out["significance"] = slot.get("significance")
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

    meta = {}
    if args.meta:
        with open(args.meta, encoding="utf-8") as f:
            meta = json.load(f)

    results = []
    rejected_count = 0
    fetch_failed = []

    for rec in records:
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
        time.sleep(0.35 if api_key else 0.4)  # E-utilities rate limit

    total = sum(1 for r in results for s in r["slots"].values())
    passed = sum(1 for r in results for s in r["slots"].values() if s["verified"])

    warnings = [
        "이 팩은 근거 수집 결과일 뿐이며 광고법 검토를 거치지 않았다. "
        "성분 설명과 제품 효능 표방의 경계를 확인하기 전에 발행하지 말 것.",
        "verified=false 슬롯의 값은 의도적으로 제거되었다. 복원하지 말 것.",
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

    pack = {
        "schema": "evidence-pack/0.1",
        "topic": meta.get("topic"),
        "tone": meta.get("tone"),
        "tone_reason": meta.get("tone_reason"),
        "evidence_map": meta.get("evidence_map"),
        "papers": results,
        "verification": {
            "slots_total": total,
            "slots_verified": passed,
            "slots_rejected": rejected_count,
            "fetch_failed_pmids": fetch_failed,
        },
        "warnings": warnings,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)

    print(f"검증 완료: {passed}/{total} 통과, {rejected_count} 폐기 → {args.output}")

    if fetch_failed and not results:
        return 2
    return 1 if rejected_count else 0


if __name__ == "__main__":
    sys.exit(main())
