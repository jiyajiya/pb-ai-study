#!/usr/bin/env python3
"""verify_slot 계약 검사. 네트워크 없이 돈다. `python3 scripts/test_verify_evidence.py`

케이스는 구현이 아니라 **문서에서** 가져온다.
abstract-miner.md "effect에 넣으면 안 되는 것" 목록과
tests/fixtures.md의 릴리스 불가 조건이 여기 그대로 들어와야 한다.
"""
import sys, pathlib
from xml.etree import ElementTree as ET
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from verify_evidence import (verify_slot, verify_quote_slot, normalize, REJECT_REASONS,
                            parse_record, design_check, has_multiple_sentences,
                            PubmedCountError, NotFoundError)
import contextlib, io, json, os, tempfile
import verify_evidence

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

# value에 단위·비교대상·p값을 통째로 넣는 것도 폐기다.
# 스키마가 unit·comparator·significance를 따로 두는 이유가 이것이다.
r = verify_slot("effect", slot("-0.34 mmol/L compared with placebo (P = 0.02)", EFFECT_Q), ABS)
assert not r["verified"] and r["reason"] == "effect_value_not_numeric", r

# 위와 같은 내용을 스키마대로 나눠 담으면 통과한다
r = verify_slot("effect", slot("-0.34", EFFECT_Q, unit="mmol/L",
                               comparator="placebo", significance="P = 0.02"), ABS)
assert r["verified"] and r["significance"] == "P = 0.02", r

# 지표명은 measure로 뺀다. "RR 0.78"을 value에 통째로 넣으면 폐기다 —
# 프로즈를 허용하는 순간 "p for trend = 0.03"도 같이 들어온다.
RR_Q = "The pooled estimate was RR 0.78 in the treatment arm."
RR_ABS = normalize(RR_Q)
assert verify_slot("effect", slot("RR 0.78", RR_Q), RR_ABS)["reason"] == "effect_value_not_numeric"
r = verify_slot("effect", slot("0.78", RR_Q, measure="RR"), RR_ABS)
assert r["verified"] and r["measure"] == "rr", r

# measure는 열거 검증만 한다. 열거값 밖이면 슬롯을 죽이지 않고 필드만 null
r = verify_slot("effect", slot("0.78", RR_Q, measure="pooled estimate"), RR_ABS)
assert r["verified"] and r["measure"] is None, r

# 이전 차감식은 p로 끝나는 단어 뒤의 음수를 p값으로 오인해 정상 효과를 죽였다
# (SBP는 마그네슘×혈압, sleep은 F2의 주 지표다). 화이트리스트는 판정이
# 마커 패턴에 의존하지 않으므로 이 회귀 자체가 성립하지 않는다.
for v_, u_ in [("-5.2", "mmHg"), ("-1.2", "points")]:
    r = verify_slot("effect", slot(v_, SBP_Q, unit=u_), ABS)
    assert r["verified"], (v_, r)

# ── C3가 차감식이던 시절 새어 나가던 표현들 ──
# 전부 초록에 실재하고(C1) 글자 그대로 옮겨졌지만(C2) 효과 크기가 아니다.
# 마커를 열거하는 방식으로는 이런 변형이 나올 때마다 구멍이 생겼다.
LEAK_Q = ("Analyses showed P for trend = 0.03, p-trend 0.02, P value of 0.03, "
          "and p values ranged from 0.01 to 0.04, "
          "with 95% confidence interval of 0.12 to 0.48 in the pooled model.")
LEAK_ABS = normalize(LEAK_Q)
for value in [
    "P for trend = 0.03",      # P_TOKEN이 "for trend"를 몰라 통과하던 것
    "p-trend 0.02",
    "P value of 0.03",         # 연산자 대신 "of"가 오면 통과하던 것
    "p values ranged from 0.01 to 0.04",
    "95% confidence interval of 0.12 to 0.48",   # CI_TOKEN이 "of"를 몰랐다
    "0.12 to 0.48",            # CI 표기 없는 맨 구간
]:
    r = verify_slot("effect", slot(value, LEAK_Q), LEAK_ABS)
    assert not r["verified"], (value, r)
    assert r["reason"] in REJECT_REASONS and r["value"] is None, (value, r)

# 반대로 숫자 하나는 형태만 맞으면 전부 통과한다 (화이트리스트가 좁으면 안 된다)
NUM_Q = "Values were 0.78, -0.34, 23.4%, 1,200 and 17.36 across the cohorts."
NUM_ABS = normalize(NUM_Q)
for value in ["0.78", "-0.34", "23.4%", "1,200", "17.36"]:
    assert verify_slot("effect", slot(value, NUM_Q), NUM_ABS)["verified"], value

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


# ══════════════════════════════════════════════════════════════════
# S1: quote는 한 문장이어야 한다. C1이 부분문자열만 보고 문장 수를 안 세면
# 두 문장짜리 quote 하나로 C2/C4가 quote 전체에서 검사하게 되어 unit 검사가
# 무력화된다. 실증된 재현: quote가 "500 mg magnesium daily. Sleep onset
# latency decreased by 17.36 min."이면 effect value=17.36, unit=mg가
# (전혀 다른 문장에서 온 단위인데도) 통과했다.
# ══════════════════════════════════════════════════════════════════

TWO_SENT_Q = ("Subjects received 500 mg magnesium daily. "
              "Sleep onset latency decreased by 17.36 min.")
TWO_SENT_ABS = normalize(TWO_SENT_Q)

# 재현 그대로: 17.36이 mg 단위로 카드에 찍히던 케이스가 이제 폐기된다
r = verify_slot("effect", slot("17.36", TWO_SENT_Q, unit="mg"), TWO_SENT_ABS)
assert not r["verified"] and r["reason"] == "quote_multiple_sentences", r
assert r["value"] is None, r

# 세 문장도 마찬가지
THREE_SENT_Q = TWO_SENT_Q + " The effect was statistically significant."
assert has_multiple_sentences(THREE_SENT_Q), THREE_SENT_Q

# C5(conclusion)도 같은 계약을 적용한다
r = verify_quote_slot("conclusion", {"quote": TWO_SENT_Q}, TWO_SENT_ABS)
assert not r["verified"] and r["reason"] == "quote_multiple_sentences", r
assert r["quote"] is None, r

# 정상 케이스: 진짜 단일 문장은 그대로 통과한다
assert verify_slot("effect", slot("17.36", MIN_Q, unit="min"), ABS)["verified"]

# ── 오탐 방지: 초록에 흔한 약어 뒤의 마침표는 문장 경계가 아니다 ──
# 약어 뒤에 대문자로 시작하는 단어가 와야만 오탐 후보가 된다는 점에 주의:
# "e.g. diabetes"처럼 소문자가 이어지면 애초에 SENTENCE_BREAK 자체가
# 매치하지 않는다. 아래는 일부러 대문자로 이어 붙여 오탐 방지 로직을 시험한다.
for abbr_quote in [
    "Patients on statins (e.g. Atorvastatin) had lower LDL levels than controls.",
    "Serum levels were assessed (i.e. Fasting samples) before treatment.",
    "Treatment vs. Placebo showed a mean difference of -0.34 mmol/L.",
    "Results reported by Smith et al. Showed significant improvement overall.",
    "See protocol No. Three for randomization details in this trial.",
    "The outcome is summarized in Fig. Two of the supplementary material.",
    "The study was led by Dr. Kim and colleagues at the center.",
]:
    assert not has_multiple_sentences(abbr_quote), abbr_quote

# 약어가 든 단일 문장이 verify_slot을 실제로 통과하는지도 확인 (오탐 없음)
ABBR_EFFECT_Q = "Statins (e.g. Atorvastatin) reduced LDL by 23.4% compared with placebo."
r = verify_slot("effect", slot("23.4%", ABBR_EFFECT_Q), normalize(ABBR_EFFECT_Q))
assert r["verified"], r

# 확인만: 소수점과 섹션 라벨은 원래부터 안 걸린다 (브리프가 명시한 확인 사항)
assert not has_multiple_sentences("Triglycerides decreased by 0.34 mmol/L in the group.")
assert not has_multiple_sentences("RESULTS: Triglycerides decreased by 0.34 mmol/L.")


# ══════════════════════════════════════════════════════════════════
# S4: %도 수 경계다. "reduced by 23%"에서 unit 없이 value="23"만 떼어 오면
# "23% 감소"라는 주장이 사라진다.
# ══════════════════════════════════════════════════════════════════

PCT_BOUNDARY_Q = "Risk was reduced by 23% versus placebo."
PCT_BOUNDARY_ABS = normalize(PCT_BOUNDARY_Q)
r = verify_slot("effect", slot("23", PCT_BOUNDARY_Q), PCT_BOUNDARY_ABS)
assert not r["verified"] and r["reason"] == "value_not_in_quote", r
assert r["value"] is None, r
r = verify_slot("effect", slot("23%", PCT_BOUNDARY_Q), PCT_BOUNDARY_ABS)
assert r["verified"] and r["unit_type"] == "percent", r


# ══════════════════════════════════════════════════════════════════
# S5: 저비용 4건
# ══════════════════════════════════════════════════════════════════

# 1) '+' 부호. abstract-miner.md:46은 "부호"라고만 쓰는데 코드는 '-?'만
#    허용했다 — 정상 양수 효과크기가 false FAIL 나던 것을 고친다.
PLUS_Q = "HDL cholesterol increased by +2.3 mg/dL after treatment."
r = verify_slot("effect", slot("+2.3", PLUS_Q, unit="mg/dL"), normalize(PLUS_Q))
assert r["verified"], r
# 형태가 아니면 여전히 폐기된다 (부호 확장이 화이트리스트를 헐겁게 하지 않았다)
r = verify_slot("effect", slot("+-2.3", PLUS_Q), normalize(PLUS_Q))
assert not r["verified"], r

# 2) unit=""은 부재이지 값이 아니다. None으로 정규화되어야 카드에 빈 문자열이
#    안 찍힌다.
DOSE_Q_UNIT = "Subjects received 500 mg magnesium daily for 8 weeks."
r = verify_slot("dose", slot("500", DOSE_Q_UNIT, unit=""), ABS)
assert r["verified"] and r["unit"] is None, r

# 3) malformed_slot: 슬롯 자체가 dict가 아니면(문자열·숫자·리스트) 폐기하되
#    죽지 않는다. verify_quote_slot과의 일관성 결함을 verify_slot에도 채운다.
for bad_slot in ["not a dict", 42, ["17.36", "min"]]:
    r = verify_slot("effect", bad_slot, ABS)
    assert not r["verified"] and r["reason"] == "malformed_slot", (bad_slot, r)
    assert r["value"] is None, r
# 정상 케이스: dict면 그대로 통과 경로를 탄다 (회귀 없음)
assert verify_slot("effect", slot("-0.34", EFFECT_Q, unit="mmol/L"), ABS)["verified"]

# 4) 존재하지 않는 PMID: efetch가 빈 PubmedArticleSet을 반환하면 parse_record가
#    PubmedCountError(count=0)를 낸다. no_abstract(초록 없는 레코드)와는 다른
#    사유다 — 논문 자체가 없다는 뜻이므로.
EMPTY_SET = "<PubmedArticleSet></PubmedArticleSet>"
try:
    parse_record(ET.fromstring(EMPTY_SET))
    assert False, "PubmedArticle이 0개인데 예외가 안 났다"
except PubmedCountError as e:
    assert e.count == 0, e.count

# 응답에 PubmedArticle이 2개 이상이면(쉼표 pmid로 두 논문이 합쳐진 경우) 거부한다.
# main()의 pmid 형식 검사가 대부분 막지만, 응답 형태로도 한 번 더 막는 방어선이다.
TWO_ARTICLES = (
    "<PubmedArticleSet>"
    "<PubmedArticle><MedlineCitation><Article><Journal><Title>A</Title>"
    "</Journal></Article></MedlineCitation></PubmedArticle>"
    "<PubmedArticle><MedlineCitation><Article><Journal><Title>B</Title>"
    "</Journal></Article></MedlineCitation></PubmedArticle>"
    "</PubmedArticleSet>"
)
try:
    parse_record(ET.fromstring(TWO_ARTICLES))
    assert False, "PubmedArticle이 2개인데 예외가 안 났다"
except PubmedCountError as e:
    assert e.count == 2, e.count

# 정상 케이스: PubmedArticle이 정확히 1개면 그대로 통과한다 (기존 무결성
# 파싱 테스트가 이미 이 경로를 쓰지만, 개수 확인 자체를 명시적으로 한 번 더 본다)
ONE_ARTICLE = (
    "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>"
    "<Journal><Title>C</Title></Journal>"
    "<Abstract><AbstractText>Some abstract text.</AbstractText></Abstract>"
    "<ArticleTitle>Title C.</ArticleTitle>"
    "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
)
assert parse_record(ET.fromstring(ONE_ARTICLE))["abstract"] == "Some abstract text. Title C."


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


# ══════════════════════════════════════════════════════════════════
# main() 통합 테스트 — tests/fixtures.md "판정 기준 > 필수 통과"에서 가져온
# main()에 해당하는 항목들.
#
# 모킹 대상: verify_evidence.fetch_record (fetch_abstract가 아니다 — 그런
# 이름의 함수는 모듈에 없다. 실제 시그니처는
# fetch_record(pmid: str, api_key: str | None = None, retries: int = 2)이고,
# main()은 이걸 fetch_record(pmid, api_key)로 호출한다. 철회 판정도
# parse_record가 만들어 fetch_record가 돌려주는 record["integrity"]에서
# 나오므로, 이 함수 하나만 갈아끼우면 네트워크 조회 경로 전체가 막힌다.
# ══════════════════════════════════════════════════════════════════


def make_record(abstract, retracted=False, retraction_pmids=None,
                 year="2024", journal="J Test", pub_types=None):
    """fetch_record가 정상 조회 시 돌려주는 것과 같은 모양의 딕셔너리."""
    return {
        "abstract": abstract,
        "abstract_sha256": "0" * 64,
        "year": year,
        "journal": journal,
        "pub_types": pub_types or [],
        "integrity": {
            "retracted": retracted,
            "retraction_pmids": retraction_pmids or [],
            "has_erratum": False,
            "erratum_pmids": [],
            "expression_of_concern": False,
            "coi_statement": None,
            "coi_available": False,
        },
    }


def effect_slots(value, quote, **kw):
    return {"effect": dict(value=value, quote=quote, **kw)}


def run_main(records, fake_fetch, meta=None):
    """main()을 임시 디렉터리에서 실행하고 (종료코드, 팩, stderr)를 돌려준다.

    tempfile.TemporaryDirectory로 입출력을 격리해 레포에 파일을 남기지 않는다
    (with 블록을 벗어나면 자동 삭제된다). fetch_record를 몫킹해 네트워크를
    완전히 차단한다 — 이 스크립트는 기내모드에서 돌아야 하는 계약이 파일
    상단 docstring에 있다.
    """
    with tempfile.TemporaryDirectory() as td:
        input_path = os.path.join(td, "raw_slots.json")
        output_path = os.path.join(td, "evidence-pack.json")
        with open(input_path, "w", encoding="utf-8") as f:
            json.dump(records, f)

        argv = ["verify_evidence.py", "--input", input_path, "--output", output_path]
        if meta is not None:
            meta_path = os.path.join(td, "meta.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f)
            argv += ["--meta", meta_path]

        old_argv, old_fetch = sys.argv, verify_evidence.fetch_record
        sys.argv = argv
        verify_evidence.fetch_record = fake_fetch
        try:
            err_buf = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err_buf):
                code = verify_evidence.main()
        finally:
            sys.argv = old_argv
            verify_evidence.fetch_record = old_fetch

        pack = None
        if os.path.exists(output_path):
            with open(output_path, encoding="utf-8") as f:
                pack = json.load(f)
        return code, pack, err_buf.getvalue()


EFFECT_ABS = "Triglycerides decreased by -0.34 mmol/L compared with placebo (P = 0.02)."
DOSE_ABS = "Subjects received 500 mg magnesium daily for 8 weeks."
PCT_ABS = "Risk was reduced by 23% versus placebo (P = 0.01)."
KG_ABS = "Weight decreased by 2.1 kg from baseline."
RETRACTED_ABS = "Onset latency decreased by 17.36 min."

# fixtures.md: "입력 레코드가 0건이면 종료 코드 2를 내고 팩 파일을 만들지 않는다"
code, pack, err = run_main([], fake_fetch=lambda pmid, api_key=None, retries=2: {})
assert code == 2, (code, err)
assert pack is None, "0건 입력인데 팩 파일이 생성됐다"


def fetch_boundary_1(pmid, api_key=None, retries=2):
    if pmid == "11111":
        return make_record(EFFECT_ABS)
    raise RuntimeError("simulated fetch failure")


# fixtures.md: "제외 편수(조회 실패+초록 없음+철회)가 수록 편수 이상이면
# 종료 코드 2" — 경계값. 성공1 + 제외1 → 1 >= 1 → 2
code, pack, err = run_main(
    [{"pmid": "11111", "slots": effect_slots("-0.34", EFFECT_ABS, unit="mmol/L")},
     {"pmid": "88888", "slots": {}}],
    fake_fetch=fetch_boundary_1,
)
assert code == 2, (code, pack, err)
assert pack is not None, "종료코드 2여도 main()은 팩을 쓴 뒤 반환한다"
assert len(pack["papers"]) == 1, pack
assert pack["verification"]["fetch_failed_pmids"] == ["88888"], pack["verification"]


def fetch_boundary_2(pmid, api_key=None, retries=2):
    if pmid == "88888":
        raise RuntimeError("simulated fetch failure")
    return make_record(EFFECT_ABS if pmid == "11111" else DOSE_ABS)


# 같은 경계의 반대쪽. 성공2 + 제외1 → 1 >= 2 → False → 0
# fixtures.md: "정상 케이스는 종료 코드 0, 팩 파일 생성"도 이 케이스가 증명한다.
code, pack, err = run_main(
    [{"pmid": "11111", "slots": effect_slots("-0.34", EFFECT_ABS, unit="mmol/L")},
     {"pmid": "22222", "slots": {"dose": {"value": "500 mg", "quote": DOSE_ABS, "unit": "mg"}}},
     {"pmid": "88888", "slots": {}}],
    fake_fetch=fetch_boundary_2,
)
assert code == 0, (code, pack, err)
assert pack["schema"] == "evidence-pack/0.3", pack
assert len(pack["papers"]) == 2, pack
assert {p["pmid"] for p in pack["papers"]} == {"11111", "22222"}, pack
assert pack["verification"]["slots_verified"] >= 1, pack["verification"]


def fetch_with_retraction(pmid, api_key=None, retries=2):
    if pmid == "9500320":
        return make_record(RETRACTED_ABS, retracted=True, retraction_pmids=["20137807"])
    return make_record(EFFECT_ABS if pmid == "11111" else DOSE_ABS)


# fixtures.md: "철회 논문이 입력에 있으면 슬롯이 전부 유효해도 papers에 들어가지
# 않고 verification.retracted_pmids와 경고 첫 줄에 오른다"
# (회귀 확인용 PMID: 9500320 — fixtures.md가 지정한 값. 슬롯은 전부 유효한
# 형태로 채워서 "슬롯 검증을 통과해도"라는 조건을 실제로 만족시킨다)
code, pack, err = run_main(
    [{"pmid": "11111", "slots": effect_slots("-0.34", EFFECT_ABS, unit="mmol/L")},
     {"pmid": "22222", "slots": {"dose": {"value": "500 mg", "quote": DOSE_ABS, "unit": "mg"}}},
     {"pmid": "9500320", "slots": {"effect": {"value": "17.36", "quote": RETRACTED_ABS, "unit": "min"}}}],
    fake_fetch=fetch_with_retraction,
)
assert code == 0, (code, pack, err)
assert "9500320" not in {p["pmid"] for p in pack["papers"]}, "철회 논문이 papers에 들어갔다"
assert pack["verification"]["retracted_pmids"] == ["9500320"], pack["verification"]
assert pack["warnings"][0].startswith("철회된 논문"), pack["warnings"]
assert "9500320" in pack["warnings"][0], pack["warnings"][0]


def fetch_warnings(pmid, api_key=None, retries=2):
    return make_record(PCT_ABS if pmid == "33001" else KG_ABS)


# fixtures.md 판정 기준 + main()의 warnings 조립 로직: unit_type이 혼재하거나
# comparator가 null인 effect가 있으면 경고가 실제로 붙는지. 이 로직은
# verify_slot이 아니라 main()의 후처리(여러 논문의 effect를 모아 비교)에
# 있으므로 단위 테스트로는 못 잡고 main()으로 확인해야 한다.
# PCT: value에 "%"가 있어 unit_type=percent, comparator="placebo"(유효값)
# KG: unit="kg"뿐이라 unit_type=absolute, comparator 미제공 → null
code, pack, err = run_main(
    [{"pmid": "33001", "slots": effect_slots("23%", PCT_ABS, comparator="placebo")},
     {"pmid": "33002", "slots": effect_slots("2.1", KG_ABS, unit="kg")}],
    fake_fetch=fetch_warnings,
)
assert code == 0, (code, pack, err)
assert any("단위 기준" in w for w in pack["warnings"]), \
    ("unit_type 혼재(percent/absolute) 경고가 없다", pack["warnings"])
assert any("comparator가 null" in w for w in pack["warnings"]), \
    ("comparator null 경고가 없다", pack["warnings"])


# ══════════════════════════════════════════════════════════════════
# S2: pmid 형식 검사. efetch의 id=는 쉼표 목록을 받으므로 pmid에 쉼표가
# 섞이면 두 논문 초록이 한 응답에 합쳐져서 B논문의 숫자가 A논문 PMID로
# 검증을 통과할 수 있다(실증됨). fetch_record가 아예 호출되지 않아야 한다 —
# urlencode로는 못 막으므로 진입부에서 형식을 검사해 막는 것이 유일한 방어다.
# ══════════════════════════════════════════════════════════════════

def fetch_should_not_be_called(pmid, api_key=None, retries=2):
    raise AssertionError(f"invalid_pmid인데 fetch_record가 호출됐다: pmid={pmid!r}")


code, pack, err = run_main(
    [{"pmid": "11111,22222", "slots": effect_slots("-0.34", EFFECT_ABS, unit="mmol/L")}],
    fake_fetch=fetch_should_not_be_called,
)
assert code == 2, (code, pack, err)  # 수록 0 + 제외 1 → 2
assert pack["papers"] == [], pack
assert pack["verification"]["invalid_pmids"] == ["11111,22222"], pack["verification"]

# 정상 케이스: 숫자만인 pmid는 그대로 조회된다 (위의 fetch_boundary_1/2 등이
# 이미 이를 증명한다 — "11111" 같은 순수 숫자 pmid로 정상 통과함)


# ══════════════════════════════════════════════════════════════════
# S5-3: 중복 pmid. 걸러내지 않으면 표에서 근거 편수가 부풀려진다.
# ══════════════════════════════════════════════════════════════════

_dup_calls = []


def fetch_dup(pmid, api_key=None, retries=2):
    _dup_calls.append(pmid)
    return make_record(EFFECT_ABS)


code, pack, err = run_main(
    [{"pmid": "77001", "slots": effect_slots("-0.34", EFFECT_ABS, unit="mmol/L")},
     {"pmid": "77001", "slots": effect_slots("-0.34", EFFECT_ABS, unit="mmol/L")}],
    fake_fetch=fetch_dup,
)
assert code == 0, (code, pack, err)
assert len(pack["papers"]) == 1, "중복 pmid가 두 번 들어갔다"
assert _dup_calls == ["77001"], "중복 pmid인데 fetch_record가 두 번 불렸다"

# 정상 케이스: 서로 다른 pmid 두 개는 모두 남는다 (위 fetch_boundary_2가 이미 증명)


# ══════════════════════════════════════════════════════════════════
# S5-4: 존재하지 않는 PMID는 not_found로 분류된다 (초록이 비어 있는
# no_abstract와는 다른 사유 — 논문 자체가 없다는 뜻이다).
# ══════════════════════════════════════════════════════════════════

def fetch_not_found(pmid, api_key=None, retries=2):
    if pmid == "99999999":
        raise NotFoundError(f"PMID {pmid}: 레코드가 존재하지 않는다")
    return make_record(EFFECT_ABS)


code, pack, err = run_main(
    [{"pmid": "99999999", "slots": {}}],
    fake_fetch=fetch_not_found,
)
assert code == 2, (code, pack, err)  # 수록 0 + 제외 1 → 2
assert pack["verification"]["not_found_pmids"] == ["99999999"], pack["verification"]
assert pack["verification"]["fetch_failed_pmids"] == [], \
    ("not_found가 fetch_failed와 섞였다", pack["verification"])

# 정상 케이스: 존재하는 pmid는 그대로 통과한다 (fetch_boundary류가 이미 증명)


# ══════════════════════════════════════════════════════════════════
# S3: 입력 스키마 가드와 종료 코드 계약(0/2만). 7가지 malformed 입력:
#   1) 없는 파일  2) 깨진 JSON  3) slots 컨테이너가 list
#   4) 개별 슬롯이 문자열  5) 개별 슬롯이 숫자  6) 개별 슬롯이 list
#   7) meta가 list
# 4)~6)은 verify_slot 단위 테스트(위 malformed_slot 블록)에서 이미 확인했다.
# 여기서는 main() 통합 레벨에서 크래시 없이 0 또는 2로 끝나는지 본다.
# ══════════════════════════════════════════════════════════════════

def run_main_bad_path(input_path, meta_path=None):
    """run_main()과 달리 --input에 임의 경로(없는 파일 등)를 그대로 넘긴다."""
    with tempfile.TemporaryDirectory() as td:
        output_path = os.path.join(td, "evidence-pack.json")
        argv = ["verify_evidence.py", "--input", input_path, "--output", output_path]
        if meta_path is not None:
            argv += ["--meta", meta_path]
        old_argv = sys.argv
        sys.argv = argv
        try:
            err_buf = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err_buf):
                code = verify_evidence.main()
        finally:
            sys.argv = old_argv
        pack = None
        if os.path.exists(output_path):
            with open(output_path, encoding="utf-8") as f:
                pack = json.load(f)
        return code, pack, err_buf.getvalue()


# 1) 없는 파일
code, pack, err = run_main_bad_path("/no/such/path/raw_slots.json")
assert code == 2, (code, err)
assert pack is None, "없는 파일인데 팩이 생성됐다"
assert err.strip(), "stderr 메시지가 없다"

# 2) 깨진 JSON
with tempfile.TemporaryDirectory() as _td:
    _bad_json_path = os.path.join(_td, "bad.json")
    with open(_bad_json_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    code, pack, err = run_main_bad_path(_bad_json_path)
assert code == 2, (code, err)
assert pack is None, "깨진 JSON인데 팩이 생성됐다"
assert err.strip(), "stderr 메시지가 없다"

# 7) meta가 list
with tempfile.TemporaryDirectory() as _td:
    _input_path = os.path.join(_td, "raw_slots.json")
    with open(_input_path, "w", encoding="utf-8") as f:
        json.dump([{"pmid": "55555",
                    "slots": effect_slots("-0.34", EFFECT_ABS, unit="mmol/L")}], f)
    _meta_path = os.path.join(_td, "meta.json")
    with open(_meta_path, "w", encoding="utf-8") as f:
        json.dump(["not", "a", "dict"], f)
    code, pack, err = run_main_bad_path(_input_path, meta_path=_meta_path)
assert code == 2, (code, err)
assert pack is None, "meta가 list인데 팩이 생성됐다"
assert err.strip(), "stderr 메시지가 없다"

# 3) slots 컨테이너가 list — 레코드째 제외되고 (유일한 레코드이므로) 종료 코드 2
code, pack, err = run_main(
    [{"pmid": "56001", "slots": ["not", "a", "dict"]}],
    fake_fetch=lambda pmid, api_key=None, retries=2: make_record(EFFECT_ABS),
)
assert code == 2, (code, pack, err)
assert pack["papers"] == [], pack
assert pack["verification"]["malformed_slots_pmids"] == ["56001"], pack["verification"]

# 정상 케이스: 개별 슬롯 하나가 malformed여도 나머지 슬롯과 논문 자체는 살아
# 남는다 (컨테이너 자체는 정상 dict이므로) — 종료 코드는 0이다.
code, pack, err = run_main(
    [{"pmid": "56002", "slots": {"effect": "not a dict",
                                  "dose": {"value": "500 mg", "quote": DOSE_ABS, "unit": "mg"}}}],
    fake_fetch=lambda pmid, api_key=None, retries=2: make_record(DOSE_ABS),
)
assert code == 0, (code, pack, err)
assert len(pack["papers"]) == 1, pack
_effect_out = pack["papers"][0]["slots"]["effect"]
assert not _effect_out["verified"] and _effect_out["reason"] == "malformed_slot", _effect_out
_dose_out = pack["papers"][0]["slots"]["dose"]
assert _dose_out["verified"], _dose_out


# ══════════════════════════════════════════════════════════════════
# R1: 문장 경계는 다음 글자를 **열거하지 않는다**.
#
# 이 자리는 두 번 뚫렸고 두 번 다 허용 목록이 짧아서였다.
#   [A-Z(]     → 숫자 시작을 놓침    ("…daily. 17.36 min was…")
#   [A-Z(0-9]  → 그리스문자·소문자를 놓침 ("…weeks. α-Tocopherol rose…")
# 자연어의 문장 첫 글자는 열린 집합이라 열거로는 끝나지 않는다. \S로 닫았다.
# 아래 케이스들은 **전부 같은 문장 쌍이고 둘째 문장 첫 글자만 다르다** —
# 어느 하나라도 통과하면 그 글자 종류가 통째로 구멍이라는 뜻이다.
# ══════════════════════════════════════════════════════════════════

for _head in ["17.36 min", "Tocopherol", "α-Tocopherol", "β-Glucan", "ω-3 intake",
              "p27 expression", "the mean value", "'quoted' text", "[bracketed] text"]:
    _q = f"Subjects received 500 mg magnesium daily. {_head} was the mean reduction."
    assert has_multiple_sentences(_q), _head
    # 초록은 min인데 카드에는 mg으로 찍히던 경로
    r = verify_slot("effect", slot("17.36", _q, unit="mg"), normalize(_q))
    assert not r["verified"] and r["reason"] == "quote_multiple_sentences", (_head, r)
    assert r["value"] is None, (_head, r)

# 오탐 방지: ABBREV_TAIL에 있는 약어 뒤는 무엇이 오든 문장 경계가 아니다
for abbr_digit in [
    "Results reported by Smith et al. 2020 showed a 23.4% reduction overall.",
    "See protocol No. 3 for the randomization details used in this trial.",
    "The outcome is summarized in Fig. 2 of the supplementary material.",
    "Treatment vs. 500 mg placebo showed a mean difference of -0.34 mmol/L.",
    "Statins (e.g. atorvastatin) reduced LDL by 23.4% compared with placebo.",
]:
    assert not has_multiple_sentences(abbr_digit), abbr_digit

# \S의 대가: ABBREV_TAIL에 **없는** 약어는 이제 경계로 오인된다. 멀쩡한
# 단일 문장이 quote_multiple_sentences로 폐기된다는 뜻이다 — 값이 새는
# 것이 아니라 값을 잃는 쪽이므로 감수한다(fail-closed). 목록을 넓혀
# 되살리려 하면 그때마다 진짜 경계도 같이 숨는다. 이 계약을 고정해 둔다.
for _known_false_reject in [
    "Patients received 10 mg i.v. administration once daily for 8 weeks.",
    "Dosing was p.o. twice daily in the intervention arm of the trial.",
    "Enrollment was approx. 500 subjects across the participating sites.",
]:
    assert has_multiple_sentences(_known_false_reject), _known_false_reject


# ══════════════════════════════════════════════════════════════════
# R2 (C1b): quote가 초록 문장의 **앞을 잘라낸 조각**이면 폐기한다.
# 부분문자열 검사만으로는 부정어를 떼어낸 인용이 그대로 통과한다 —
# 초록이 부정한 결론이 긍정으로 뒤집혀 팩에 실린다.
# ══════════════════════════════════════════════════════════════════

NEG_ABS_RAW = ("Magnesium did not improve subjective sleep quality in this cohort. "
               "Supplementation did not reduce sleep onset latency by 17.36 min.")
NEG_ABS = normalize(NEG_ABS_RAW)

# conclusion: "did not improve" → "improve" 로 뒤집히던 경로
r = verify_quote_slot("conclusion",
                      {"quote": "improve subjective sleep quality in this cohort."},
                      NEG_ABS)
assert not r["verified"] and r["reason"] == "quote_not_at_sentence_start", r
assert r["quote"] is None, r

# effect도 같은 방식으로 뒤집힌다 — 그래서 전 슬롯에 건다
r = verify_slot("effect",
                slot("17.36", "reduce sleep onset latency by 17.36 min.", unit="min"),
                NEG_ABS)
assert not r["verified"] and r["reason"] == "quote_not_at_sentence_start", r
assert r["value"] is None and r["rejected_value"] == "17.36", r

# 문장 전체를 그대로 인용하면 통과한다 (부정문이어도 폐기 사유가 아니다 —
# direction 판정은 miner 몫이고, 게이트는 실재 여부만 본다)
r = verify_quote_slot("conclusion",
                      {"quote": "Magnesium did not improve subjective sleep quality in this cohort."},
                      NEG_ABS)
assert r["verified"], r

# 구조화 초록: efetch가 "CONCLUSIONS: text"로 이어 붙이므로 라벨을 뺀 quote는
# ": " 뒤에서 시작한다. 이걸 경계로 안 보면 정상 인용이 전부 폐기된다.
LABELED_ABS = normalize(
    "METHODS: Subjects received 500 mg magnesium daily for 8 weeks. "
    "CONCLUSIONS: Supplementation appears to improve insomnia severity scores."
)
r = verify_quote_slot("conclusion",
                      {"quote": "Supplementation appears to improve insomnia severity scores."},
                      LABELED_ABS)
assert r["verified"], r
r = verify_slot("dose", slot("500", "Subjects received 500 mg magnesium daily for 8 weeks.",
                             unit="mg"), LABELED_ABS)
assert r["verified"], r


# ══════════════════════════════════════════════════════════════════
# R3: 종료 코드 계약(0/2). 입력 배열의 원소가 객체가 아니면 rec.get()이
# AttributeError로 죽어 계약에 없는 1이 나갔다.
# ══════════════════════════════════════════════════════════════════

# 제외 1편이 수록 2편보다 적어야 block으로 넘어가지 않는다 — 여기서 보려는
# 것은 "미포착 예외로 죽지 않는가"이므로 제외 경계와 겹치지 않게 둔다.
code, pack, err = run_main(
    ["문자열 레코드",
     {"pmid": "57001", "slots": {"dose": {"value": "500 mg", "quote": DOSE_ABS, "unit": "mg"}}},
     {"pmid": "57002", "slots": {"dose": {"value": "500 mg", "quote": DOSE_ABS, "unit": "mg"}}}],
    fake_fetch=lambda pmid, api_key=None, retries=2: make_record(DOSE_ABS),
)
assert code == 0, (code, pack, err)
assert pack["verification"]["malformed_records"] == 1, pack["verification"]
assert len(pack["papers"]) == 2, pack
assert any(x["reason"] == "malformed_record" for x in pack["verification"]["excluded"]), pack

# 출력 경로가 쓸 수 없는 곳이어도 1이 아니라 2다
_old_argv, _old_fetch = sys.argv, verify_evidence.fetch_record
with tempfile.TemporaryDirectory() as _td:
    _in = os.path.join(_td, "raw.json")
    with open(_in, "w", encoding="utf-8") as f:
        json.dump([{"pmid": "57002",
                    "slots": {"dose": {"value": "500 mg", "quote": DOSE_ABS, "unit": "mg"}}}], f)
    sys.argv = ["verify_evidence.py", "--input", _in,
                "--output", os.path.join(_td, "없는디렉토리", "pack.json")]
    verify_evidence.fetch_record = lambda pmid, api_key=None, retries=2: make_record(DOSE_ABS)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            _code = verify_evidence.main()
    finally:
        sys.argv, verify_evidence.fetch_record = _old_argv, _old_fetch
assert _code == 2, _code


# ══════════════════════════════════════════════════════════════════
# R4: verdict — 종료 코드 0이 "폐기 없음"과 "절반 폐기"를 같은 값으로
# 덮던 것을 호출자가 필드로 가를 수 있는가.
# ══════════════════════════════════════════════════════════════════

# pass: 폐기·제외·무결성 이슈가 하나도 없고 comparator까지 확정된 경우
code, pack, err = run_main(
    [{"pmid": "58001", "slots": effect_slots("-0.34", EFFECT_ABS, unit="mmol/L",
                                             comparator="placebo")}],
    fake_fetch=lambda pmid, api_key=None, retries=2: make_record(EFFECT_ABS),
)
assert code == 0, (code, pack, err)
assert pack["verdict"] == "pass", (pack["verdict"], pack["verdict_reasons"])
assert pack["verdict_reasons"] == [], pack["verdict_reasons"]

# review: 슬롯이 폐기됐지만 팩 자체는 성립한다 → 종료 코드는 그대로 0
code, pack, err = run_main(
    [{"pmid": "58002", "slots": {"effect": {"value": "99.9", "quote": EFFECT_ABS,
                                            "unit": "mmol/L", "comparator": "placebo"},
                                 "dose": {"value": "500 mg", "quote": EFFECT_ABS}}}],
    fake_fetch=lambda pmid, api_key=None, retries=2: make_record(EFFECT_ABS),
)
assert code == 0, (code, pack, err)
assert pack["verdict"] == "review", (pack["verdict"], pack["verdict_reasons"])
assert any("폐기" in r for r in pack["verdict_reasons"]), pack["verdict_reasons"]
# 폐기값은 감사용으로 남고, 경고 문구도 그렇게 말한다 ("제거했다"가 아니다)
_eff = pack["papers"][0]["slots"]["effect"]
assert _eff["value"] is None and _eff["rejected_value"] == "99.9", _eff
assert any("rejected_value" in w for w in pack["warnings"]), pack["warnings"]

# block: 종료 코드 2와 같은 조건 — 제외가 수록 이상
code, pack, err = run_main(
    [{"pmid": "11111", "slots": effect_slots("-0.34", EFFECT_ABS, unit="mmol/L")},
     {"pmid": "88888", "slots": {}}],
    fake_fetch=fetch_boundary_2,
)
assert code == 2, (code, pack, err)
assert pack["verdict"] == "block", (pack["verdict"], pack["verdict_reasons"])

print("ok")
