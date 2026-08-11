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
      --claims data/kr_claims.json --ingredient "EPA 및 DHA 함유 유지"

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
import unicodedata

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


def find_ingredient(claims: dict, query: str) -> tuple[list[dict], str]:
    """원료명으로 조회한다.

    정확일치 > 접두일치 > 부분일치 순으로 랭크해, 가장 높은 등급의 결과만
    돌려준다(S7). 부분일치만으로 매칭하면 "철"·"마늘" 같은 짧은 이름이
    아무 질의에나 걸리고, "마그네"처럼 잘린 오타까지 등재로 잡힌다.
    NFC로 정규화해 비교한다 — NFD로 분리된 질의는 육안상 같아도 바이트가
    달라 매칭에 실패한다(S3).
    """
    q = unicodedata.normalize("NFC", query).strip().lower().replace(" ", "")
    exact, prefix, partial = [], [], []
    for e in claims.get("ingredients", []):
        name = unicodedata.normalize("NFC", e.get("ingredient_ko") or "").lower().replace(" ", "")
        if not q or not name:
            continue
        if name == q:
            exact.append(e)
        elif name.startswith(q) or q.startswith(name):
            prefix.append(e)
        elif q in name or name in q:
            partial.append(e)
    if exact:
        return exact, "exact"
    if prefix:
        return prefix, "prefix"
    if partial:
        return partial, "partial"
    return [], "none"


# 고시 인정 문구의 서술어. 이 셋으로 끝나지 않으면 인정 문구가 아닐 수 있다.
#
# 파서가 「기능성 내용」 자리에서 뽑았다는 것이 그것이 문구라는 뜻은 아니다.
# 1-8 나이아신은 이 값이 "니코틴산인 경우"·"니코틴산아미드인 경우"다 —
# 기능성이 아니라 어느 형태에 해당하는지를 가르는 조건문이고, 그대로 표의
# 「인정 기능성 문구」 칸에 옮기면 없는 기능성을 만든 것이 된다.
#
# "…인 경우"를 막는 대신 "무엇이 문구인가"를 닫는다. 막을 것을 열거하면
# 새 표현이 나올 때마다 구멍이 생기고, 그건 이 저장소가 C3에서 이미
# 겪은 실패다. 동봉 데이터 99행 중 97행이 이 셋으로 끝난다
# (도움을 줄 수 있음 87 · 필요 9 · 관여 1).
#
# 고시가 개정돼 새 서술어가 생기면 그 행이 "확인 필요"로 뜬다. 폐기가
# 아니라 사람에게 한 번 보이는 것이므로 그쪽 오탐이 반대보다 싸다.
CLAIM_TAILS = ("도움을 줄 수 있음", "필요", "관여")


def claim_shape(text: str | None) -> str:
    """인정 문구의 꼴인가. null/공란은 별도 경로(S2)가 이미 다룬다."""
    if not text or not text.strip():
        return "empty"
    return "ok" if text.strip().endswith(CLAIM_TAILS) else "unexpected"


UNIT_MODIFIERS = ("α-TE", "RAE", "DFE", "NE", "TE")


def split_modifier(unit: str | None) -> tuple[str | None, str | None]:
    """단위 끝에 붙은 환산 기준 수식어(RAE/DFE/α-TE/NE/TE)를 분리한다.

    RAE·DFE는 단위가 아니라 환산 기준이다 — "μg RAE"와 "μg"은 질량은 같아
    보여도 재는 대상이 다르다(엽산 DFE는 합성 엽산 대비 1.7배). norm_unit이
    뒤 토큰을 버리면 이 차이가 조용히 사라진다(S4).
    """
    if not unit:
        return None, None
    parts = unit.strip().split()
    if len(parts) >= 2 and parts[-1] in UNIT_MODIFIERS:
        return " ".join(parts[:-1]), parts[-1]
    return unit.strip(), None


def is_rate_unit(unit: str | None) -> bool:
    """'mg/kg'(체중당) 같은 비율 표기는 절대 섭취량이 아니다(S10).

    norm_unit은 '/' 뒤를 버려 mg/kg도 mg으로 읽는다. 그래서 무게 환산 이전에
    따로 걸러낸다. '/day', '/일'은 절대량 표기라 제외한다.
    """
    if not unit or "/" not in unit:
        return False
    tail = unit.split("/", 1)[1].strip().lower()
    return not tail.startswith(("day", "일"))


def compare_dose(dose_value: str | None, dose_unit: str | None,
                 intake: dict | None) -> dict:
    """논문 용량과 고시 섭취량을 비교한다. 애매하면 물러선다."""
    out = {"status": "incomparable", "reason": None, "basis": None,
           "range_raw": None, "basis_unconfirmed": False}
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

    if is_rate_unit(dose_unit) or is_rate_unit(intake.get("unit")):
        out["reason"] = (f"체중당(비율) 단위는 절대 섭취량과 비교할 수 없다 "
                          f"(논문 {dose_unit!r} vs 고시 {intake.get('unit')!r})")
        return out

    _, intake_mod = split_modifier(intake.get("unit"))
    if intake_mod:
        # 수식어를 basis로 승격해 "기준 성분 확인" 경고가 뜨게 한다(S4).
        out["basis"] = f"{out['basis']} ({intake_mod})" if out["basis"] else intake_mod
        _, dose_mod = split_modifier(dose_unit)
        if dose_mod != intake_mod:
            out["reason"] = (f"고시 섭취량이 환산 기준 '{intake_mod}'다. 논문 용량이 같은 "
                              f"기준인지 확인 없이는 비교할 수 없다 (논문 단위 {dose_unit!r})")
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

    # 기준 성분(basis)이 붙은 섭취량은 "무엇의 양인가"가 논문과 다를 수 있다.
    # "EPA와 DHA의 합으로서 0.5~2 g"에 EPA 단독 500 mg을 대면 숫자는 범위
    # 안이지만 애초에 같은 것을 재고 있지 않다. RAE/DFE 수식어는 위에서
    # incomparable로 막으면서 이쪽만 within으로 통과시키는 것은 비대칭이었다.
    #
    # 그렇다고 basis만으로 incomparable로 돌리지는 않는다 — basis는 흔해서
    # 그러면 거의 모든 비교가 사라지고 도구가 쓸모없어진다. 비교는 수행하되
    # "확인되지 않았다"는 사실을 status와 나란히 실어 verdict가 집어 든다.
    out["basis_unconfirmed"] = bool(out["basis"])

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

    try:
        return run(args)
    except Exception as e:  # noqa: BLE001 — 계약을 지키는 것이 목적이다
        # 미포착 예외로 죽으면 종료 코드 1이 나가는데 계약은 0/2뿐이고,
        # 더 나쁜 것은 --output이 갱신되지 않아 **직전 실행의 통과 리포트가
        # 디스크에 그대로 남는다**는 점이다. 다음 단계가 그걸 이번 결과로
        # 읽으면 상한 초과 용량이 검증된 것처럼 콘텐츠에 실린다.
        # 오류 경로에서도 반드시 리포트를 덮어쓴다.
        _emit(_error_report(args, f"{type(e).__name__}: {e}"), args.output)
        print(f"실행 중 오류: {type(e).__name__}: {e}", file=sys.stderr)
        print("리포트를 오류 상태로 덮어썼다. 직전 결과를 이번 결과로 읽지 말 것.",
              file=sys.stderr)
        return 2


def _error_report(args, detail: str) -> dict:
    return {
        "schema": "kr-claims-report/0.2",
        "verdict": "block",
        "verdict_reasons": [f"스크립트가 정상 종료하지 못했다 ({detail})"],
        "ingredient_query": args.ingredient,
        "found": None,
        "matches": [], "dose_checks": [],
        "warnings": ["이 리포트는 실행 실패로 생성된 오류 리포트다. "
                     "대조 결과가 아니므로 어떤 판단의 근거로도 쓰지 말 것."],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def run(args) -> int:
    try:
        pack = json.load(open(args.pack, encoding="utf-8")) if args.pack else {}
    except (OSError, json.JSONDecodeError) as e:
        _emit(_error_report(args, f"팩을 읽을 수 없다: {e}"), args.output)
        print(f"팩을 읽을 수 없다: {args.pack} ({e})", file=sys.stderr)
        return 2
    if not isinstance(pack, dict):
        _emit(_error_report(args, f"팩이 객체가 아니다 (type={type(pack).__name__})"),
              args.output)
        print(f"팩이 객체가 아니다 (type={type(pack).__name__}).", file=sys.stderr)
        return 2
    try:
        claims = json.load(open(args.claims, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _emit(_error_report(args, f"claims 파일을 읽을 수 없다: {e}"), args.output)
        print(f"claims 파일을 읽을 수 없다: {e}", file=sys.stderr)
        return 2

    # S3: 데이터 로드 시점에도 NFC로 정규화한다. find_ingredient가 질의를
    # 정규화해도, 저장된 이름 자체가 NFD로 얼어붙어 있으면 다른 호출자가
    # 그대로 오탐할 수 있다.
    for _e in claims.get("ingredients", []):
        if _e.get("ingredient_ko"):
            _e["ingredient_ko"] = unicodedata.normalize("NFC", _e["ingredient_ko"])

    query = args.ingredient
    if not query:
        topic = pack.get("topic") or ""
        query = re.split(r"[×xX]", topic)[0].strip()
    if not query:
        print("원료명을 정할 수 없다. --ingredient로 지정할 것.", file=sys.stderr)
        return 2

    hits, match_kind = find_ingredient(claims, query)
    src = claims.get("source", {})
    warnings = [
        f"고시 기준: {src.get('notice')} (시행 {src.get('effective_date')}), "
        f"추출 확인일 {src.get('checked_at')}. 개정되면 이 결과는 무효다.",
        "이 리포트는 인정 문구를 꺼내 놓을 뿐 카피 표현의 적법성을 판정하지 않는다. "
        "표방 문구는 고시 문구와 사람이 직접 대조할 것.",
    ]

    # S10: source가 통째로 없으면 어느 고시 기준인지 알 길이 없다.
    if not claims.get("source"):
        warnings.insert(0,
            "이 claims 파일에 출처(source) 정보가 없다 — 어느 고시를 기준으로 "
            "한 데이터인지 알 수 없다. 결과를 신뢰하지 말고 재빌드된 파일로 교체할 것.")

    # 플러그인에 동봉된 데이터는 배포 시점에 얼어붙는다. 고시는 개정되므로
    # 오래된 스냅샷을 현행으로 읽는 것이 이 도구의 조용한 실패 경로다.
    stale = stale_days(src.get("checked_at"))
    if stale is not None and stale > STALE_AFTER_DAYS:
        warnings.insert(0,
            f"고시 데이터가 {stale}일 전 추출본이다 (기준 {STALE_AFTER_DAYS}일). "
            "그 사이 개정됐을 수 있으므로 최신 고시를 확인하고 재빌드할 것 — "
            "특히 일일섭취량은 개정에서 자주 바뀐다.")

    # S1b: 동명 원료면 부분일치가 어느 쪽인지 못 가른다. 코드로 구분하라고 남긴다.
    dup_map = claims.get("coverage", {}).get("duplicate_ingredient_names") or {}
    dup_hit_names = sorted({e.get("ingredient_ko") for e in hits
                            if e.get("ingredient_ko") in dup_map})
    for name in dup_hit_names:
        codes = dup_map[name]
        warnings.append(f"'{name}' 이름으로 고시에 {len(codes)}개 코드가 등재돼 있다 "
                        f"({', '.join(codes)}) — 동명이인일 수 있으니 코드로 구분해 확인할 것.")

    # S7: 정확일치가 아니면 질의어와 등재명이 다를 수 있다.
    # 접두일치도 마찬가지다 — "홍삼농축액"으로 물으면 "홍삼"이 접두로 걸려
    # 다른 원료의 인정 문구·섭취량이 조용히 돌아온다. 부분일치만 경고하고
    # 접두일치를 통과시키면, 실제로 더 흔한 쪽이 무경고로 새 나간다.
    if hits and match_kind in ("prefix", "partial"):
        kind_ko = {"prefix": "접두일치", "partial": "부분일치"}[match_kind]
        found_names = ", ".join(sorted({e.get("ingredient_ko") or "" for e in hits}))
        warnings.append(f"질의어 '{query}'가 등재명과 정확히 일치하지 않는다({kind_ko}). "
                        f"다른 원료의 기준을 보고 있을 수 있으니 등재명을 직접 "
                        f"확인할 것: {found_names}")

    if not hits:
        warnings.insert(0,
            f"'{query}'을(를) 고시에서 찾지 못했다. **인정되지 않았다는 뜻이 아니다** — "
            "이 고시는 고시형 원료만 담으므로 개별인정형 목록을 따로 확인해야 한다.")
        report = {"schema": "kr-claims-report/0.2",
                  "verdict": "block",
                  "verdict_reasons": [f"'{query}'이(가) 고시에 없어 대조하지 못했다 "
                                      "— 미인정이 아니라 개별인정형 확인이 남았다는 뜻이다"],
                  "ingredient_query": query,
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
                         "claim_shape": claim_shape(r.get("functional_claim")),
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
                        "functional_claims_raw": e.get("functional_claims_raw"),
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
    # S2: functional_claim이 null인 행을 조용히 넘기면, 그걸 표로 옮기는
    # 에이전트가 빈 칸을 보고 문구를 지어낼 확률이 높다 — 이 도구가 막으려는
    # 바로 그 실패다.
    if any(r.get("functional_claim") is None for m in matches for r in m.get("rows", [])):
        warnings.append("이 원료는 고시 인정 문구를 추출하지 못한 행이 있다 — "
                        "원문을 직접 확인해야 한다. 비어 있다고 문구를 생성하지 말 것.")
    # 문구 칸이 채워져 있어도 그것이 인정 문구가 아닐 수 있다 (1-8 나이아신의
    # "니코틴산인 경우"). 빈 칸보다 위험하다 — 빈 칸은 사람이 채워야 한다는
    # 신호라도 되지만, 이건 그냥 옮겨 적힌다.
    odd_claims = [f'{m.get("ingredient_ko")}({m.get("code")}): {r.get("functional_claim")}'
                  for m in matches for r in m.get("rows", [])
                  if r.get("claim_shape") == "unexpected"]
    if odd_claims:
        warnings.append(
            "인정 문구의 꼴이 아닌 행이 있다 — 고시가 문구 자리에 적용 조건을 적어 둔 "
            "경우다(예: 1-8 나이아신 '니코틴산인 경우'). 기능성이 아니므로 표의 "
            f"「인정 기능성 문구」 칸에 그대로 옮기지 말고 '확인 필요'로 둘 것: "
            f"{'; '.join(odd_claims)}")
    # S10: raw에 시행 전 개정 주석("고시 제…호", "시행일(…)")이 섞여 있으면
    # parsed:false라 비교는 막혀도, raw를 그대로 보여주는 리포트는 시행 전
    # 개정치를 현행처럼 읽힐 수 있다.
    if any("시행일" in (r.get("daily_intake_raw") or "") for m in matches for r in m.get("rows", [])):
        warnings.append("고시 원문에 개정 예정(시행 전) 문구가 섞여 있는 행이 있다. "
                        "raw 텍스트의 날짜를 확인해 이미 시행된 내용인지 사람이 판단할 것.")

    # ── verdict — 산문 경고 대신 읽을 필드 ──
    #
    # 조회 모드·접두일치·basis 미확인·below가 전부 exit 0으로 수렴했다.
    # 그 셋을 가르는 정보는 warnings 배열의 문장으로만 있었고, 그걸 읽는
    # 쪽은 LLM이다. 문장은 흘린다. 분기에 쓸 값을 따로 준다.
    #
    # **verdict는 "발행해도 되는가"가 아니다.** 카피 표현이 인정 범위 안인지는
    # 의미 판정이라 이 스크립트가 하지 않는다 (불변 원칙 2). pass는 "숫자가
    # 고시 범위 안이고 이름이 정확히 맞았다"까지다.
    below = [c for c in checks if c["status"] == "below"]
    verdict_reasons = []
    if above:
        verdict_reasons.append(f"논문 용량이 인정 상한을 넘는 조합이 {len(above)}건 있다")
    if args.pack and incomparable:
        verdict_reasons.append(f"비교 불가 {len(incomparable)}건 — '문제없음'이 아니다")
    if args.pack and not doses:
        verdict_reasons.append("팩에 검증된 용량 슬롯이 없어 비교하지 못했다")
    # 여기까지가 기존 종료 코드 2의 조건이다. verdict가 종료 코드의 상위
    # 집합이 되지 않도록 경계를 여기서 끊는다 — 아래 사유들은 예전에도
    # 0이었고 지금도 0이다. 바뀐 것은 그 0이 무엇을 뜻하는지 읽을 수 있게
    # 됐다는 점뿐이다.
    blocked = bool(verdict_reasons)

    if not args.pack:
        verdict_reasons.append("조회 모드다 — 용량 대조를 하지 않았다")
    if below:
        verdict_reasons.append(f"인정 최소치 미만 {len(below)}건 "
                               "(다른 기능성 기준이면 정상이다)")
    if match_kind in ("prefix", "partial"):
        verdict_reasons.append(f"질의어가 등재명과 정확히 일치하지 않는다({match_kind})")
    if any(c.get("basis_unconfirmed") for c in checks):
        verdict_reasons.append("기준 성분(basis)이 논문 용량과 같은지 확인되지 않았다")
    if any(r.get("functional_claim") is None for m in matches for r in m.get("rows", [])):
        verdict_reasons.append("인정 문구를 추출하지 못한 행이 있다")
    if odd_claims:
        verdict_reasons.append(f"인정 문구의 꼴이 아닌 행이 {len(odd_claims)}건 있다 "
                               "(적용 조건이 문구 자리에 들어간 경우)")
    if dup_hit_names:
        verdict_reasons.append("같은 이름으로 여러 코드가 등재돼 있다")

    verdict = "block" if blocked else ("review" if verdict_reasons else "pass")

    report = {"schema": "kr-claims-report/0.2",
              "verdict": verdict,
              "verdict_reasons": verdict_reasons,
              "ingredient_query": query,
              "found": True, "matches": matches, "dose_checks": checks,
              "warnings": warnings, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    _emit(report, args.output)

    # 사람이 읽는 요약은 전부 stderr다. --output 없이 돌리면 stdout은
    # 리포트 JSON 하나뿐이어야 `> report.json`이 그대로 성립한다.
    print(f"'{query}' → 고시 등재 {len(hits)}건 / 인정 문구 행 "
          f"{sum(len(m['rows']) for m in matches)}개 / 용량 비교 {len(checks)}건",
          file=sys.stderr)
    if above:
        print(f"  상한 초과 {len(above)}건", file=sys.stderr)
    if incomparable:
        print(f"  비교 불가 {len(incomparable)}건", file=sys.stderr)
    if below:
        print(f"  인정 최소치 미만 {len(below)}건 (다른 기능성 기준이면 정상)",
              file=sys.stderr)
    print(f"verdict={verdict}"
          + (f" — {'; '.join(verdict_reasons)}" if verdict_reasons else ""),
          file=sys.stderr)

    # 종료 코드는 verdict에서 파생시킨다. 같은 조건을 두 곳에 적어 두면
    # 한쪽만 고쳐질 때 조용히 갈라진다. blocked의 정의가 곧 기존 계약이라
    # 종료 코드 자체는 이 변경으로 달라지지 않는다 — 조회 모드는 0,
    # 상한 초과·비교 불가·용량 슬롯 없음은 2다.
    return 2 if verdict == "block" else 0


def _emit(report: dict, path: str | None) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()


if __name__ == "__main__":
    sys.exit(main())
