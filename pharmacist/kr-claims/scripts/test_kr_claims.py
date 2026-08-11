#!/usr/bin/env python3
"""kr-claims 계약 검사. 네트워크 없이 돈다. `python3 scripts/test_kr_claims.py`

케이스는 고시전문에서 **실제로 관찰한 표기**에서 가져온다.
파서가 조용히 틀리는 지점(천 단위 쉼표, 괄호 환산값, 복합 조건)이
전부 여기 들어 있어야 한다.
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_kr_claims import (BUILDER_VERSION, duplicate_names, extract,
                              is_ingredient_entry, parse_intake, split_blocks)
from check_kr_claims import compare_dose, find_ingredient, main as check_main, norm_unit, to_microgram

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
    # 중첩 괄호(S5). "3.3 g (베타글루칸(β-glucan)으로서 287.1~534.6 mg)"에서
    # 진짜 일일섭취량은 추출물 3.3 g이지, 괄호 안 지표성분 범위가 아니다.
    ("상황버섯추출물로서 3.3 g (베타글루칸(β-glucan)으로서 287.1 ~ 534.6 mg)",
     "상황버섯추출물", 3.3, 3.3, "g", "exact"),
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
# S5 위생 검사: 우연히 숫자 매칭에 성공해도 unit에 괄호가 남으면 parsed:false로
# 되돌린다 — 이게 바로 "우연히 안 터진" 구 버전 버그의 모양이다.
r = parse_intake("94.5 ~ 250 mg)")
assert not r["parsed"] and r["raw"] == "94.5 ~ 250 mg)", r

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

# ── S6: 라벨 조각만 남은 기능성 문구는 None으로 비운다 ──
# 1-2 베타카로틴에서 실제로 관찰된 모양이다. "(가)"로 시작해야 하위 항목으로
# 잡히므로, 실측 라벨 텍스트를 (가)(나) 항목 안에 그대로 넣어 재현한다.
LABEL_FIXTURE = """
 1-2
 베타카로틴
1) 제조기준
2) 최종제품의 요건
(1) 일일섭취량
(가) 1). (1). (가) 및 (나)의 경우 : 0.42 ~ 7 mg
(나) 1). (1). (다)의 경우 : 1.26 mg 이상
"""
blocks2 = split_blocks(LABEL_FIXTURE)
assert len(blocks2) == 1, [b["code"] for b in blocks2]
label_polluted: list[str] = []
e2 = extract(blocks2[0], label_polluted)
assert e2["rows"][0]["functional_claim"] is None, e2["rows"]
assert e2["rows"][1]["functional_claim"] is None, e2["rows"]
assert e2["rows"][0]["daily_intake"]["max"] == 7.0, e2["rows"]
assert label_polluted == ["1-2", "1-2"], label_polluted
# label_polluted를 안 넘기면(기존 호출부 호환) 조용히 넘어가되 여전히 비운다
e2b = extract(blocks2[0])
assert e2b["rows"][0]["functional_claim"] is None

# ── S1b: 동명 원료 탐지 ──
sample = [
    {"code": "1-6", "ingredient_ko": "비타민 X", "rows": [], "cautions": [], "functional_claims_raw": None},
    {"code": "1-7", "ingredient_ko": "비타민 X", "rows": [], "cautions": [], "functional_claims_raw": None},
    {"code": "1-8", "ingredient_ko": "비타민 Y", "rows": [], "cautions": [], "functional_claims_raw": None},
]
assert duplicate_names(sample) == {"비타민 X": ["1-6", "1-7"]}, duplicate_names(sample)

# ── S7: 원료가 아닌 블록(13-2 사례) 걸러내기 ──
GARBAGE = {"code": "13-2", "ingredient_ko": "4 프로폴리스추출물가공식품, 16-1 농",
           "rows": [], "functional_claims_raw": None}
assert not is_ingredient_entry(GARBAGE)
assert is_ingredient_entry({"rows": [], "functional_claims_raw": "x"})
assert is_ingredient_entry({"rows": [{"functional_claim": None, "daily_intake": None}],
                            "functional_claims_raw": None})

# ── S7: 조회 랭킹 — 정확일치 > 접두일치 > 부분일치 ──
RANK_CLAIMS = {"ingredients": [
    {"code": "A", "ingredient_ko": "철", "rows": [], "cautions": []},
    {"code": "B", "ingredient_ko": "프로폴리스추출물", "rows": [], "cautions": []},
]}
hits, kind = find_ingredient(RANK_CLAIMS, "철")
assert kind == "exact" and [h["code"] for h in hits] == ["A"], (kind, hits)
hits, kind = find_ingredient(RANK_CLAIMS, "프로폴리스")
assert kind == "prefix" and [h["code"] for h in hits] == ["B"], (kind, hits)
hits, kind = find_ingredient(RANK_CLAIMS, "폴리스")
assert kind == "partial" and [h["code"] for h in hits] == ["B"], (kind, hits)
hits, kind = find_ingredient(RANK_CLAIMS, "존재하지않음")
assert kind == "none" and hits == [], (kind, hits)

# ── S3: NFD 정규화 질의도 등재로 잡혀야 한다 ──
nfd_query = unicodedata.normalize("NFD", "마그네슘")
assert nfd_query != "마그네슘"  # 정말 바이트가 다른지 확인
MG_CLAIMS = {"ingredients": [{"code": "1-16", "ingredient_ko": "마그네슘", "rows": [], "cautions": []}]}
hits, kind = find_ingredient(MG_CLAIMS, nfd_query)
assert kind == "exact" and len(hits) == 1 and hits[0]["code"] == "1-16", (kind, hits)

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

# ── S4: RAE/DFE는 단위가 아니라 환산 기준이다. 안전 방향은 incomparable ──
RAE = parse_intake("210 ~ 1,000 μg RAE (699.93 ~ 3,333 IU)")
r = compare_dose("800", "μg", RAE)  # 논문이 그냥 μg만 썼다 — 기준이 다를 수 있다
assert r["status"] == "incomparable" and "RAE" in r["reason"], r
assert r["basis"] == "RAE", r  # 수식어가 basis로 승격돼 경고가 뜨게 한다
# 같은 기준끼리는 정상 비교한다
assert compare_dose("800", "μg RAE", RAE)["status"] == "within"

DFE = parse_intake("120 ~ 400 ㎍ DFE")
r = compare_dose("400", "μg", DFE)
assert r["status"] == "incomparable" and "DFE" in r["reason"], r

# ── S10: mg/kg(체중당) 같은 비율 단위는 절대 섭취량과 비교하면 안 된다 ──
RATE = parse_intake("10 ~ 20 mg/kg")
assert RATE["parsed"] and RATE["unit"] == "mg/kg", RATE  # 파싱 자체는 된다
r = compare_dose("15", "mg", RATE)
assert r["status"] == "incomparable" and "체중" in r["reason"], r

# ── S9: 데이터 파일의 provenance가 실제로 채워져 있는지 ──
DATA_PATH = pathlib.Path(__file__).parent.parent / "data" / "kr_claims.json"
_data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
assert _data["source"]["builder_version"] == BUILDER_VERSION, _data["source"]
assert "input_sha256" in _data["source"], _data["source"]  # raw 없으면 null이어도 키는 있어야 한다
# S1: 데이터 파일 자체가 보정을 반영했는지
_names = {e["code"]: e["ingredient_ko"] for e in _data["ingredients"]}
assert _names["1-6"] == "비타민 B1" and _names["1-7"] == "비타민 B2", _names
assert _names["1-10"] == "비타민 B6" and _names["1-12"] == "비타민 B12", _names
assert not _data["coverage"].get("duplicate_ingredient_names"), _data["coverage"]
# S7: 13-2 쓰레기 엔트리가 데이터에서 빠졌는지
assert "13-2" not in _names, _names
# S6: 커버리지에 missing_cautions 지표가 있는지
assert "missing_cautions" in _data["coverage"], _data["coverage"]


# ══════════════════════════════════════════════════════════════════════
# S8: check_kr_claims.py main()의 종료 코드·분기 회귀 테스트
# ══════════════════════════════════════════════════════════════════════

_MISSING_DEFAULT_SENTINEL = object()


def _write_claims(path: str, ingredients: list[dict], source: dict | None = _MISSING_DEFAULT_SENTINEL) -> None:
    doc: dict = {"schema": "kr-claims/0.1", "ingredients": ingredients}
    if source is not _MISSING_DEFAULT_SENTINEL:
        if source is not None:
            doc["source"] = source
    else:
        doc["source"] = {"title": "t", "notice": "n", "effective_date": "2026-01-01",
                          "url": None, "scope": "s", "checked_at": "2026-08-01",
                          "input_sha256": None, "builder_version": "test"}
    doc["coverage"] = {}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def _run_main(argv: list[str]) -> tuple[int, str]:
    """main()을 실행하고 (종료코드, stdout)을 돌려준다."""
    old_argv = sys.argv
    buf = io.StringIO()
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(buf):
            rc = check_main()
    finally:
        sys.argv = old_argv
    return rc, buf.getvalue()


MG_ROW = {"code": "1-16", "ingredient_ko": "마그네슘", "category": "영양성분",
          "recognition_type": "고시형", "functional_claims_raw": None,
          "rows": [{"functional_claim": None,
                    "daily_intake": {"raw": "94.5 ~ 250 mg", "basis": None,
                                     "min": 94.5, "max": 250.0, "unit": "mg",
                                     "bound": "range", "parsed": True}}],
          "cautions": []}

_tmpdir = tempfile.mkdtemp()
_claims = os.path.join(_tmpdir, "claims.json")
_out = os.path.join(_tmpdir, "report.json")
_pack = os.path.join(_tmpdir, "pack.json")

# 1) 미등재 → exit 2
_write_claims(_claims, [MG_ROW])
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "존재하지않는원료", "--output", _out])
assert rc == 2, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["found"] is False

# 2) 조회 모드 등재 → exit 0. functional_claim null 경고(S2)와
#    functional_claims_raw가 matches에 실렸는지(S2a)도 함께 확인한다.
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["found"] is True and rep["dose_checks"] == []
assert "functional_claims_raw" in rep["matches"][0]
assert any("문구를 생성하지 말" in w for w in rep["warnings"]), rep["warnings"]

# NFD 질의로도 같은 원료가 잡혀야 한다(S3, 풀스택)
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", unicodedata.normalize("NFD", "마그네슘"), "--output", _out])
assert rc == 0
assert json.load(open(_out, encoding="utf-8"))["found"] is True

# 3) 상한 초과 → exit 2
json.dump({"topic": "마그네슘", "papers": [
    {"pmid": "1", "slots": {"dose": {"verified": True, "value": "500", "unit": "mg"}}}]},
    open(_pack, "w", encoding="utf-8"), ensure_ascii=False)
rc, _ = _run_main(["check_kr_claims.py", "--pack", _pack, "--claims", _claims,
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 2, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["dose_checks"][0]["status"] == "above"

# 4) 검증된 용량 슬롯 없음 → exit 2
json.dump({"topic": "마그네슘", "papers": [
    {"pmid": "1", "slots": {"dose": {"verified": False, "value": "500", "unit": "mg"}}}]},
    open(_pack, "w", encoding="utf-8"), ensure_ascii=False)
rc, _ = _run_main(["check_kr_claims.py", "--pack", _pack, "--claims", _claims,
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 2, rc
rep = json.load(open(_out, encoding="utf-8"))
assert any("검증된 용량 슬롯이 없어" in w for w in rep["warnings"]), rep["warnings"]

# 5) 오래된 추출본이면 stale 경고가 삽입된다 (조회 모드이므로 exit는 0)
_write_claims(_claims, [MG_ROW],
             source={"title": "t", "notice": "n", "effective_date": "2026-01-01",
                     "url": None, "scope": "s", "checked_at": "2000-01-01",
                     "input_sha256": None, "builder_version": "test"})
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert any("추출본이다" in w for w in rep["warnings"]), rep["warnings"]

# 6) S10: 존재하지 않는 --claims 경로 → exit 2, 트레이스백 없이
rc, _ = _run_main(["check_kr_claims.py", "--claims", "/no/such/claims-file.json",
                   "--ingredient", "마그네슘"])
assert rc == 2, rc

# 7) S10: source가 통째로 없으면 경고
_write_claims(_claims, [MG_ROW], source=None)
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert any("출처" in w for w in rep["warnings"]), rep["warnings"]

# 8) S10: raw에 시행 전 개정 주석이 섞인 행은 경고를 남긴다 (2-49 사례)
KEY_ROW = {"code": "2-49", "ingredient_ko": "키토산", "category": "기능성원료",
           "recognition_type": "고시형", "functional_claims_raw": "체지방 감소에 도움을 줄 수 있음",
           "rows": [{"functional_claim": "체지방 감소에 도움을 줄 수 있음",
                     "daily_intake": {"raw": "키토산으로서 3.0 ~ 4.5 g 고시 제2026-32호(2026. 4. 13.), "
                                              "시행일(2027.1.1.)",
                                      "basis": None, "min": None, "max": None,
                                      "unit": None, "bound": None, "parsed": False}}],
           "cautions": []}
_write_claims(_claims, [KEY_ROW])
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "키토산", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert any("시행" in w for w in rep["warnings"]), rep["warnings"]

# 9) S1b: 동명 원료 경고가 실제로 뜨는지 (build 커버리지 → check 경고 전달)
DUP_A = {**MG_ROW, "code": "1-16b"}
_write_claims(_claims, [MG_ROW, DUP_A])
_doc = json.load(open(_claims, encoding="utf-8"))
_doc["coverage"]["duplicate_ingredient_names"] = {"마그네슘": ["1-16", "1-16b"]}
json.dump(_doc, open(_claims, "w", encoding="utf-8"), ensure_ascii=False)
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert any("동명이인" in w for w in rep["warnings"]), rep["warnings"]

# 10) S7: 부분일치일 때 "질의어와 등재명이 다르다"를 남긴다
_write_claims(_claims, [MG_ROW])
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "그네슘", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert any("정확히 일치하지 않는다" in w for w in rep["warnings"]), rep["warnings"]

# 11) S10: --output 없이 돌리면 stdout에 리포트 JSON 하나만 있어야
#     jq가 그대로 통과한다 (요약은 전부 stderr)
rc, out_text = _run_main(["check_kr_claims.py", "--claims", _claims, "--ingredient", "마그네슘"])
assert rc == 0, rc
_decoder = json.JSONDecoder()
_obj, _idx = _decoder.raw_decode(out_text)
assert out_text[_idx:].strip() == "", repr(out_text[_idx:])


# ══════════════════════════════════════════════════════════════════════
# R1: 오류 경로에서 --output이 갱신되지 않아 **직전 실행의 통과 리포트가
# 디스크에 그대로 남던** 문제. 다음 단계가 그걸 이번 결과로 읽으면
# 상한 초과 용량이 검증된 것처럼 콘텐츠에 실린다.
# ══════════════════════════════════════════════════════════════════════

# 먼저 정상 실행으로 통과 리포트를 남긴다
_write_claims(_claims, [MG_ROW])
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 0, rc
assert json.load(open(_out, encoding="utf-8"))["found"] is True

# 그 다음 깨진 팩으로 실행 — 예외로 죽지 않고, 리포트도 덮어써야 한다
with open(_pack, "w", encoding="utf-8") as f:
    f.write("{깨진 JSON")
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims, "--pack", _pack,
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 2, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["verdict"] == "block", rep
assert rep["found"] is None, "직전 통과 리포트가 그대로 남아 있다"
assert any("오류 리포트" in w for w in rep["warnings"]), rep["warnings"]

# 없는 팩 경로도 마찬가지
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--pack", os.path.join(_tmpdir, "없는팩.json"),
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 2, rc
assert json.load(open(_out, encoding="utf-8"))["verdict"] == "block"

# 팩이 배열이어도 죽지 않는다
with open(_pack, "w", encoding="utf-8") as f:
    json.dump(["not", "a", "dict"], f)
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims, "--pack", _pack,
                   "--ingredient", "마그네슘", "--output", _out])
assert rc == 2, rc


# ══════════════════════════════════════════════════════════════════════
# R2: 접두일치도 경고한다. "홍삼농축액"으로 물으면 "홍삼"이 접두로 걸려
# 다른 원료의 기준이 무경고로 돌아오던 경로다.
# ══════════════════════════════════════════════════════════════════════

_HS = dict(MG_ROW, code="2-1", ingredient_ko="홍삼")
_write_claims(_claims, [_HS])
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "홍삼농축액", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert any("접두일치" in w for w in rep["warnings"]), rep["warnings"]
assert rep["verdict"] == "review", rep
assert any("정확히 일치하지 않는다" in r for r in rep["verdict_reasons"]), rep


# ══════════════════════════════════════════════════════════════════════
# R3: verdict — 조회 모드·below·basis 미확인이 전부 exit 0으로 수렴하던
# 것을 호출자가 필드로 가를 수 있는가.
# ══════════════════════════════════════════════════════════════════════

# 문구·주의사항이 다 있고 정확일치한 조회 모드도 "대조 안 함"으로 review다
_CLEAN = {"code": "2-2", "ingredient_ko": "키토산", "category": "기능성원료",
          "recognition_type": "고시형", "functional_claims_raw": "raw",
          "rows": [{"functional_claim": "혈중 콜레스테롤 개선에 도움을 줄 수 있음",
                    "daily_intake": {"raw": "1.2 ~ 4.5 g", "basis": None,
                                     "min": 1.2, "max": 4.5, "unit": "g",
                                     "bound": "range", "parsed": True}}],
          "cautions": ["주의"]}
_write_claims(_claims, [_CLEAN])
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "키토산", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["verdict"] == "review", rep
assert any("조회 모드" in r for r in rep["verdict_reasons"]), rep["verdict_reasons"]


def _write_pack(path, value, unit):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"schema": "evidence-pack/0.3", "topic": "키토산 × 콜레스테롤",
                   "papers": [{"pmid": "1", "slots": {"dose": {
                       "slot": "dose", "value": value, "unit": unit,
                       "quote": "q", "verified": True}}}]}, f)


# 범위 안 + 이름 정확일치 + 문구 있음 → pass
_write_pack(_pack, "2", "g")
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims, "--pack", _pack,
                   "--ingredient", "키토산", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["verdict"] == "pass", rep
assert rep["verdict_reasons"] == [], rep["verdict_reasons"]

# 최소치 미만 → exit 0은 그대로지만 verdict는 review로 갈린다
_write_pack(_pack, "0.5", "g")
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims, "--pack", _pack,
                   "--ingredient", "키토산", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["verdict"] == "review", rep
assert any("최소치 미만" in r for r in rep["verdict_reasons"]), rep["verdict_reasons"]

# 상한 초과 → block, exit 2 (기존 계약 그대로)
_write_pack(_pack, "9", "g")
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims, "--pack", _pack,
                   "--ingredient", "키토산", "--output", _out])
assert rc == 2, rc
assert json.load(open(_out, encoding="utf-8"))["verdict"] == "block"

# basis가 붙은 섭취량: 숫자는 범위 안이지만 같은 것을 재는지 확인되지 않았다.
# RAE/DFE는 incomparable로 막으면서 이쪽만 조용히 within으로 통과하던 비대칭.
_BASIS = {"code": "2-3", "ingredient_ko": "이피에이", "category": "기능성원료",
          "recognition_type": "고시형", "functional_claims_raw": "raw",
          "rows": [{"functional_claim": "혈중 중성지질 개선에 도움을 줄 수 있음",
                    "daily_intake": {"raw": "EPA와 DHA의 합으로서 0.5 ~ 2 g",
                                     "basis": "EPA와 DHA의 합", "min": 0.5, "max": 2.0,
                                     "unit": "g", "bound": "range", "parsed": True}}],
          "cautions": ["주의"]}
_write_claims(_claims, [_BASIS])
_write_pack(_pack, "1", "g")
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims, "--pack", _pack,
                   "--ingredient", "이피에이", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["dose_checks"][0]["status"] == "within", rep["dose_checks"]
assert rep["dose_checks"][0]["basis_unconfirmed"] is True, rep["dose_checks"]
assert rep["verdict"] == "review", rep
assert any("기준 성분" in r for r in rep["verdict_reasons"]), rep["verdict_reasons"]

# 미등재는 block이고 종료 코드 2다 ("미인정"이 아니라는 문구도 그대로)
_write_claims(_claims, [_CLEAN])
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "없는원료", "--output", _out])
assert rc == 2, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["verdict"] == "block" and rep["found"] is False, rep
assert any("개별인정형" in r for r in rep["verdict_reasons"]), rep["verdict_reasons"]


# ══════════════════════════════════════════════════════════════════════
# R4: build_kr_claims.py 코드 연속성 — 블록으로 잡히지도 못한 원료는
# 어느 커버리지 필드에도 안 남아 "처음부터 없었던 것"처럼 보였다.
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# R5: 문구 칸이 채워져 있어도 인정 문구가 아닐 수 있다 (1-8 나이아신의
# "니코틴산인 경우"는 기능성이 아니라 적용 조건이다). 빈 칸보다 위험하다 —
# 빈 칸은 채우라는 신호라도 되지만 이건 그냥 표로 옮겨진다.
# 막을 것을 열거하지 않고 허용할 서술어를 닫는다.
# ══════════════════════════════════════════════════════════════════════

from check_kr_claims import claim_shape  # noqa: E402

assert claim_shape("혈중 콜레스테롤 개선에 도움을 줄 수 있음") == "ok"
assert claim_shape("탄수화물과 에너지 대사에 필요") == "ok"          # 영양성분 템플릿
assert claim_shape("체내 탄수화물, 지방, 단백질 대사에 관여") == "ok"
assert claim_shape("니코틴산인 경우") == "unexpected"                 # 1-8
assert claim_shape("니코틴산아미드인 경우") == "unexpected"           # 1-8
assert claim_shape(None) == "empty"
assert claim_shape("   ") == "empty"

_ODD = {"code": "1-8", "ingredient_ko": "나이아신", "category": "영양성분",
        "recognition_type": "고시형", "functional_claims_raw": "raw",
        "rows": [{"functional_claim": "니코틴산인 경우",
                  "daily_intake": {"raw": "3.5 ~ 23 mg", "basis": None, "min": 3.5,
                                   "max": 23.0, "unit": "mg", "bound": "range",
                                   "parsed": True}}],
        "cautions": ["주의"]}
_write_claims(_claims, [_ODD])
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "나이아신", "--output", _out])
assert rc == 0, rc
rep = json.load(open(_out, encoding="utf-8"))
assert rep["matches"][0]["rows"][0]["claim_shape"] == "unexpected", rep["matches"]
assert rep["verdict"] == "review", rep
assert any("문구의 꼴이 아닌" in r for r in rep["verdict_reasons"]), rep["verdict_reasons"]
# 경고에는 어느 행인지 원문이 실려야 사람이 확인할 수 있다
assert any("니코틴산인 경우" in w for w in rep["warnings"]), rep["warnings"]

# 정상 문구만 있으면 이 사유는 붙지 않는다
_write_claims(_claims, [_CLEAN])
rc, _ = _run_main(["check_kr_claims.py", "--claims", _claims,
                   "--ingredient", "키토산", "--output", _out])
rep = json.load(open(_out, encoding="utf-8"))
assert not any("문구의 꼴이 아닌" in r for r in rep["verdict_reasons"]), rep["verdict_reasons"]


from build_kr_claims import missing_sequence_codes  # noqa: E402

_e = lambda c: {"code": c}  # noqa: E731
_seq = lambda g, ns: [_e(f"{g}-{n}") for n in ns]  # noqa: E731

# 실제로 유실됐던 형태: 군 2에 1~8이 있고 7만 없다
assert missing_sequence_codes(_seq(2, [1, 2, 3, 4, 5, 6, 8]), []) == ["2-7"]
# dropped로 잡힌 코드는 결번이 아니다 — 이미 다른 필드에 남아 있다
assert missing_sequence_codes(_seq(13, [1, 3]), ["13-2"]) == []
# dropped는 군의 상한을 정하지 않는다. 원료 항목이 하나도 없는 군에서
# 없는 결번을 만들어내던 경로 — 실제 데이터의 13-2가 "13-1 유실"을 낳았다.
assert missing_sequence_codes(_seq(1, [1, 2]), ["13-2"]) == []
# 연속이면 빈 목록
assert missing_sequence_codes(_seq(1, [1, 2, 3]), []) == []
# 1번부터 센다 — 군의 앞쪽이 통째로 안 읽힌 경우가 가장 위험하다
assert missing_sequence_codes(_seq(1, [3]), []) == ["1-1", "1-2"]
# 군이 여럿이면 각각 따로 센다
assert missing_sequence_codes(_seq(1, [1, 3]) + _seq(2, [2]), []) == ["1-2", "2-1"]
# 정렬은 사전순이 아니라 수열순이다 (2-10이 2-9보다 뒤)
assert missing_sequence_codes(_seq(2, [11]), [])[-1] == "2-10"

print("ok")
