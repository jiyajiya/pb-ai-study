#!/usr/bin/env python3
"""kr-claims 계약 검사. 네트워크 없이 돈다. `python3 scripts/test_kr_claims.py`

케이스는 고시전문에서 **실제로 관찰한 표기**에서 가져온다.
파서가 조용히 틀리는 지점(천 단위 쉼표, 괄호 환산값, 복합 조건)이
전부 여기 들어 있어야 한다.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_kr_claims import extract, parse_intake, split_blocks
from check_kr_claims import compare_dose, norm_unit, to_microgram

# ── 섭취량 표기 파싱 ──
# 전부 고시전문에 실재하는 문자열이다.
ok = [
    ("EPA와 DHA의 합으로서 0.5 ~ 2 g", "EPA와 DHA의 합", 0.5, 2.0, "g", "range"),
    ("공액리놀레산으로 1.4 ~ 4.2 g", "공액리놀레산", 1.4, 4.2, "g", "range"),
    ("94.5 ~ 250 mg", None, 94.5, 250.0, "mg", "range"),
    # 천 단위 쉼표. `,\s*\d`를 복합조건 신호로 쓰면 여기서 전부 죽는다
    ("21 ~ 1,000 μg", None, 21.0, 1000.0, "μg", "range"),
    ("30 ~ 1,000 mg", None, 30.0, 1000.0, "mg", "range"),
    # 괄호 환산값은 걷어내고 본다
    ("210 ~ 1,000 μg RAE (699.93 ~ 3,333 IU)", None, 210.0, 1000.0, "μg RAE", "range"),
    ("100,000,000(1억) ~ 10,000,000,000(100억) CFU", None, 1e8, 1e10, "CFU", "range"),
    # 범위가 아닌 세 형태
    ("식이섬유로서 5 g 이상", "식이섬유", 5.0, None, "g", "min"),
    ("포스파티딜세린으로서 300 mg", "포스파티딜세린", 300.0, 300.0, "mg", "exact"),
    # basis에 '또는'이 들어가도 숫자가 하나면 열린다
    ("글루코사민염산염 또는 황산염으로서 1.5 g", "글루코사민염산염 또는 황산염", 1.5, 1.5, "g", "exact"),
]
for raw, basis, lo, hi, unit, bound in ok:
    r = parse_intake(raw)
    assert r["parsed"], (raw, r)
    assert (r["basis"], r["min"], r["max"], r["bound"]) == (basis, lo, hi, bound), (raw, r)
    assert r["unit"] == unit, (raw, r)
    assert r["raw"] == raw, r          # raw는 언제나 원문 그대로

# 조건이 둘이면 열지 않는다. 앞부분만 잡으면 뒤 조건이 조용히 사라진다.
r = parse_intake("리놀레산은 4.0 g 이상 또는 리놀렌산은 0.6 g 이상")
assert not r["parsed"] and r["raw"].startswith("리놀레산"), r
# 라벨이 섞여 들어온 오염 문자열도 열지 않는다 (2-8에서 실제로 발생)
assert not parse_intake("총 플라보노이드로서 20 ~ 40 mg (3) 섭취 시 주의사항")["parsed"]
assert not parse_intake("")["parsed"]

# ── 블록 분할: 원료가 아닌 것을 원료로 세면 안 된다 ──
FIXTURE = """
 2-16
 EPA 및 DHA 함유 유지
 1) 제조기준
2) 규격
3) 최종제품의 요건
(1) 기능성 내용 : 혈중 중성지질 개선 ･ 혈행 개선에 도움을 줄 수 있음
(2) 일일섭취량
(가) 혈중 중성지질 개선 ･ 혈행 개선에 도움을 줄 수 있음 : EPA와 DHA의 합으로서 0.5 ~ 2 g
(나) 기억력 개선에 도움을 줄 수 있음 : EPA와 DHA의 합으로서 0.9 ~ 2 g
(3) 섭취 시 주의사항
(가) 의약품(항응고제, 항혈소판제, 혈압강하제 등) 복용 시 전문가와 상담할 것
(나) 이상사례 발생 시 섭취를 중단하고 전문가와 상담할 것
4) 시험법
(1) 성상 : 제 4. 2-7 성상시험법
 3-32
 지방산
시험법 본문이며 원료가 아니다.
"""
blocks = split_blocks(FIXTURE)
assert len(blocks) == 1, [b["code"] for b in blocks]   # 3-32는 시험법이라 제외
assert blocks[0]["code"] == "2-16" and blocks[0]["name"] == "EPA 및 DHA 함유 유지"

e = extract(blocks[0])
# 기능성마다 섭취량이 갈리면 (원료 × 기능성) 단위로 행이 늘어나야 한다.
# 원료당 범위 하나로 접으면 중성지질 콘텐츠에 기억력 기준이 붙는다.
assert len(e["rows"]) == 2, e["rows"]
assert e["rows"][0]["functional_claim"] == "혈중 중성지질 개선 ･ 혈행 개선에 도움을 줄 수 있음"
assert e["rows"][0]["daily_intake"]["max"] == 2.0
assert e["rows"][1]["daily_intake"]["min"] == 0.9
assert e["category"] == "기능성원료" and e["recognition_type"] == "고시형"
# 주의사항은 다음 라벨(4시험법) 전까지만 모은다
assert len(e["cautions"]) == 2 and "항응고제" in e["cautions"][0], e["cautions"]

# ── 단위 환산 ──
assert to_microgram(1, "g") == 1_000_000
assert to_microgram(500, "mg") == 500_000
assert norm_unit("mg/day") == "mg" and norm_unit("μg RAE") == "μg"
assert norm_unit("µg") == "μg"          # micro sign과 greek mu는 같은 단위다
assert to_microgram(1, "CFU") is None   # 환산 불가 단위는 열지 않는다

# ── 용량 비교 ──
EPA = parse_intake("EPA와 DHA의 합으로서 0.5 ~ 2 g")
assert compare_dose("500", "mg", EPA)["status"] == "within"      # 0.5 g
assert compare_dose("3600", "mg", EPA)["status"] == "above"      # 3.6 g
assert compare_dose("100", "mg", EPA)["status"] == "below"
# basis는 결과에 항상 따라붙어야 한다. 없으면 단일 성분 용량을 합계 기준과
# 비교하고도 통과했다고 읽게 된다.
assert compare_dose("500", "mg", EPA)["basis"] == "EPA와 DHA의 합"

MG = parse_intake("94.5 ~ 250 mg")
assert compare_dose("500", "mg", MG)["status"] == "above"

# 환산 불가는 넘겨짚지 않는다
r = compare_dose("500", "mg", parse_intake("100,000,000(1억) ~ 10,000,000,000(100억) CFU"))
assert r["status"] == "incomparable" and "환산" in r["reason"], r
# 같은 불투명 단위끼리는 직접 비교한다
assert compare_dose("5000000000", "CFU",
                    parse_intake("100,000,000(1억) ~ 10,000,000,000(100억) CFU"))["status"] == "within"

# 열지 못한 고시 표기로는 비교하지 않는다 — raw를 보라고 말한다
r = compare_dose("500", "mg", parse_intake("리놀레산은 4.0 g 이상 또는 리놀렌산은 0.6 g 이상"))
assert r["status"] == "incomparable" and r["range_raw"].startswith("리놀레산"), r
# 상한이 없는 '이상' 표기는 초과 판정을 낼 수 없다
assert compare_dose("9999", "g", parse_intake("식이섬유로서 5 g 이상"))["status"] == "within"
# 팩에 용량이 없으면 비교가 아니라 '비교 불가'다
assert compare_dose(None, None, EPA)["status"] == "incomparable"

print("ok")
