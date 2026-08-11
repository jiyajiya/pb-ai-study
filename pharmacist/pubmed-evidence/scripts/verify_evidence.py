#!/usr/bin/env python3
"""
verify_evidence.py — pubmed-evidence P6 게이트

abstract-miner가 반환한 슬롯을 PubMed 초록과 대조해 검증한다.
LLM 판단은 이 단계에 개입하지 않는다.

검증 계약 (전부 통과해야 verified=true):
  C1. quote 가 PubMed 초록 원문에 부분문자열로 존재하고, 단일 문장이다
      — 두 문장짜리 quote 하나면 C2/C4가 quote 전체에서 검사하므로
        다른 문장의 숫자·단위가 슬롯 검사를 무력화한다
  C1b. quote 가 초록에서 **문장 시작 위치**에 놓여 있다
      — 부분문자열 검사만으로는 앞을 잘라낸 인용을 막지 못한다.
        "did not improve sleep quality" → "improve sleep quality" 는
        초록에 실재하는 문자열이면서 저자 결론을 정반대로 뒤집는다
  C2. value 가 quote 에 존재한다 — 숫자를 포함하면 수 경계까지 일치해야 한다
      — 부분문자열 검사는 "-0.34"를 "0.34"(부호 소실)·"34"(100배)로 통과시킨다
      — %도 수 경계다. "23%"에서 "23"만 떼어 오면 "23% 감소"가 사라진다
  C3. (effect 슬롯 한정) value 가 숫자 하나다 — 부호·소수점·% 까지만 허용
      — 단위는 unit, 지표명은 measure, p값은 significance 로 간다.
        "무엇이 효과 크기가 아닌가"를 열거하면 표현이 새 나올 때마다 구멍이
        생긴다. "무엇이 효과 크기인가"는 닫힌 형태라 열거가 끝난다
  C4. unit 이 quote 에 부분문자열로 존재한다
      — value만 검사하면 "17.36 min"을 "17.36 hours"로 바꿔 적어도 통과한다
  C5. (conclusion 슬롯 한정) quote 만 검사한다 — C1·C1b 를 적용한다
      — value·unit이 없는 슬롯이다. 저자 결론 문장 자체가 산출물이다
      — C1b가 가장 중요한 슬롯이다. 숫자는 사람이 이상하다고 느끼지만
        앞이 잘린 결론 문장은 그대로 읽힌다

무결성 정보(철회·정오표·이해충돌·연도·저널·PublicationType)는 검증이 아니라
같은 efetch 응답에서 기계적으로 읽어 옮긴 것이다. 추가 조회는 하지 않는다.
철회 논문은 슬롯이 전부 통과하더라도 팩에서 제외한다 — 검증은 "그 초록에
그 값이 있다"만 보증하므로 철회를 걸러내지 못한다.

C1은 초록을 스크립트가 독립적으로 재조회해 확인하므로,
에이전트가 quote를 지어내면 통과할 수 없다.
C3은 C1/C2를 통과하는 "원문에 실재하지만 효과 크기가 아닌" 문자열
(예: "p < 0.05")을 막는다. 프롬프트 규칙만으로는 새어 나온다.

의존성: 표준 라이브러리만 사용.

사용:
  python3 verify_evidence.py --input raw_slots.json --output evidence-pack.json
  python3 verify_evidence.py --input raw_slots.json --meta meta.json --output pack.json

종료 코드:
  0  팩 생성 완료 (슬롯 폐기는 정상 흐름이므로 0이다. 개수는 stdout·팩에 있다)
  2  팩을 신뢰할 수 없음 — 입력이 비었거나, 검증 불가 논문이 성공한 논문 이상,
     또는 입력 파일·JSON·슬롯 스키마가 애초에 읽을 수 없는 형태다
  이 둘 외의 종료 코드는 내지 않는다. 미포착 예외로 1이 나가는 경로가 있으면
  그건 계약 위반이므로 버그다.

팩의 verdict (pass | review | block):
  종료 코드 0이 "폐기 슬롯 없음"과 "슬롯 절반이 폐기됨"을 같은 값으로 덮는다.
  호출자가 산문 경고 대신 읽을 필드를 따로 둔다.
    pass    게이트가 걸 것을 다 걸었고 남은 것이 없다
    review  사람이 봐야 할 것이 있다 (폐기 슬롯·제외 논문·철회·정오표·
            design 불일치·comparator 미상). 종료 코드는 0이다
    block   팩을 신뢰할 수 없다. 종료 코드 2와 같은 조건이다
  **verdict는 "발행해도 되는가"가 아니다.** 광고법 검토(P8)는 이 도구 밖이고
  항상 미완이라 verdict에 넣지 않았다 — 넣으면 모든 팩이 영원히 review가 되어
  필드가 신호를 잃는다. pass여도 발행 전 사람 검토는 그대로 남는다.
"""

import argparse
import hashlib
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
# value가 없고 quote만 있는 슬롯. 검증 계약이 C1 하나뿐이라 따로 돈다.
QUOTE_SLOTS = ("conclusion",)
COMPARATORS = frozenset({"placebo", "active", "baseline"})
# 지표명. value에서 분리해 두어야 value가 "숫자 하나"라는 닫힌 형태를 유지한다.
# "RR 0.78"을 통째로 value에 넣으면 C3가 프로즈를 허용해야 하고,
# 그 순간 "p for trend = 0.03"도 같이 들어온다.
MEASURES = frozenset({"rr", "or", "hr", "smd", "md"})

# NLM 색인자가 붙인 PublicationType은 miner의 design 판정과 독립적으로 만들어진다.
# 그래서 대조할 수 있다. 단 태그가 없는 레코드가 흔하므로(2013년 RCT가
# 'Journal Article'만 달고 있는 식) 부재를 불일치로 읽으면 안 된다.
PT_DESIGN = {
    "meta-analysis": "meta-analysis",
    "systematic review": "systematic-review",
    "randomized controlled trial": "rct",
    "observational study": "observational",
    "review": "review",
}
# 'Review'는 서술형 리뷰와 체계적 문헌고찰을 구분하지 못하므로 단독으로는
# 판정 근거가 약하다. 나머지만 설계를 특정한다.
STRONG_DESIGNS = frozenset({"meta-analysis", "systematic-review", "rct", "observational"})

# 값이 실재하지 않거나 효과 크기가 아니어서 버린 경우. 추출 자체가 없었던
# not_extracted / missing_value_or_quote / missing_quote 는 폐기가 아니라 정보다.
REJECT_REASONS = frozenset({
    "quote_not_in_abstract", "value_not_in_quote",
    "p_value_not_effect_size", "ci_only_not_effect_size",
    "no_numeric_effect", "effect_value_not_numeric", "unit_not_in_quote",
    "quote_multiple_sentences", "quote_not_at_sentence_start", "malformed_slot",
})

# 대조 전 정규화 대상: 유니코드 대시/공백/따옴표류
DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
QUOTES = dict.fromkeys(map(ord, "\u2018\u2019\u201b\u2032"), "'")
DQUOTES = dict.fromkeys(map(ord, "\u201c\u201d\u201f\u2033"), '"')


# C3의 판정. effect의 value는 숫자 하나여야 한다 — 부호·소수점·천단위·% 까지.
# 이전에는 "효과 크기가 아닌 것"(p값·신뢰구간·서술어)을 걷어낸 뒤 숫자가
# 남는지 보는 차감식이었다. 그 방식은 걷어낼 표현을 열거해야 하는데
# 자연어 표현은 열려 있어서 "p for trend", "p values ranged from" 처럼
# 새 표현이 나올 때마다 구멍이 생겼다. 반대로 "숫자 하나"는 닫힌 형태다.
EFFECT_VALUE = re.compile(r"^[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?$")

# 아래 둘은 이제 판정에 쓰이지 않는다. 폐기 사유를 사람이 읽을 수 있게
# 라벨을 붙이는 용도다 — 못 맞혀도 폐기 결정은 이미 EFFECT_VALUE가 내렸다.
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

# C2: 숫자가 든 값은 "다른 수의 일부"로 매칭되면 안 된다.
# 부분문자열 검사는 "-0.34"의 부호를 지운 "0.34", 자릿수를 자른 "34"·"17",
# 반올림한 "17"을 전부 통과시킨다 — C4가 unit 쪽에서 막은 것과 같은 유형의
# 구멍이 value 쪽에 남아 있었다. 부호 반전과 자릿수 변경은 카드에 실리는
# 순간 그대로 오정보가 되므로 수 경계로 본다.
NUM_EDGE_L = r"(?<![0-9.,\-])"
# %도 경계 문자다. "reduced by 23%"에서 value="23"(unit=None)이면 %가 없어도
# 이전 경계식은 "23" 뒤가 숫자도 [.,]숫자도 아니라는 이유로 통과시켰다.
# "23% 감소"와 "23 감소"는 다른 주장이므로 % 앞에서 끊긴 매칭도 막는다.
NUM_EDGE_R = r"(?!%|[0-9]|[.,][0-9])"


def value_in_quote(value_norm: str, quote_norm: str) -> bool:
    """값이 quote 안에 있는가. 숫자를 포함하면 수 경계까지 맞아야 한다."""
    if not HAS_DIGIT.search(value_norm):
        return value_norm in quote_norm
    return re.search(NUM_EDGE_L + re.escape(value_norm) + NUM_EDGE_R, quote_norm) is not None


# C1: quote는 한 문장이어야 한다. 문장 경계가 있으면 두 번째 문장의 숫자·단위가
# quote_norm 전체에서 검사하는 C2/C4를 무력화한다 — "500 mg magnesium daily.
# Sleep onset latency decreased by 17.36 min."에서 effect value=17.36,
# unit=mg가 그대로 통과하던 것이 이 유형이다. abstract-miner.md가 이미
# "한 문장만"을 프롬프트 계약으로 요구한다. 여기서는 그 계약을 기계로 집행한다.
# 문장부호 + 공백 뒤에 공백 아닌 글자가 오면 경계다. **다음 글자가 무엇인지
# 열거하지 않는다.**
#
# 이 자리는 두 번 뚫렸고 두 번 다 같은 이유였다. 처음엔 [A-Z(]라서 둘째 문장이
# 숫자로 시작하면("…daily. 17.36 min was…") 안 잡혔고, 그걸 [A-Z(0-9]로 고치자
# 이번엔 그리스문자와 소문자가 남았다("…12 weeks. α-Tocopherol rose by…",
# "…weeks. p27 expression fell…"). 하필 이 도메인이다 — α-토코페롤·β-글루칸·
# ω-3는 고시 등재명이고 영문 초록에서 문장 첫머리에 온다.
#
# 허용 목록을 늘리는 방식이 틀렸다. C3에서 차감식을 버리고 "숫자 하나"라는
# 닫힌 형태로 뒤집은 것과 같은 교훈인데 여기에는 적용하지 않고 있었다.
# 자연어의 문장 첫 글자는 열린 집합이므로 \S로 닫는다.
#
# 소수점은 걸리지 않는다 — "0.5"에는 마침표 뒤 공백이 없다. 약어는
# ABBREV_TAIL이 따로 걷어낸다.
SENTENCE_BREAK = re.compile(r"[.!?]\s+\S")
# 이 약어들 뒤의 마침표는 문장 끝이 아니다. 초록에 흔히 나오는 것만 넣는다 —
# 목록을 넓히면 진짜 문장 경계도 같이 숨어버린다.
ABBREV_TAIL = re.compile(
    r"(?:^|[\s(])(?:e\.g|i\.e|vs|et al|no|fig|dr)\.$", re.IGNORECASE
)


def has_multiple_sentences(quote: str) -> bool:
    """quote 안에 (약어를 제외한) 문장 경계가 있으면 True.

    정규화 전 원문에 대해 검사해야 한다. normalize()는 소문자로 바꾸므로
    대문자 판정([A-Z])이 정규화된 문자열에서는 항상 거짓이 되어 버린다.
    """
    for m in SENTENCE_BREAK.finditer(quote):
        if ABBREV_TAIL.search(quote[: m.start() + 1]):
            continue
        return True
    return False


# C1b: quote가 초록 문장의 **시작**에서 떨어져 있어야 한다.
#
# 부분문자열 검사만으로는 앞을 잘라낸 인용을 막지 못한다. 초록이
# "Magnesium did not improve sleep quality."인데 quote를 "improve sleep
# quality."로 내면 C1(⊂ 초록)도 C1(단일 문장)도 통과한다 — 저자가 부정한
# 결론이 긍정으로 뒤집혀 팩에 실린다. 자릿수 오류보다 나쁘다. 숫자는
# 사람이 이상하다고 느끼지만 뒤집힌 문장은 그대로 읽히기 때문이다.
#
# conclusion만의 문제가 아니다. "did not reduce latency by 17.36 min"에서
# 앞을 자르면 effect도 같은 방식으로 뒤집힌다. 그래서 전 슬롯에 건다.
#
# 경계로 인정하는 것: 초록의 맨 앞, 문장부호(. ! ? ;), 그리고 콜론 —
# efetch가 구조화 초록을 "CONCLUSIONS: text" 형태로 이어 붙이므로,
# 라벨을 뺀 quote는 ": " 뒤에서 시작한다. 이걸 경계로 안 보면 정상
# 인용이 전부 폐기된다.
QUOTE_ANCHOR = re.compile(r"(?:^|[.!?;:])\s*$")


def quote_at_sentence_start(quote_norm: str, abstract_norm: str) -> bool:
    """quote가 초록 안에서 문장 시작 위치에 놓인 곳이 한 군데라도 있는가.

    같은 문자열이 초록에 여러 번 나올 수 있으므로 모든 출현을 본다.
    한 곳이라도 문장 시작이면 통과다 — 인용은 원문에 실재하는 문장이면
    되고, 어느 출현인지까지 특정할 근거는 없다.
    """
    start = 0
    while True:
        i = abstract_norm.find(quote_norm, start)
        if i < 0:
            return False
        if i == 0 or QUOTE_ANCHOR.search(abstract_norm[:i]):
            return True
        start = i + 1


def effect_shape_reason(value_norm: str) -> str | None:
    """effect 값이 숫자 하나가 아니면 폐기 사유를, 맞으면 None을 반환한다.

    p값은 "우연이 아니다"라는 판정이지 크기가 아니므로 카드에 쓸 수 없다.
    신뢰구간도 점추정치 없이 구간만 있으면 카드에 쓸 숫자가 없는 것이다.
    단위는 unit, 지표명(RR·OR·HR·SMD·MD)은 measure, p값은 significance로
    간다 — 그래서 value에 남는 것은 숫자뿐이다.
    """
    if EFFECT_VALUE.match(value_norm):
        return None
    # 여기서부터는 폐기가 확정됐다. 아래는 사람이 읽을 라벨을 고르는 것뿐이라
    # 못 맞혀도 판정은 바뀌지 않는다. p값/CI를 걷어내고도 숫자가 남으면
    # "숫자는 맞는데 value에 다른 것이 섞였다"는 뜻이다.
    if not HAS_DIGIT.search(value_norm):
        return "no_numeric_effect"
    if HAS_DIGIT.search(P_TOKEN.sub(" ", CI_TOKEN.sub(" ", value_norm))):
        return "effect_value_not_numeric"
    if P_TOKEN.search(value_norm):
        return "p_value_not_effect_size"
    return "ci_only_not_effect_size"


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


class PubmedCountError(ValueError):
    """efetch 응답의 PubmedArticle 개수가 1이 아니다.

    0개는 존재하지 않는 PMID다 (efetch가 빈 PubmedArticleSet을 반환한다).
    2개 이상은 id= 파라미터에 쉼표로 여러 PMID가 섞여 두 논문 초록이 한
    응답에 합쳐진 경우다 — fetch_record 진입부의 pmid 형식 검사(숫자만
    허용)로 대부분 막히지만, 응답 형태로도 한 번 더 막는다. root.iter()로
    긁으면 PubmedArticle이 몇 개든 구분하지 않고 다 섞어 버리므로, 이
    개수 확인이 없으면 B논문의 숫자가 A논문 PMID로 검증을 통과할 수 있다.
    """

    def __init__(self, count: int):
        self.count = count
        super().__init__(f"PubmedArticle 개수가 1이 아니다: {count}개")


class NotFoundError(RuntimeError):
    """efetch가 빈 PubmedArticleSet을 반환했다 — 존재하지 않는 PMID.

    fetch_failed(조회 실패)와 구분한다. 재시도해도 응답은 계속 비어 있으므로
    다른 실패 사유와 같은 재시도 취급을 하면 무의미한 대기만 늘어난다.
    """


def parse_record(root: ET.Element) -> dict:
    """efetch 응답에서 초록과 무결성 정보를 읽는다. 판단은 하지 않는다.

    전부 한 응답 안에 있으므로 추가 조회 비용이 없다. 여기서 읽지 않으면
    같은 정보를 사람이 PubMed에서 눈으로 다시 확인해야 한다.

    PubmedArticle이 정확히 1개가 아니면 PubmedCountError를 낸다 — 이 함수는
    "이 응답은 정확히 한 논문의 것"이라는 전제 위에서만 안전하다.
    """
    articles = root.findall("PubmedArticle")
    if len(articles) != 1:
        raise PubmedCountError(len(articles))

    parts = []
    for node in root.iter("AbstractText"):
        label = node.get("Label")
        text = "".join(node.itertext())
        parts.append(f"{label}: {text}" if label else text)
    if parts:
        # 초록이 있을 때만 제목을 붙인다. 제목만으로 대조하면 전부 환각으로 오분류된다
        for node in root.iter("ArticleTitle"):
            parts.append("".join(node.itertext()))
    abstract = " ".join(parts)

    pub_types = [t for t in ((e.text or "").strip() for e in root.iter("PublicationType")) if t]

    refs: dict[str, list[str]] = {}
    for node in root.iter("CommentsCorrections"):
        pmid = (node.findtext("PMID") or "").strip()
        if pmid:
            refs.setdefault(node.get("RefType") or "", []).append(pmid)

    # CoiStatement는 요소가 있어도 내용이 빈 레코드가 흔하다. 빈 값을
    # "이해충돌 없음"으로 읽으면 안 되므로 원문과 존재 여부를 따로 둔다.
    coi = " ".join((root.findtext(".//CoiStatement") or "").split())

    year = None
    pubdate = root.find(".//Article/Journal/JournalIssue/PubDate")
    if pubdate is not None:
        year = (pubdate.findtext("Year") or "").strip() or None
        if not year:
            m = re.search(r"\d{4}", pubdate.findtext("MedlineDate") or "")
            year = m.group() if m else None

    journal = (root.findtext(".//Article/Journal/ISOAbbreviation")
               or root.findtext(".//Article/Journal/Title") or "").strip()

    return {
        "abstract": abstract,
        # 초록 전문은 저장하지 않는다. 해시만 남기면 재검증 때 "그때 그 초록인가"를
        # 1회 조회로 판정할 수 있다. 조립 방식이 바뀌면 값도 바뀐다.
        "abstract_sha256": hashlib.sha256(abstract.encode("utf-8")).hexdigest() if abstract else None,
        "year": year,
        "journal": journal or None,
        "pub_types": pub_types,
        "integrity": {
            "retracted": "Retracted Publication" in pub_types or bool(refs.get("RetractionIn")),
            "retraction_pmids": refs.get("RetractionIn", []),
            "has_erratum": bool(refs.get("ErratumIn")),
            "erratum_pmids": refs.get("ErratumIn", []),
            "expression_of_concern": bool(refs.get("ExpressionOfConcernIn")),
            "coi_statement": coi or None,
            "coi_available": bool(coi),
        },
    }


def design_check(design: str | None, pub_types: list[str]) -> dict:
    """miner의 design 판정을 NLM PublicationType과 대조한다.

    태그가 없으면 `agrees: null`이다. 부재는 불일치가 아니다.
    """
    implied = {PT_DESIGN[t] for t in (p.lower() for p in pub_types) if t in PT_DESIGN}
    claimed = (design or "").strip().lower()
    if not implied:
        agrees = None
    elif implied & STRONG_DESIGNS:
        agrees = claimed in implied
    else:
        # 'Review' 태그뿐인 레코드. 원 시험 설계를 주장할 때만 불일치로 본다.
        agrees = claimed in {"review", "meta-analysis", "systematic-review"}
    return {"pub_types": pub_types, "implied": sorted(implied), "agrees": agrees}


def fetch_record(pmid: str, api_key: str | None = None, retries: int = 2) -> dict:
    """PubMed 레코드를 독립 조회한다. 에이전트 출력을 신뢰하지 않는다."""
    params = {"db": "pubmed", "id": pmid, "retmode": "xml", "rettype": "abstract"}
    if api_key:
        params["api_key"] = api_key
    url = f"{EUTILS}/efetch.fcgi?{urllib.parse.urlencode(params)}"

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "pubmed-evidence/0.1 (pharmacy content tool)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml = resp.read()
            return parse_record(ET.fromstring(xml))
        except PubmedCountError as e:
            if e.count == 0:
                raise NotFoundError(f"PMID {pmid}: 레코드가 존재하지 않는다") from e
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"PMID {pmid} 레코드 조회 실패: {last_err}")


def verify_slot(slot_name: str, slot: dict | None, abstract_norm: str) -> dict:
    """단일 슬롯 검증. 통과 못하면 value를 null로 만들고 사유를 남긴다."""
    if slot is None:
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "not_extracted"}
    # verify_quote_slot과 같은 이유로 필요한 가드: 에이전트가 dict 대신
    # 문자열·숫자·리스트를 낼 수 있다. 이 슬롯은 value/unit/measure 등을
    # 나눠 담는 계약이라 문자열 하나로는 되살릴 수 없다 — 그대로 폐기한다.
    if not isinstance(slot, dict):
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "malformed_slot"}

    value = slot.get("value")
    quote = slot.get("quote")

    if not value or not quote:
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "missing_value_or_quote"}

    # C1: quote는 한 문장이어야 한다. 원문(정규화 전) 케이스에 대해서만
    # 판정할 수 있다 — normalize()가 대문자를 지워 버리기 때문이다.
    if has_multiple_sentences(quote):
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "quote_multiple_sentences",
                "rejected_value": value, "rejected_quote": quote}

    quote_norm = normalize(quote)
    value_norm = normalize(value)

    # C1: quote ⊂ abstract
    if quote_norm not in abstract_norm:
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "quote_not_in_abstract",
                "rejected_value": value, "rejected_quote": quote}

    # C1b: quote가 문장 앞을 잘라낸 조각이면 폐기. 부정어를 떼어낸 인용이
    # 여기서 걸린다 — "did not reduce X by 17.36 min" → "reduce X by 17.36 min"
    if not quote_at_sentence_start(quote_norm, abstract_norm):
        return {"slot": slot_name, "value": None, "verified": False,
                "reason": "quote_not_at_sentence_start",
                "rejected_value": value, "rejected_quote": quote}

    # C2: value ⊂ quote (숫자는 수 경계까지)
    if not value_in_quote(value_norm, quote_norm):
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
    unit = slot.get("unit") or None  # ""는 값이 아니라 부재다. None으로 정규화한다.
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

        # measure도 열거 검증만 한다. quote 대조는 하지 않는다 —
        # 저자가 "relative risk"로 풀어 쓰면 "rr"은 초록에 없고, 반대로
        # "or"은 영어 접속사라 아무 문장에나 걸린다. 어느 쪽도 검사가 못 된다.
        # 숫자가 아니므로 틀려도 자릿수 오류로는 번지지 않는다.
        measure = normalize(slot.get("measure"))
        out["measure"] = measure if measure in MEASURES else None

        # significance는 헤드라인 숫자가 아니라 부가 정보다.
        # quote 밖이면 슬롯을 죽이지 않고 이 필드만 버린다 —
        # p값이 효과 문장과 다른 문장에 있는 정상 케이스에서 멀쩡한 슬롯이 죽는다.
        significance = slot.get("significance")
        if significance and value_in_quote(normalize(significance), quote_norm):
            out["significance"] = significance
        else:
            out["significance"] = None
            if significance:
                out["dropped_significance"] = significance
    return out


def verify_quote_slot(slot_name: str, slot: dict | None, abstract_norm: str) -> dict:
    """value 없는 슬롯(conclusion) 검증. C1만 적용한다.

    저자 결론 문장은 숫자가 아니라 문장 자체가 산출물이므로 C2~C4가 없다.
    이 슬롯이 통과해야 `direction` 판정에 대조 가능한 근거가 생긴다 —
    지금까지 direction은 miner의 판정만 있고 원문이 남지 않았다.
    """
    if slot is None:
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "not_extracted"}
    # 문장 하나짜리 슬롯이라 에이전트가 dict 대신 문자열을 낼 수 있다.
    if isinstance(slot, str):
        slot = {"quote": slot}
    if not isinstance(slot, dict):
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "missing_quote"}

    quote = slot.get("quote")
    if not quote:
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "missing_quote"}

    if has_multiple_sentences(quote):
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "quote_multiple_sentences", "rejected_quote": quote}

    quote_norm = normalize(quote)
    if quote_norm not in abstract_norm:
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "quote_not_in_abstract", "rejected_quote": quote}

    # C1b: 이 슬롯에서 가장 위험한 실패다. 결론 문장은 카피의 방향을 정하는데,
    # 앞의 부정어를 떼면 "did not improve"가 "improve"가 되어 정반대 카피가
    # 근거를 갖춘 것처럼 나간다. 숫자 슬롯과 달리 이상함을 느낄 단서도 없다.
    if not quote_at_sentence_start(quote_norm, abstract_norm):
        return {"slot": slot_name, "quote": None, "verified": False,
                "reason": "quote_not_at_sentence_start", "rejected_quote": quote}

    return {"slot": slot_name, "quote": quote, "verified": True}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True,
                    help="abstract-miner 결과 배열 JSON")
    ap.add_argument("--output", required=True, help="evidence-pack.json 경로")
    ap.add_argument("--meta", help="정규화·scout 결과 JSON (선택)")
    args = ap.parse_args()

    api_key = os.environ.get("NCBI_API_KEY")

    # 입력 파일과 JSON은 에이전트·사용자가 준 것이지 스크립트가 만든 것이
    # 아니다 — 파일이 없거나 JSON이 깨졌다고 미포착 예외로 죽으면 종료
    # 코드 1이 나가는데, 그건 이 스크립트의 종료 코드 계약(0/2)에 없는 값이다.
    try:
        with open(args.input, encoding="utf-8") as f:
            records = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"입력 파일을 읽을 수 없다: {args.input} ({e})", file=sys.stderr)
        return 2

    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        print(f"입력이 배열도 객체도 아니다 (type={type(records).__name__}).",
              file=sys.stderr)
        return 2

    if not records:
        print("입력에 레코드가 없다. 검증할 것이 없으므로 팩을 만들지 않는다.",
              file=sys.stderr)
        return 2

    meta = {}
    if args.meta:
        try:
            with open(args.meta, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"메타 파일을 읽을 수 없다: {args.meta} ({e})", file=sys.stderr)
            return 2
        if not isinstance(meta, dict):
            print(f"메타 파일이 객체가 아니다 (type={type(meta).__name__}).",
                  file=sys.stderr)
            return 2

    results = []
    excluded = []  # 팩에서 빠진 논문. 무엇이 빠졌는지가 남아야 팩을 읽을 수 있다
    rejected_count = 0
    fetch_failed = []
    no_abstract = []
    retracted = []
    invalid_pmid = []
    not_found = []
    malformed_slots = []
    # 레코드 자체가 객체가 아닌 경우. rec.get(...)이 미포착 예외로 죽으면
    # 종료 코드 1이 나가는데, 그건 이 스크립트의 계약(0/2)에 없는 값이다.
    malformed_records = 0
    seen: set[str] = set()  # 중복 pmid 제거 — 안 그러면 표에서 근거 편수가 부풀려진다

    for i, rec in enumerate(records):
        # 성공 경로에만 두면 조회가 연속 실패할 때 재시도 폭주가 된다.
        # E-utilities는 이럴 때 IP를 막는다.
        if i:
            time.sleep(0.35 if api_key else 0.4)

        # 입력 배열의 원소가 객체가 아닐 수 있다 (에이전트가 문자열 목록을
        # 냈거나 배열이 한 겹 더 감싸인 경우). rec.get()이 AttributeError로
        # 죽으면 팩도 안 생기고 종료 코드도 계약 밖인 1이 나간다.
        if not isinstance(rec, dict):
            print(f"[MALFORMED] index={i} 레코드가 객체가 아니다 "
                  f"(type={type(rec).__name__}). 제외한다.", file=sys.stderr)
            malformed_records += 1
            excluded.append({"pmid": None, "reason": "malformed_record",
                             "index": i})
            continue

        pmid = rec.get("pmid")
        if not pmid or rec.get("error"):
            fetch_failed.append(pmid)
            excluded.append({"pmid": pmid, "reason": rec.get("error") or "no_pmid"})
            continue

        # efetch의 id= 파라미터는 쉼표 목록을 받는다. pmid가 "11111,22222"면
        # 두 논문 초록이 한 응답에 합쳐져서 B논문의 숫자가 A논문 PMID로
        # 검증을 통과할 수 있다. urlencode는 쉼표를 이스케이프하지 않는다 —
        # 그건 efetch의 정상 기능이라 인코딩으로는 못 막는다.
        if not re.fullmatch(r"[0-9]+", str(pmid)):
            invalid_pmid.append(pmid)
            excluded.append({"pmid": pmid, "reason": "invalid_pmid"})
            continue

        if pmid in seen:
            excluded.append({"pmid": pmid, "reason": "duplicate_pmid"})
            continue
        seen.add(pmid)

        try:
            record = fetch_record(pmid, api_key)
        except NotFoundError as e:
            print(f"[NOT FOUND] {e}", file=sys.stderr)
            not_found.append(pmid)
            excluded.append({"pmid": pmid, "reason": "not_found"})
            continue
        except RuntimeError as e:
            print(f"[FETCH FAIL] {e}", file=sys.stderr)
            fetch_failed.append(pmid)
            excluded.append({"pmid": pmid, "reason": "fetch_failed"})
            continue

        integrity = record["integrity"]
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        # 철회 논문은 슬롯이 전부 검증을 통과해도 카드에 쓸 수 없다.
        # C1~C4는 "그 초록에 그 값이 있다"만 보므로 철회를 걸러내지 못한다.
        # 초록 유무보다 먼저 본다 — 철회 사실이 더 중요한 정보다.
        if integrity["retracted"]:
            print(f"[RETRACTED] pmid={pmid} 철회된 논문이다. 슬롯 전체를 버린다.",
                  file=sys.stderr)
            retracted.append(pmid)
            excluded.append({"pmid": pmid, "reason": "retracted",
                             "retraction_pmids": integrity["retraction_pmids"],
                             "url": url})
            continue

        if not record["abstract"].strip():
            print(f"[NO ABSTRACT] pmid={pmid} 초록이 없는 레코드다. 검증 불가로 제외.",
                  file=sys.stderr)
            no_abstract.append(pmid)
            excluded.append({"pmid": pmid, "reason": "no_abstract", "url": url})
            continue

        abstract_norm = normalize(record["abstract"])
        slots_in = rec.get("slots", {})
        # slots 필드 자체가 dict가 아니면 (예: 리스트) 아래 .get(name) 호출이
        # 미포착 예외로 죽는다. 레코드째 제외한다 — 어느 슬롯도 신뢰할 수 없다.
        if not isinstance(slots_in, dict):
            print(f"[MALFORMED] pmid={pmid} slots 필드가 dict가 아니다 "
                  f"(type={type(slots_in).__name__}). 레코드를 제외한다.",
                  file=sys.stderr)
            malformed_slots.append(pmid)
            excluded.append({"pmid": pmid, "reason": "malformed_slots"})
            continue

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
        for name in QUOTE_SLOTS:
            outcome = verify_quote_slot(name, slots_in.get(name), abstract_norm)
            if not outcome["verified"] and outcome["reason"] in REJECT_REASONS:
                rejected_count += 1
                print(f"[REJECT] pmid={pmid} slot={name} reason={outcome['reason']}",
                      file=sys.stderr)
            verified_slots[name] = outcome

        results.append({
            "pmid": pmid,
            "design": rec.get("design"),
            "direction": rec.get("direction"),
            "subgroup_only": rec.get("subgroup_only"),
            "year": record["year"],
            "journal": record["journal"],
            "integrity": integrity,
            "design_check": design_check(rec.get("design"), record["pub_types"]),
            "abstract_sha256": record["abstract_sha256"],
            "url": url,
            "slots": verified_slots,
        })

    excluded_count = (len(fetch_failed) + len(no_abstract) + len(retracted)
                       + len(invalid_pmid) + len(not_found) + len(malformed_slots)
                       + malformed_records)
    total = sum(1 for r in results for s in r["slots"].values())
    passed = sum(1 for r in results for s in r["slots"].values() if s["verified"])

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    warnings = [
        "이 팩은 근거 수집 결과일 뿐이며 광고법 검토를 거치지 않았다. "
        "성분 설명과 제품 효능 표방의 경계를 확인하기 전에 발행하지 말 것.",
        "verified=false 슬롯의 value는 제거했다. 폐기된 원값은 무엇이 왜 걸렸는지 "
        "추적할 수 있게 rejected_value·rejected_quote에 감사용으로 남아 있다 — "
        "검증을 통과하지 못한 값이므로 표·카피로 옮기지 말 것. 팩에 보인다는 것이 "
        "쓸 수 있다는 뜻이 아니다.",
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

    if excluded_count:
        warnings.append(
            f"{excluded_count}편이 팩에서 제외되었다 "
            f"(조회 실패 {len(fetch_failed)}, 초록 없음 {len(no_abstract)}, "
            f"철회 {len(retracted)}). "
            "이 팩은 검색된 근거 전체가 아니라 검증에 성공한 일부다."
        )

    # ── 무결성: 판정하지 않고 사실만 올린다 ──
    if retracted:
        warnings.insert(0, (
            f"철회된 논문 {len(retracted)}편을 제외했다 (PMID {', '.join(retracted)}). "
            "이 PMID는 인용하지 말고, 이미 발행한 콘텐츠에 쓰이지 않았는지 확인할 것."
        ))
    eoc = [r["pmid"] for r in results if r["integrity"]["expression_of_concern"]]
    if eoc:
        warnings.append(
            f"우려 표명(Expression of Concern)이 붙은 논문이 있다 (PMID {', '.join(eoc)}). "
            "철회는 아니지만 인용 전 사유를 확인할 것."
        )
    errata = [r["pmid"] for r in results if r["integrity"]["has_erratum"]]
    if errata:
        warnings.append(
            f"정오표(erratum)가 있는 논문이 있다 (PMID {', '.join(errata)}). "
            "초록의 수치가 정정 대상인지 원문에서 확인할 것."
        )
    coi_yes = sum(1 for r in results if r["integrity"]["coi_available"])
    if results:
        warnings.append(
            f"이해충돌 진술: 있음 {coi_yes}편, 없음 {len(results) - coi_yes}편. "
            "진술 원문(coi_statement)을 읽고 제조사 관련 여부는 사람이 판단할 것 — "
            "이 팩은 판단하지 않는다. 미공개는 '이해충돌 없음'이 아니라 "
            "PubMed가 받지 못했다는 뜻이다."
        )
    mismatched = [r["pmid"] for r in results if r["design_check"]["agrees"] is False]
    if mismatched:
        warnings.append(
            f"design 판정이 NLM PublicationType과 어긋나는 논문이 있다 "
            f"(PMID {', '.join(mismatched)}). 톤 판정이 design 위에 서 있으므로 확인할 것."
        )
    warnings.append(
        f"철회·정오표·이해충돌 정보는 {generated_at} 시점의 PubMed 스냅샷이다. "
        "철회는 나중에 일어나므로, 오래된 팩을 재사용할 때는 다시 조회할 것 "
        "(abstract_sha256으로 초록 변경 여부를 대조할 수 있다)."
    )

    # ── verdict — 산문 경고 대신 읽을 필드 ──
    #
    # 경고는 warnings 배열의 문장으로만 있었고, 그걸 읽는 쪽은 LLM이다.
    # 문장은 흘린다. 종료 코드도 0/2 둘뿐이라 "슬롯 몇 개가 폐기됐지만 팩은
    # 정상"과 "전부 통과"가 같은 0으로 수렴했다. 호출자가 분기에 쓸 값을 준다.
    #
    # **verdict는 "발행해도 되는가"를 판정하지 않는다.** 광고법 검토(P8)는
    # 이 도구 밖이고 항상 미완이므로 verdict에 넣지 않았다 — 넣으면 모든 팩이
    # 영원히 review가 되어 필드가 신호를 잃는다. pass는 "게이트가 걸 것을
    # 다 걸었고 남은 것이 없다"까지다.
    verdict_reasons = []
    if not results:
        verdict_reasons.append("팩에 수록된 논문이 0편이다")
    if excluded_count and excluded_count >= len(results):
        verdict_reasons.append(
            f"제외 {excluded_count}편이 수록 {len(results)}편 이상이라 지형을 대표하지 못한다")
    blocked = bool(verdict_reasons)

    if rejected_count:
        verdict_reasons.append(f"검증에 걸려 폐기된 슬롯이 {rejected_count}개 있다")
    if excluded_count and not blocked:
        verdict_reasons.append(f"{excluded_count}편이 팩에서 제외되었다")
    if retracted:
        verdict_reasons.append(f"철회 논문 {len(retracted)}편을 제외했다")
    if eoc or errata:
        verdict_reasons.append("우려 표명·정오표가 붙은 논문이 있다")
    if mismatched:
        verdict_reasons.append("design 판정이 PublicationType과 어긋나는 논문이 있다")
    if any(e.get("comparator") is None for e in effects):
        verdict_reasons.append("비교 대상(comparator)을 확정하지 못한 effect가 있다")

    verdict = "block" if blocked else ("review" if verdict_reasons else "pass")

    pack = {
        "schema": "evidence-pack/0.3",
        "verdict": verdict,
        "verdict_reasons": verdict_reasons,
        "topic": meta.get("topic"),
        # 식약처 고시 등재명. 검색어("오메가3")와 등재명("EPA 및 DHA 함유 유지")은
        # 다른 축이라 topic에서 유도할 수 없다. 이 값이 없으면 하류 kr-claims가
        # topic 앞부분으로 되돌아가고, 고시에 실재하는 기준을 미등재로 보고한다.
        # ""(공란)은 "고시형에 없음을 확인했다", null은 "확인하지 않았다"이다.
        "kr_notice_name": meta.get("kr_notice_name"),
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
            "retracted_pmids": retracted,
            "invalid_pmids": invalid_pmid,
            "not_found_pmids": not_found,
            "malformed_slots_pmids": malformed_slots,
            "malformed_records": malformed_records,
            "excluded": excluded,
        },
        "warnings": warnings,
        "generated_at": generated_at,
    }

    # 출력 경로는 호출자가 준 것이다 — 없는 디렉토리나 권한 없는 경로면
    # OSError가 그대로 올라가 종료 코드 1이 나간다. 계약에 없는 값이다.
    try:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(pack, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"팩을 쓸 수 없다: {args.output} ({e})", file=sys.stderr)
        return 2

    print(f"검증 완료: {passed}/{total} 통과, {rejected_count} 폐기, "
          f"{excluded_count}편 제외(철회 {len(retracted)}) → {args.output}")
    print(f"verdict={verdict}"
          + (f" — {'; '.join(verdict_reasons)}" if verdict_reasons else ""),
          file=sys.stderr)

    # 팩에 들어가지 못한 논문이 들어간 논문보다 많으면 이 팩은 지형을 대표하지 못한다.
    # 8편 중 7편이 조회되지 않았는데 0을 반환하면 호출자는 정상으로 읽는다.
    # 반씩 갈린 경우(4/4)도 대표성이 없으므로 >= 로 둔다.
    # 그 판정은 이제 verdict가 들고 있고, 종료 코드는 거기서 파생시킨다 —
    # 두 곳에 같은 조건을 적어 두면 한쪽만 고쳐질 때 조용히 갈라진다.
    if verdict == "block":
        print(f"제외 {excluded_count}편 ≥ 수록 {len(results)}편. 팩을 신뢰하지 말 것.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
