#!/usr/bin/env python3
"""check_kr_claims.py — evidence-pack ↔ 식약처 고시 대조 (P8 일부)

논문 근거 팩을 국내 고시와 대조해 **사람이 판단할 재료를 꺼내 놓는다.**
판정하지 않는다.

이 스크립트가 하는 것:
  1. 해당 원료의 **인정 기능성 문구를 원문 그대로** 꺼내 온다
  2. 팩의 용량(dose 슬롯)을 인정 섭취량 범위와 비교한다
  3. 비교할 수 없으면 "비교 불가"라고 말한다 — 넘겨짚지 않는다

이 스크립트가 하지 않는 것:
  - **카피 표현이 인정 범위 안인지 판정.** 의미 판정이라 기계로 못 한다.
    문구를 나란히 놓아 줄 뿐이고, 대조는 사람이 한다.
  - **미등재를 "인정되지 않음"으로 단정.** 이 고시는 고시형만 담는다.
    개별인정형은 별도 문서이므로 없으면 "확인 필요"다.

용량 비교가 어려운 이유:
  고시는 기준 성분을 따로 정한다. EPA·DHA는 "EPA와 DHA의 합으로서 0.5~2 g"이라
  논문의 EPA 단독 용량과 직접 비교할 수 없다. 그래서 basis가 있으면 비교
  결과에 항상 basis를 함께 싣고, 기준이 같은지 확인하라고 요구한다.

의존성: 표준 라이브러리만 사용.

사용:
  python3 check_kr_claims.py --pack evidence-pack.json \
      --claims data/kr_claims.json --ingredient 오메가3

종료 코드:
  0  등재 확인 + 용량 비교 성공 + **상한 초과 없음** (문구 대조는 여전히 사람 몫이다)
  2  발행 전 확인이 필요하다 — 미등재 / 상한 초과 / 비교 불가

  `below`(인정 최소치 미만)는 2를 내지 않는다. 원료 하나에 기능성이 여러 개면
  다른 기능성 기준에서는 미달로 나오는 게 정상이라, 2로 올리면 늘 2가 된다.
  대신 리포트와 stdout에 건수로 남는다.
"""

import argparse
import json
import re
import sys
import time

# μg 기준 환산. 고시와 논문의 단위가 다른 건 정상이므로 여기서만 변환한다.
# (miner의 단위 변환 금지는 "원문에서 뽑을 때" 규칙이다. 이미 검증된 두
#  숫자를 스크립트가 결정론적으로 환산하는 것은 다른 얘기다.)
TO_MICROGRAM = {
    "g": 1_000_000.0, "mg": 1_000.0, "㎎": 1_000.0,
    "ug": 1.0, "mcg": 1.0, "μg": 1.0, "µg": 1.0, "㎍": 1.0,
}
# 환산 불가 단위. 있는 그대로 같을 때만 비교한다.
OPAQUE_UNITS = {"cfu", "iu", "billion", "억"}

# 동봉 데이터가 이만큼 지나면 경고한다. 고시 개정 주기가 정해져 있지 않으므로
# 임계값은 "반년쯤 지났으면 한 번 확인해 보라"는 뜻이지 유효기간이 아니다.
STALE_AFTER_DAYS = 180


def stale_days(checked_at: str | None) -> int | None:
    """추출 확인일로부터 며칠 지났는가. 날짜를 못 읽으면 None."""
    if not checked_at:
        return None
    try:
        t = time.strptime(checked_at[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return int((time.time() - time.mktime(t)) // 86400)


def norm_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.strip().lower()
    u = u.replace("µ", "μ")  # micro sign → greek mu
    u = re.split(r"[/\s]", u)[0]       # "mg/day", "μg RAE" → 앞부분
    return u or None


def to_microgram(value: float, unit: str | None) -> float | None:
    u = norm_unit(unit)
    if u is None:
        return None
    factor = TO_MICROGRAM.get(u) or TO_MICROGRAM.get(u.replace("μ", "u"))
    return value * factor if factor else None


def find_ingredient(claims: dict, query: str) -> list[dict]:
    """원료명으로 조회. 부분 일치를 허용하되 어느 이름에 걸렸는지 남긴다."""
    q = query.strip().lower().replace(" ", "")
    hits = []
    for e in claims.get("ingredients", []):
        name = (e.get("ingredient_ko") or "").lower().replace(" ", "")
        if q and (q in name or name in q):
            hits.append(e)
    return hits


def compare_dose(dose_value: str | None, dose_unit: str | None,
                 intake: dict | None) -> dict:
    """논문 용량과 고시 섭취량을 비교한다. 애매하면 물러선다."""
    out = {"status": "incomparable", "reason": None, "basis": None,
           "range_raw": None}
    if not intake:
        out["reason"] = "고시에 일일섭취량이 없다"
        return out
    out["basis"] = intake.get("basis")
    out["range_raw"] = intake.get("raw")

    if not intake.get("parsed"):
        out["reason"] = "고시 섭취량 표기를 수치로 열지 못했다 (raw를 직접 확인할 것)"
        return out
    if not dose_value:
        out["reason"] = "팩에 검증된 용량 슬롯이 없다"
        return out

    try:
        val = float(str(dose_value).replace(",", ""))
    except ValueError:
        out["reason"] = f"논문 용량을 수치로 열지 못했다: {dose_value!r}"
        return out

    lo_u, hi_u = intake.get("min"), intake.get("max")
    d_norm, lo_norm, hi_norm = (to_microgram(val, dose_unit),
                                to_microgram(lo_u, intake.get("unit")) if lo_u is not None else None,
                                to_microgram(hi_u, intake.get("unit")) if hi_u is not None else None)

    if d_norm is None or (lo_norm is None and hi_norm is None):
        du, cu = norm_unit(dose_unit), norm_unit(intake.get("unit"))
        if du and cu and du == cu and du in OPAQUE_UNITS:
            d_norm, lo_norm, hi_norm = val, lo_u, hi_u  # 같은 불투명 단위끼리는 직접 비교
        else:
            out["reason"] = f"단위를 환산할 수 없다 (논문 {dose_unit!r} vs 고시 {intake.get('unit')!r})"
            return out

    if hi_norm is not None and d_norm > hi_norm:
        out["status"] = "above"
    elif lo_norm is not None and d_norm < lo_norm:
        out["status"] = "below"
    else:
        out["status"] = "within"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    # --pack 없이도 돈다. 팩이 없으면 조회 모드 — 인정 문구·섭취량·주의사항만
    # 꺼내 온다. 용량 대조는 비교할 논문 용량이 있어야 성립하기 때문이다.
    ap.add_argument("--pack", help="evidence-pack.json (없으면 조회 모드)")
    ap.add_argument("--claims", required=True, help="kr_claims.json")
    ap.add_argument("--ingredient", help="원료 한글명 (없으면 팩의 topic에서 뽑는다)")
    ap.add_argument("--output", help="리포트 JSON 경로 (선택)")
    args = ap.parse_args()

    pack = json.load(open(args.pack, encoding="utf-8")) if args.pack else {}
    claims = json.load(open(args.claims, encoding="utf-8"))

    query = args.ingredient
    if not query:
        topic = pack.get("topic") or ""
        query = re.split(r"[×xX]", topic)[0].strip()
    if not query:
        print("원료명을 정할 수 없다. --ingredient로 지정할 것.", file=sys.stderr)
        return 2

    hits = find_ingredient(claims, query)
    src = claims.get("source", {})
    warnings = [
        f"고시 기준: {src.get('notice')} (시행 {src.get('effective_date')}), "
        f"추출 확인일 {src.get('checked_at')}. 개정되면 이 결과는 무효다.",
        "이 리포트는 인정 문구를 꺼내 놓을 뿐 카피 표현의 적법성을 판정하지 않는다. "
        "표방 문구는 고시 문구와 사람이 직접 대조할 것.",
    ]

    # 플러그인에 동봉된 데이터는 배포 시점에 얼어붙는다. 고시는 개정되므로
    # 오래된 스냅샷을 현행으로 읽는 것이 이 도구의 조용한 실패 경로다.
    stale = stale_days(src.get("checked_at"))
    if stale is not None and stale > STALE_AFTER_DAYS:
        warnings.insert(0,
            f"고시 데이터가 {stale}일 전 추출본이다 (기준 {STALE_AFTER_DAYS}일). "
            "그 사이 개정됐을 수 있으므로 최신 고시를 확인하고 재빌드할 것 — "
            "특히 일일섭취량은 개정에서 자주 바뀐다.")

    if not hits:
        warnings.insert(0,
            f"'{query}'을(를) 고시에서 찾지 못했다. **인정되지 않았다는 뜻이 아니다** — "
            "이 고시는 고시형 원료만 담으므로 개별인정형 목록을 따로 확인해야 한다.")
        report = {"schema": "kr-claims-report/0.1", "ingredient_query": query,
                  "found": False, "matches": [], "dose_checks": [],
                  "warnings": warnings, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _emit(report, args.output)
        print(f"'{query}': 고시 미등재 → 개별인정형 확인 필요", file=sys.stderr)
        return 2

    # 팩에서 검증된 용량 슬롯만 모은다. verified=false는 값이 이미 제거돼 있다.
    doses = []
    for p in pack.get("papers", []):
        slot = (p.get("slots") or {}).get("dose") or {}
        if slot.get("verified"):
            doses.append({"pmid": p.get("pmid"), "value": slot.get("value"),
                          "unit": slot.get("unit")})

    matches, checks = [], []
    for e in hits:
        rows = []
        for r in e.get("rows", []):
            rows.append({"functional_claim": r.get("functional_claim"),
                         "daily_intake_raw": (r.get("daily_intake") or {}).get("raw")})
            for d in doses:
                c = compare_dose(d["value"], d["unit"], r.get("daily_intake"))
                checks.append({"pmid": d["pmid"],
                               "paper_dose": f'{d["value"]} {d["unit"] or ""}'.strip(),
                               "ingredient": e.get("ingredient_ko"),
                               "functional_claim": r.get("functional_claim"),
                               **c})
        matches.append({"code": e.get("code"), "ingredient_ko": e.get("ingredient_ko"),
                        "category": e.get("category"),
                        "recognition_type": e.get("recognition_type"),
                        "rows": rows, "cautions": e.get("cautions", [])})

    above = [c for c in checks if c["status"] == "above"]
    incomparable = [c for c in checks if c["status"] == "incomparable"]
    if above:
        warnings.insert(0,
            f"논문 용량이 국내 인정 상한을 넘는 조합이 {len(above)}건 있다. "
            "그 수치를 국내 콘텐츠에 그대로 인용하지 말 것.")
    if any(c.get("basis") for c in checks):
        warnings.append(
            "인정 섭취량에 기준 성분(basis)이 붙어 있다. 논문 용량이 같은 기준인지 "
            "확인할 것 — 단일 성분 용량을 '합계' 기준 범위와 비교하면 틀린다.")
    if not args.pack:
        warnings.append("조회 모드다 (팩 없음). 인정 문구와 섭취량만 꺼냈고 "
                        "용량 대조는 하지 않았다.")
    elif not doses:
        warnings.append("팩에 검증된 용량 슬롯이 없어 용량 비교를 수행하지 못했다.")
    if any(e.get("cautions") for e in hits):
        warnings.append("고시의 섭취 시 주의사항은 안전성 슬라이드의 1차 근거다. "
                        "초록에 부작용 언급이 없다는 것과 혼동하지 말 것.")

    report = {"schema": "kr-claims-report/0.1", "ingredient_query": query,
              "found": True, "matches": matches, "dose_checks": checks,
              "warnings": warnings, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    _emit(report, args.output)

    print(f"'{query}' → 고시 등재 {len(hits)}건 / 인정 문구 행 "
          f"{sum(len(m['rows']) for m in matches)}개 / 용량 비교 {len(checks)}건")
    below = [c for c in checks if c["status"] == "below"]
    if above:
        print(f"  상한 초과 {len(above)}건", file=sys.stderr)
    if incomparable:
        print(f"  비교 불가 {len(incomparable)}건", file=sys.stderr)
    if below:
        print(f"  인정 최소치 미만 {len(below)}건 (다른 기능성 기준이면 정상)")
    if not args.pack:
        return 0  # 조회 모드는 대조를 하지 않았으므로 판정할 것이 없다
    return 2 if (above or incomparable or not doses) else 0


def _emit(report: dict, path: str | None) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()


if __name__ == "__main__":
    sys.exit(main())
