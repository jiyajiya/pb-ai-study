#!/usr/bin/env python3
"""verify_slot 계약 검사. 네트워크 없이 돈다. `python3 scripts/test_verify_numbers.py`

케이스는 구현이 아니라 **문서에서** 가져온다.
abstract-miner.md "effect에 넣으면 안 되는 것" 목록과
tests/fixtures.md의 릴리스 불가 조건이 여기 그대로 들어와야 한다.
"""
import sys, pathlib
from xml.etree import ElementTree as ET
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from verify_numbers import (verify_slot, verify_quote_slot, normalize,
                            parse_record, design_check)

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

# ── C2: 숫자는 수 경계까지 맞아야 한다 ──
# 부분문자열 검사이던 시절 아래가 전부 verified=True로 통과했다.
# 부호 소실과 자릿수 변경은 카드에 실리는 순간 그대로 오정보가 된다.
for value, quote, why in [
    ("0.34", EFFECT_Q, "부호 소실 — 감소가 증가로 읽힌다"),
    ("34", EFFECT_Q, "100배"),
    ("17", MIN_Q, "반올림 — abstract-miner.md가 명시 금지한 것"),
    ("7.36", MIN_Q, "앞자리 절단"),
    ("1", MIN_Q, "한 자리만 떼어냄"),
    ("50", "Subjects received 500 mg magnesium daily for 8 weeks.", "1/10 용량"),
]:
    r = verify_slot("effect", slot(value, quote), ABS)
    assert not r["verified"] and r["reason"] == "value_not_in_quote", (why, value, r)
    assert r["value"] is None, r

# 반대로 정상 값은 과잉 폐기되면 안 된다 (수 경계가 좁으면 전부 죽는다)
for value, quote in [
    ("-0.34", EFFECT_Q),          # 음수 부호 포함
    ("17.36", MIN_Q),             # 소수
    ("500", "Subjects received 500 mg magnesium daily for 8 weeks."),
    ("8", "Subjects received 500 mg magnesium daily for 8 weeks."),  # 한 자리 정수
    ("-5.2", SBP_Q),
]:
    assert verify_slot("dose", slot(value, quote), ABS)["verified"], (value, quote)

# 숫자가 없는 값은 그대로 부분문자열 검사 (수 경계 규칙이 적용되면 안 된다)
assert verify_slot("form", slot("magnesium",
                                "Subjects received 500 mg magnesium daily for 8 weeks."),
                   ABS)["verified"]

# significance도 같은 구멍이 있었다 — "P = 0.0"이 "P = 0.02"에 걸리면 안 된다
r = verify_slot("effect", slot("-0.34", EFFECT_Q, unit="mmol/L", significance="P = 0.0"), ABS)
assert r["verified"] and r["significance"] is None, r
assert r["dropped_significance"] == "P = 0.0", r

# ── C1/C2는 그대로 ──
assert verify_slot("effect", slot("-0.34 mmol/L", "지어낸 문장이다."), ABS)["reason"] == "quote_not_in_abstract"
assert verify_slot("effect", slot("-0.99 mmol/L", EFFECT_Q), ABS)["reason"] == "value_not_in_quote"
assert verify_slot("form", None, ABS)["reason"] == "not_extracted"


# ── 무결성 파싱 ──
# 픽스처의 형태는 실제 efetch 응답에서 확인한 것이다.
# PMID 9500320: PT에 'Retracted Publication' + RetractionIn 2건 + ExpressionOfConcernIn
# PMID 23853635: CoiStatement 요소는 있으나 내용이 비어 있음
# PMID 41500861: CoiStatement에 제조사 고용 사실이 적혀 있음
def rec(xml): return parse_record(ET.fromstring(xml))

RETRACTED = """<PubmedArticleSet><PubmedArticle><MedlineCitation>
 <DateRevised><Year>2024</Year></DateRevised>
 <Article><Journal><ISOAbbreviation>Lancet</ISOAbbreviation>
   <JournalIssue><PubDate><Year>1998</Year></PubDate></JournalIssue></Journal>
  <ArticleTitle>Ileal-lymphoid-nodular hyperplasia.</ArticleTitle>
  <Abstract><AbstractText Label="RESULTS">Onset latency decreased by 17.36 min.</AbstractText></Abstract>
  <PublicationTypeList><PublicationType>Journal Article</PublicationType>
   <PublicationType>Retracted Publication</PublicationType></PublicationTypeList></Article>
 <CommentsCorrectionsList>
  <CommentsCorrections RefType="RetractionIn"><PMID>20137807</PMID></CommentsCorrections>
  <CommentsCorrections RefType="ExpressionOfConcernIn"><PMID>21971344</PMID></CommentsCorrections>
 </CommentsCorrectionsList>
 <CoiStatement></CoiStatement>
</MedlineCitation></PubmedArticle></PubmedArticleSet>"""

r = rec(RETRACTED)
assert r["integrity"]["retracted"], r
assert r["integrity"]["retraction_pmids"] == ["20137807"], r
assert r["integrity"]["expression_of_concern"], r
# 연도는 PubDate에서 온다. root.iter("Year")로 긁으면 DateRevised의 2024가 먼저 잡힌다
assert r["year"] == "1998", r
assert r["journal"] == "Lancet", r
# 빈 CoiStatement를 "이해충돌 없음"으로 읽으면 안 된다
assert r["integrity"]["coi_statement"] is None and not r["integrity"]["coi_available"], r
# 초록이 있으면 제목이 뒤에 붙는다 (기존 C1 대조 동작 유지)
assert "17.36 min" in r["abstract"] and "Ileal-lymphoid" in r["abstract"], r
assert len(r["abstract_sha256"]) == 64, r

FUNDED = """<PubmedArticleSet><PubmedArticle><MedlineCitation>
 <Article><Journal><ISOAbbreviation>J Clin Lipidol</ISOAbbreviation>
   <JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal>
  <ArticleTitle>EPA trial.</ArticleTitle>
  <Abstract><AbstractText>Triglycerides fell by 0.34 mmol/L.</AbstractText></Abstract>
  <PublicationTypeList><PublicationType>Journal Article</PublicationType>
   <PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList></Article>
 <CommentsCorrectionsList>
  <CommentsCorrections RefType="ErratumIn"><PMID>41999999</PMID></CommentsCorrections>
 </CommentsCorrectionsList>
 <CoiStatement>Takuya Mori is employed by Mochida Pharmaceutical Co., Ltd.</CoiStatement>
</MedlineCitation></PubmedArticle></PubmedArticleSet>"""

r = rec(FUNDED)
assert not r["integrity"]["retracted"], r
assert r["integrity"]["has_erratum"] and r["integrity"]["erratum_pmids"] == ["41999999"], r
assert r["integrity"]["coi_available"], r
assert "Mochida" in r["integrity"]["coi_statement"], r

# 초록 없는 레코드: 제목만으로 대조하면 전부 환각으로 오분류되므로 빈 문자열이어야 한다
NO_ABS = """<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>
 <Journal><Title>Some Journal</Title>
  <JournalIssue><PubDate><MedlineDate>2011 Spring</MedlineDate></PubDate></JournalIssue></Journal>
 <ArticleTitle>Title only.</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
r = rec(NO_ABS)
assert r["abstract"] == "" and r["abstract_sha256"] is None, r
assert r["year"] == "2011", r                    # MedlineDate 폴백
assert r["journal"] == "Some Journal", r         # ISOAbbreviation 없으면 Title

# ── design 교차검증: 태그가 있을 때만, 태그 부재는 불일치가 아니다 ──
assert design_check("meta-analysis", ["Journal Article", "Meta-Analysis", "Review"])["agrees"] is True
assert design_check("rct", ["Journal Article", "Meta-Analysis", "Review"])["agrees"] is False
# 2013년 RCT처럼 설계 태그가 없는 레코드가 흔하다. 이걸 불일치로 읽으면 경고가 무의미해진다
assert design_check("rct", ["Journal Article"])["agrees"] is None
assert design_check("rct", [])["agrees"] is None
# 'Review'만 있으면 서술형/체계적을 구분 못한다 — 원 시험 설계 주장만 잡는다
assert design_check("rct", ["Review"])["agrees"] is False
assert design_check("meta-analysis", ["Review"])["agrees"] is True
assert design_check(None, ["Randomized Controlled Trial"])["agrees"] is False

# ── C5: conclusion 슬롯은 quote만 본다 ──
CONC_Q = "Sleep onset latency decreased by 17.36 min."
assert verify_quote_slot("conclusion", {"quote": CONC_Q}, ABS)["verified"]
assert verify_quote_slot("conclusion", CONC_Q, ABS)["verified"]        # 문자열로 와도 받는다
r = verify_quote_slot("conclusion", {"quote": "저자가 이렇게 결론지었다."}, ABS)
assert not r["verified"] and r["reason"] == "quote_not_in_abstract", r
assert r["quote"] is None and r["rejected_quote"] == "저자가 이렇게 결론지었다.", r
assert verify_quote_slot("conclusion", None, ABS)["reason"] == "not_extracted"
assert verify_quote_slot("conclusion", {"quote": ""}, ABS)["reason"] == "missing_quote"
# value/unit이 붙어 와도 C2~C4를 적용하지 않는다 — 결론 슬롯에는 검사할 숫자가 없다
assert verify_quote_slot("conclusion", {"quote": CONC_Q, "value": "17.36", "unit": "hours"},
                         ABS)["verified"]

print("ok")
