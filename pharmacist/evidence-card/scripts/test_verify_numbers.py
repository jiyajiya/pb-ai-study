#!/usr/bin/env python3
"""verify_slot 계약 검사. 네트워크 없이 돈다. `python3 scripts/test_verify_numbers.py`

케이스는 구현이 아니라 **문서에서** 가져온다.
abstract-miner.md "effect에 넣으면 안 되는 것" 목록과
tests/fixtures.md의 릴리스 불가 조건이 여기 그대로 들어와야 한다.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from verify_numbers import verify_slot, normalize

ABS = normalize(
    "Triglycerides decreased by -0.34 mmol/L compared with placebo (P = 0.02). "
    "Serum levels were significantly reduced (95% CI 0.12 to 0.48) in the treatment group. "
    "The reduction was significant at p<0.001 across all sites; p value < 0.05 overall, "
    "and P-value = 0.03 for the subgroup. "
    "Sleep onset latency decreased by 17.36 min. "
    "Subjects received 500 mg magnesium daily for 8 weeks. "
    "Secondary outcomes: SBP -5.2 mmHg and sleep -1.2 points versus baseline. "
    "The effect held with a confidence interval 0.12 to 0.48 in the pooled analysis."
)

def slot(v, q, **kw): return dict(value=v, quote=q, **kw)

EFFECT_Q = "Triglycerides decreased by -0.34 mmol/L compared with placebo (P = 0.02)."
CI_Q = "Serum levels were significantly reduced (95% CI 0.12 to 0.48) in the treatment group."
P_Q = ("The reduction was significant at p<0.001 across all sites; p value < 0.05 overall, "
       "and P-value = 0.03 for the subgroup.")
MIN_Q = "Sleep onset latency decreased by 17.36 min."
SBP_Q = "Secondary outcomes: SBP -5.2 mmHg and sleep -1.2 points versus baseline."
CI_Q2 = "The effect held with a confidence interval 0.12 to 0.48 in the pooled analysis."

# 통과: 효과 크기
ok = verify_slot("effect", slot("-0.34", EFFECT_Q, unit="mmol/L",
    unit_type="absolute", comparator="placebo", significance="P = 0.02"), ABS)
assert ok["verified"] and ok["comparator"] == "placebo", ok
assert ok["unit_type"] == "absolute" and ok["significance"] == "P = 0.02", ok

# unit_type은 에이전트 판정이 아니라 값에서 파생된다 — 틀리게 넣어도 교정된다
r = verify_slot("effect", slot("-0.34", EFFECT_Q, unit="mmol/L", unit_type="percent"), ABS)
assert r["unit_type"] == "absolute", r

# ── C3: 효과 크기가 아닌 값 ──
# 앞 3개는 abstract-miner.md:51-53이 금지 예시로 명시한 문자열이다.
for value, quote, why in [
    ("P = 0.02", EFFECT_Q, "p_value_not_effect_size"),
    ("p<0.001", P_Q, "p_value_not_effect_size"),
    ("significant at p<0.001", P_Q, "p_value_not_effect_size"),   # 접두어가 붙어도
    ("p value < 0.05", P_Q, "p_value_not_effect_size"),
    ("P-value = 0.03", P_Q, "p_value_not_effect_size"),
    ("95% CI 0.12 to 0.48", CI_Q, "ci_only_not_effect_size"),
    ("(95% CI 0.12 to 0.48)", CI_Q, "ci_only_not_effect_size"),   # 괄호가 붙어도
    ("confidence interval 0.12 to 0.48", CI_Q2, "ci_only_not_effect_size"),  # 95% 없이도
    ("significantly reduced", CI_Q, "no_numeric_effect"),
]:
    r = verify_slot("effect", slot(value, quote), ABS)
    assert not r["verified"] and r["reason"] == why, (value, r)
    assert r["value"] is None, r

# 점추정치가 있으면 p값·CI가 붙어 있어도 통과한다 (차감식이 과잉 폐기하지 않는지)
r = verify_slot("effect", slot("-0.34 mmol/L compared with placebo (P = 0.02)", EFFECT_Q), ABS)
assert r["verified"], r

# 차감식은 p로 끝나는 단어 뒤의 음수를 p값으로 오인하기 쉽다.
# P_TOKEN에서 (?<![a-z])를 빼면 아래 둘이 p_value_not_effect_size로 폐기된다.
# SBP는 마그네슘×혈압, sleep은 F2(마그네슘×수면)의 주 지표다 — 조용히 죽으면 톤이 T3로 기운다.
for v_ in ["SBP -5.2 mmHg", "sleep -1.2 points"]:
    r = verify_slot("effect", slot(v_, SBP_Q), ABS)
    assert r["verified"], (v_, r)

# 점추정치가 앞에 있으면 CI를 걷어내도 숫자가 남는다 — 살아야 한다
r = verify_slot("effect", slot("confidence interval 0.12 to 0.48 in the pooled analysis",
                               CI_Q2), ABS)
assert not r["verified"] and r["reason"] == "ci_only_not_effect_size", r

# ── C4: unit ⊂ quote ──
# 초록은 min인데 unit을 hours로 바꿔 적으면 카드에 "17.36시간"이 찍힌다.
# value만 검사하던 시절 이것이 verified=True로 통과했다.
r = verify_slot("effect", slot("17.36", MIN_Q, unit="hours"), ABS)
assert not r["verified"] and r["reason"] == "unit_not_in_quote", r
assert r["value"] is None, r
assert verify_slot("effect", slot("17.36", MIN_Q, unit="min"), ABS)["verified"]

# unit 없는 슬롯은 그대로 통과 (검사는 unit이 있을 때만)
assert verify_slot("population", slot("46 elderly", "A trial in 46 elderly subjects."),
                   normalize("A trial in 46 elderly subjects."))["verified"]

# ── comparator: 열거값 밖은 null. "위약 대비"를 지어내지 못하게 한다 ──
r = verify_slot("effect", slot("-0.34", EFFECT_Q, comparator="vs. usual care"), ABS)
assert r["verified"] and r["comparator"] is None, r

# ── significance: quote 밖이면 슬롯을 죽이지 않고 그 필드만 버린다 ──
r = verify_slot("effect", slot("17.36", MIN_Q, unit="min", significance="P = 0.02"), ABS)
assert r["verified"] and r["significance"] is None, r
assert r["dropped_significance"] == "P = 0.02", r

# ── C3는 effect 슬롯에만 적용된다 ──
r = verify_slot("dose", slot("500 mg", "Subjects received 500 mg magnesium daily for 8 weeks.",
                             unit="mg"), ABS)
assert r["verified"], r

# ── C1/C2는 그대로 ──
assert verify_slot("effect", slot("-0.34 mmol/L", "지어낸 문장이다."), ABS)["reason"] == "quote_not_in_abstract"
assert verify_slot("effect", slot("-0.99 mmol/L", EFFECT_Q), ABS)["reason"] == "value_not_in_quote"
assert verify_slot("form", None, ABS)["reason"] == "not_extracted"

print("ok")
