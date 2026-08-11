#!/usr/bin/env python3
"""verify_slot 계약 검사. 네트워크 없이 돈다. `python3 scripts/test_verify_numbers.py`"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from verify_numbers import verify_slot, normalize

ABS = normalize(
    "Triglycerides decreased by -0.34 mmol/L compared with placebo (P = 0.02). "
    "Serum levels were significantly reduced (95% CI 0.12 to 0.48) in the treatment group. "
    "Subjects received 500 mg magnesium daily for 8 weeks."
)

def slot(v, q, **kw): return dict(value=v, quote=q, **kw)

# 통과: 효과 크기
ok = verify_slot("effect", slot("-0.34 mmol/L",
    "Triglycerides decreased by -0.34 mmol/L compared with placebo (P = 0.02).",
    unit="mmol/L", unit_type="absolute", comparator="placebo", significance="P = 0.02"), ABS)
assert ok["verified"] and ok["comparator"] == "placebo", ok

# C3: 원문에 실재해도 효과 크기가 아니면 폐기 — 이슈 4의 38830807 케이스
for value, quote, why in [
    ("P = 0.02", "Triglycerides decreased by -0.34 mmol/L compared with placebo (P = 0.02).",
     "p_value_not_effect_size"),
    ("95% CI 0.12 to 0.48",
     "Serum levels were significantly reduced (95% CI 0.12 to 0.48) in the treatment group.",
     "ci_only_not_effect_size"),
    ("significantly reduced",
     "Serum levels were significantly reduced (95% CI 0.12 to 0.48) in the treatment group.",
     "significance_word_not_effect_size"),
]:
    r = verify_slot("effect", slot(value, quote), ABS)
    assert not r["verified"] and r["reason"] == why, (value, r)
    assert r["value"] is None, r

# 같은 문자열이라도 effect가 아닌 슬롯에는 C3를 적용하지 않는다
r = verify_slot("dose", slot("500 mg", "Subjects received 500 mg magnesium daily for 8 weeks."), ABS)
assert r["verified"], r

# C1/C2는 그대로
assert verify_slot("effect", slot("-0.34 mmol/L", "지어낸 문장이다."), ABS)["reason"] == "quote_not_in_abstract"
assert verify_slot("effect", slot("-0.99 mmol/L",
    "Triglycerides decreased by -0.34 mmol/L compared with placebo (P = 0.02)."),
    ABS)["reason"] == "value_not_in_quote"
assert verify_slot("form", None, ABS)["reason"] == "not_extracted"

print("ok")
