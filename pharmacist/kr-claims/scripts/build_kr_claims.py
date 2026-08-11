#!/usr/bin/env python3
"""build_kr_claims.py — 식약처 고시전문(txt) → kr_claims.json

「건강기능식품의 기준 및 규격」 고시전문에서 **(원료 × 기능성) 단위로**
인정 문구·일일섭취량·섭취 시 주의사항을 추출한다.
LLM은 이 경로에 개입하지 않는다.

왜 (원료 × 기능성)인가:
  같은 EPA·DHA라도 고시가 기능성마다 다른 섭취량을 정해 두었다.
    (가) 혈중 중성지질 개선 ･ 혈행 개선 : EPA와 DHA의 합으로서 0.5 ~ 2 g
    (나) 기억력 개선                  : EPA와 DHA의 합으로서 0.9 ~ 2 g
  원료당 범위 하나로 저장하면 중성지질 콘텐츠에 기억력 기준이 붙는다.

이 스크립트가 지키는 것:
  1. **문구는 원문 그대로 옮긴다.** 다듬지도, 쪼개지도, 합성하지도 않는다 —
     한 글자만 달라도 그것은 인정받지 않은 표현이다. 고시가 `･`로 이어
     붙여 놓은 기능성은 이어 붙은 채로 한 행이다.
  2. 숫자 파싱에 실패해도 `raw`를 남기고 `parsed: false`로 표시한다.
     **raw가 정본이고 파싱된 숫자는 편의다.**
  3. 읽지 못한 블록을 반드시 보고한다. 커버리지가 출력에 남지 않으면
     읽은 것이 전부라고 오해된다.

이 파일에 없는 것:
  개별인정형 원료. 이 고시는 고시형(별표 등재)만 담는다.
  **"표에 없음"은 "인정되지 않음"이 아니다.**

의존성: 표준 라이브러리만 사용.

사용:
  python3 build_kr_claims.py --input raw/고시전문.txt --output data/kr_claims.json \
      --notice "제2026-43호" --effective 2026-06-11 --url "https://..."

  입력 txt를 만드는 절차는 README 1절에 있다. `raw/`는 git 미추적이다 —
  원문을 저장소에 남기면 에이전트가 이 JSON 대신 원문을 직접 읽는다.

종료 코드:
  0  생성 완료 (미추출 블록이 있어도 0이다. 개수는 stdout과 파일에 있다)
  2  입력을 읽을 수 없거나 원료 블록을 하나도 찾지 못했다
"""

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter

# 이 스크립트의 파싱 로직 버전. data/kr_claims.json의 source.builder_version과
# 대조해 재현 가능성을 확인한다(S9) — raw가 없어 재빌드를 못 할 때도 최소한
# "어느 버전의 파서가 만들었는지"는 데이터에 남는다.
BUILDER_VERSION = "build_kr_claims/0.2"

# 원료 블록 머리 — " 2-16" 처럼 번호만 홀로 있는 줄.
# 제4장 시험법도 같은 번호 체계를 쓰므로 번호만으로는 원료를 특정할 수 없다.
# 아래 BLOCK_MARK 중 하나를 품은 블록만 원료로 인정한다.
BLOCK_HEAD = re.compile(r"^[ \t]*(\d{1,2}-\d{1,2})[ \t]*$", re.M)
BLOCK_MARK = ("제조기준", "최종제품의 요건")

# hwpx 추출에서 아래첨자가 유실돼 비타민 B1/B2/B6/B12가 전부 "비타민 B"
# 하나로 붕괴하는 문제(S1)의 보정 맵. 근거 없이 매핑을 지어내면 안 되므로
# 아래 세 근거가 독립적으로 같은 결론을 가리키는 항목만 넣는다:
#   1. 표 순서가 고시 별표의 표준 영양성분 순서와 정확히 일치한다 —
#      A(1-1)·베타카로틴(1-2)·D(1-3)·E(1-4)·K(1-5)·B1(1-6)·B2(1-7)·
#      나이아신(1-8)·판토텐산(1-9)·B6(1-10)·엽산(1-11)·B12(1-12)·비오틴(1-13)·C(1-14)…
#   2. 각 최소섭취량(daily_intake.min)이 "한국인 영양소 섭취기준" RDA의 30%다.
#      같은 표의 나이아신(4.5=15×30%)·판토텐산(1.5=5×30%)·비오틴(9=30×30%)과
#      동일한 규칙으로 계산하면: B1 1.2mg→0.36(1-6과 일치), B2 1.4mg→0.42
#      (1-7과 일치), B6 1.5mg→0.45(1-10과 일치), B12 2.4μg→0.72(1-12와 일치).
#   3. 1-6·1-7·1-12의 raw 기능성 문구가 각 비타민의 공인 문구와 일치한다:
#      "탄수화물과 에너지 대사에 필요"(B1), "체내 에너지 생성에 필요"(B2),
#      "정상적인 엽산 대사에 필요"(B12 — 엽산·B12 대사 연관은 공인 기능이다).
#      1-12만 단위가 μg(마이크로그램)라 mg 단위인 B1/B2/B6와도 구분된다.
#   1-10(B6)은 raw 문구 자체가 유실돼 근거 3은 못 쓰지만, 근거 1·2가 서로
#   독립적으로 같은 자리를 가리키므로 보정한다.
NAME_FIX = {
    "1-6": "비타민 B1",
    "1-7": "비타민 B2",
    "1-10": "비타민 B6",
    "1-12": "비타민 B12",
}

# 라벨 앞의 `(1)` 번호와 콜론은 추출본에서 빠져 있는 경우가 있다.
# 프로바이오틱스는 번호가 없고("기능성 내용 :"), 섭취량은 콜론이 없다.
LABEL_FUNC = re.compile(r"^[ \t]*(?:\(\d+\)[ \t]*)?기능성[ \t]*내용[ \t]*[:：][ \t]*(.*)$")
LABEL_DOSE = re.compile(r"^[ \t]*(?:\(\d+\)[ \t]*)?일일섭취량[ \t]*[:：]?[ \t]*(.*)$")
LABEL_CAUTION = re.compile(r"^[ \t]*(?:\(\d+\)[ \t]*)?섭취[ \t]*시[ \t]*주의사항[ \t]*[:：]?[ \t]*(.*)$")

# (가)(나)(다) 하위 항목. 기능성별 섭취량이 여기로 갈라진다.
SUB_ITEM = re.compile(r"^[ \t]*\(([가-힣])\)[ \t]*(.+)$")
# 다음 라벨 — 하위 항목 수집을 여기서 멈춘다.
NEXT_LABEL = re.compile(r"^[ \t]*(?:\d+\)|\(\d+\))")

# (S6) 라벨만 있고 실제 문구가 없는 하위 항목. "1). (1). (가) 및 (나)의
# 경우"처럼 조항 번호만 이어 붙은 문자열이 기능성 문구 자리에 들어가면,
# 그걸 표에 옮기는 에이전트가 "다듬어" 진짜 문구를 창작할 여지가 생긴다.
LABEL_ONLY = re.compile(
    r"^\s*\d+\)\.?\s*(?:\(\d+\)\.?\s*)?"
    r"(?:\([가-힣]+\)\s*(?:및|,)?\s*)*(?:의\s*)?경우\.?\s*$"
)

# "EPA와 DHA의 합으로서 0.5 ~ 2 g" → basis / min / max / unit
# basis는 논문 용량과 비교할 때 결정적이다. EPA 단독 용량을 'EPA와 DHA의 합'
# 범위와 비교하면 안 되기 때문이다. 못 잡으면 None으로 두고 비교를 포기한다.
BASIS = r"(?:(?P<basis>.+?)\s*(?:으로서|로서|으로써|로써|으로|로)\s+)?"
NUM = r"(?P<n1>\d[\d,]*(?:\.\d+)?)"
NUM2 = r"(?P<n2>\d[\d,]*(?:\.\d+)?)"
UNIT = r"(?P<unit>[^\s(（]+(?:\s+[A-Za-z]{2,4})?)"

# 고시의 섭취량 표기는 네 가지뿐이다. 범위 / 이상 / 이하 / 단일값.
# bound를 남겨야 "상한 없음"과 "상한 N"을 구분해 비교할 수 있다.
INTAKE_FORMS = (
    ("range", re.compile(rf"^{BASIS}{NUM}\s*[~∼〜–—-]\s*{NUM2}\s*{UNIT}")),
    ("min", re.compile(rf"^{BASIS}{NUM}\s*(?P<unit>[^\s(（]+)\s*이상\b")),
    ("max", re.compile(rf"^{BASIS}{NUM}\s*(?P<unit>[^\s(（]+)\s*이하\b")),
    ("exact", re.compile(rf"^{BASIS}{NUM}\s*{UNIT}\s*$")),
)
# 괄호 보조 표기는 매칭 전에 걷어낸다.
#   "210 ~ 1,000 μg RAE (699.93 ~ 3,333 IU)"  괄호 안은 환산값
#   "100,000,000(1억) ~ 10,000,000,000(100억) CFU"  괄호가 숫자를 끊는다
# 중첩 괄호("3.3 g (베타글루칸(β-glucan)으로서 287.1 ~ 534.6 mg)")는 한 번의
# 치환으로 못 걷어낸다 — [^)]*가 첫 ')'에서 멈춰 바깥쪽 여는 괄호를 삼키고
# 그 짝인 닫는 괄호만 밖에 남긴다(S5). 괄호가 없는 가장 안쪽 그룹만 매칭하고
# 더 지울 게 없을 때까지 반복해야 안쪽부터 바깥으로 걷힌다.
PAREN = re.compile(r"\([^()]*\)")
# 파싱이 성공해도 unit에 괄호·구두점이 남아 있으면 괄호 제거가 불완전했다는
# 신호다. 우연히 숫자 매칭까지 성공했더라도 parsed:false로 되돌린다(S5 위생 검사).
UNIT_JUNK = re.compile(r"[()（）\[\].,;:]")


def strip_parens(text: str) -> str:
    """괄호를 안쪽부터 바깥쪽으로 반복 제거한다. 중첩 괄호 대응(S5)."""
    prev = None
    while prev != text:
        prev = text
        text = PAREN.sub(" ", text)
    return text


def missing_sequence_codes(entries: list[dict], dropped: list[str]) -> list[str]:
    """`군-번호` 코드 수열의 결번을 찾는다.

    이 저장소는 고시 원문(raw/)을 남기지 않는다. 그래서 "무엇을 못 읽었는지가
    JSON에 남는다"는 것이 이 데이터를 믿을 수 있는 유일한 축인데, 커버리지가
    보는 것은 **읽어낸 블록의 결손**뿐이었다. 블록으로 잡히지도 못한 원료는
    missing_functional_claim에도 dropped_non_ingredient_blocks에도 안 나온다 —
    처음부터 없었던 것처럼 보인다. 실제로 2-7이 그렇게 사라져 있었고,
    커버리지 어디에도 흔적이 없었다.

    코드가 연속이라는 보장은 고시에 없다(폐지된 원료의 번호는 빈다).
    그래서 실패로 만들지 않고 사실만 남긴다 — 사람이 원문에서 판단할 몫이다.
    """
    seen = {e["code"] for e in entries} | set(dropped)
    groups: dict[str, list[int]] = {}
    for code in seen:
        m = re.fullmatch(r"(\d+)-(\d+)", code)
        if m:
            groups.setdefault(m.group(1), []).append(int(m.group(2)))
    missing = []
    for g, nums in groups.items():
        missing += [f"{g}-{n}" for n in range(1, max(nums) + 1) if n not in nums]
    return sorted(missing, key=lambda c: tuple(int(p) for p in c.split("-")))


def split_blocks(text: str) -> list[dict]:
    """고시전문을 원료 블록으로 자른다."""
    heads = list(BLOCK_HEAD.finditer(text))
    blocks = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[m.end():end]
        if not any(mark in body for mark in BLOCK_MARK):
            continue  # 시험법 등 원료가 아닌 블록
        name = next((ln.strip() for ln in body.split("\n") if ln.strip()), "")
        blocks.append({"code": m.group(1), "name": name, "body": body})
    return blocks


def parse_intake(raw: str) -> dict:
    """일일섭취량 문자열을 수치로 연다. 실패해도 raw는 남긴다.

    raw가 정본이다. min/max/unit은 비교를 위한 편의값이고,
    `parsed: false`면 비교 단계가 스스로 물러선다.
    """
    text = raw.strip()
    out = {"raw": text, "basis": None, "min": None, "max": None,
           "unit": None, "bound": None, "parsed": False}
    if not text:
        return out
    probe = re.sub(r"\s+", " ", strip_parens(text)).strip()

    for bound, pat in INTAKE_FORMS:
        m = pat.match(probe)
        if not m:
            continue
        # 문장을 끝까지 소화했는지 본다. 남은 말이 있으면 조건이 더 붙어 있다는
        # 뜻이다 — "리놀레산은 4.0 g 이상 또는 리놀렌산은 0.6 g 이상"에서
        # 앞부분만 잡고 넘어가면 뒤 조건이 조용히 사라진다.
        if probe[m.end():].strip(" .,·･"):
            continue
        try:
            n1 = float(m.group("n1").replace(",", ""))
            n2 = float(m.group("n2").replace(",", "")) if bound == "range" else None
        except ValueError:
            continue
        unit = m.group("unit").strip()
        if UNIT_JUNK.search(unit):
            continue  # 괄호·구두점이 남은 단위 — 괄호 제거가 불완전했다는 신호(S5)
        out["min"] = n1 if bound in ("range", "min", "exact") else None
        out["max"] = n2 if bound == "range" else (n1 if bound in ("max", "exact") else None)
        basis = (m.group("basis") or "").strip(" ,･·").strip()
        out["basis"] = basis or None
        out["unit"] = unit
        out["bound"] = bound
        out["parsed"] = True
        return out
    return out


def collect_sub_items(lines: list[str], start: int) -> list[str]:
    """(가)(나)(다) 하위 항목을 다음 라벨 전까지 모은다."""
    items = []
    for ln in lines[start + 1:]:
        if NEXT_LABEL.match(ln):
            break
        sub = SUB_ITEM.match(ln)
        if sub:
            items.append(sub.group(2).strip())
        elif items and ln.strip():
            items[-1] += " " + ln.strip()  # 줄바꿈으로 끊긴 항목 잇기
    return items


def extract(block: dict, label_polluted: list[str] | None = None) -> dict:
    """블록 하나에서 기능성·섭취량·주의사항을 뽑는다.

    label_polluted: 넘기면, 라벨 조각만 남아 functional_claim을 None으로
    비운 행의 블록 코드를 여기 덧붙인다(S6). 호출자가 커버리지에 보고한다.
    """
    lines = block["body"].split("\n")
    claim_text = None
    dose_inline = None
    dose_items: list[str] = []
    cautions: list[str] = []

    for i, ln in enumerate(lines):
        if claim_text is None and (m := LABEL_FUNC.match(ln)):
            claim_text = m.group(1).strip() or None
        elif dose_inline is None and not dose_items and (m := LABEL_DOSE.match(ln)):
            rest = m.group(1).strip()
            if rest:
                dose_inline = rest
            else:
                # 라벨만 있고 값이 없다 = 기능성별로 갈라진 표다
                dose_items = collect_sub_items(lines, i)
        elif not cautions and LABEL_CAUTION.match(ln):
            cautions = collect_sub_items(lines, i)

    rows = []
    if dose_items:
        # 기능성별 섭취량. 하위 항목의 문구가 그 행의 정본이다.
        for item in dose_items:
            parts = re.split(r"[:：]", item, maxsplit=1)
            if len(parts) == 2:
                claim = parts[0].strip()
                if LABEL_ONLY.match(claim):
                    # 라벨 조각만 남은 문구 — 창작 유혹을 남기느니 비워 둔다(S6)
                    if label_polluted is not None:
                        label_polluted.append(block["code"])
                    claim = None
                rows.append({"functional_claim": claim,
                             "daily_intake": parse_intake(parts[1])})
            else:
                # 콜론이 없으면 문구와 수치를 가를 수 없다. 통째로 남긴다.
                rows.append({"functional_claim": None,
                             "daily_intake": parse_intake(item)})
    elif claim_text or dose_inline:
        rows.append({"functional_claim": claim_text,
                     "daily_intake": parse_intake(dose_inline) if dose_inline else None})

    return {
        "code": block["code"],
        "ingredient_ko": block["name"],
        # 1-x는 영양성분, 2-x는 기능성원료다. 템플릿이 서로 다르다.
        "category": "영양성분" if block["code"].startswith("1-") else "기능성원료",
        "recognition_type": "고시형",
        "functional_claims_raw": claim_text,
        "rows": rows,
        "cautions": cautions,
    }


def duplicate_names(entries: list[dict]) -> dict[str, list[str]]:
    """동명 원료 코드 맵(S1b). 조회 부분일치가 어느 쪽에 걸렸는지 못 가르는
    이름이 2개 이상이면 여기 잡힌다 — check_kr_claims.py가 리포트 warnings로
    흘려보낸다."""
    counts = Counter(e["ingredient_ko"] for e in entries)
    return {name: [e["code"] for e in entries if e["ingredient_ko"] == name]
            for name, c in counts.items() if c > 1}


def is_ingredient_entry(e: dict) -> bool:
    """rows도 없고 functional_claims_raw도 없으면 원료가 아니라 BLOCK_MARK
    검사를 우연히 통과한 쓰레기 블록이다(S7 — 13-2 사례)."""
    return bool(e["rows"] or e["functional_claims_raw"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="고시전문 텍스트")
    ap.add_argument("--output", required=True, help="kr_claims.json 경로")
    ap.add_argument("--notice", default="", help="고시 번호 (예: 제2026-43호)")
    ap.add_argument("--effective", default="", help="시행일 (YYYY-MM-DD)")
    ap.add_argument("--url", default="", help="고시전문 게시물 URL")
    args = ap.parse_args()

    try:
        text = open(args.input, encoding="utf-8").read()
    except OSError as e:
        print(f"입력을 읽을 수 없다: {e}", file=sys.stderr)
        return 2

    blocks = split_blocks(text)
    if not blocks:
        print("원료 블록을 하나도 찾지 못했다. 추출 형식이 바뀌었을 수 있다.",
              file=sys.stderr)
        return 2

    label_polluted: list[str] = []
    entries = [extract(b, label_polluted) for b in blocks]

    # S1: hwpx 추출에서 아래첨자가 유실돼 붕괴한 이름을 보정한다.
    for e in entries:
        fix = NAME_FIX.get(e["code"])
        if fix:
            e["ingredient_ko"] = fix

    # S7: 원료가 아닌 블록이 BLOCK_MARK 검사를 통과해 들어온 경우(예: 13-2)
    # — rows도 functional_claims_raw도 없으면 조회에서 "등재"로 잡히면 안 된다.
    dropped = [e["code"] for e in entries if not is_ingredient_entry(e)]
    entries = [e for e in entries if is_ingredient_entry(e)]

    # 커버리지 — 무엇을 못 읽었는지가 남아야 이 파일을 믿을 수 있다.
    no_claim = [e["code"] for e in entries if not e["functional_claims_raw"]]
    no_dose = [e["code"] for e in entries
               if not any(r["daily_intake"] for r in e["rows"])]
    unparsed = [f'{e["code"]}:{r["daily_intake"]["raw"][:40]}'
                for e in entries for r in e["rows"]
                if r["daily_intake"] and not r["daily_intake"]["parsed"]]
    no_cautions = [e["code"] for e in entries if not e["cautions"]]
    dup_names = duplicate_names(entries)
    missing_codes = missing_sequence_codes(entries, dropped)

    input_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

    doc = {
        "schema": "kr-claims/0.1",
        "source": {
            "title": "건강기능식품의 기준 및 규격",
            "notice": args.notice or None,
            "effective_date": args.effective or None,
            "url": args.url or None,
            "scope": "고시형 원료만. 개별인정형은 이 고시에 없다 — "
                     "표에 없음은 인정되지 않음이 아니다.",
            "checked_at": time.strftime("%Y-%m-%d"),
            # S9: 재빌드 결과가 같은지, JSON을 사람이 직접 손댔는지 확인할
            # 유일한 수단. input(고시전문 txt)의 해시와 이 파서의 버전이다.
            "input_sha256": input_sha256,
            "builder_version": BUILDER_VERSION,
        },
        "coverage": {
            "blocks": len(entries),
            "rows": sum(len(e["rows"]) for e in entries),
            "missing_functional_claim": no_claim,
            "missing_daily_intake": no_dose,
            "unparsed_intake": unparsed,
            "missing_cautions": no_cautions,
            "duplicate_ingredient_names": dup_names,
            "label_only_claims": sorted(set(label_polluted)),
            "dropped_non_ingredient_blocks": dropped,
            # 블록으로 잡히지도 못한 원료는 다른 어떤 필드에도 안 남는다.
            # 결번이 있다고 반드시 유실인 것은 아니다(폐지 번호일 수 있다).
            "missing_codes": missing_codes,
            "note": "missing/unparsed 항목은 고시 원문을 보고 사람이 채워야 한다. "
                    "빈 값을 '해당 없음'으로 읽지 말 것. missing_codes는 코드 수열의 "
                    "결번이다 — 파서가 놓친 것인지 고시에서 폐지된 번호인지는 "
                    "원문을 봐야 갈린다.",
        },
        "ingredients": entries,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    print(f"원료 {len(entries)}개 / 행 {doc['coverage']['rows']}개 → {args.output}")
    print(f"  기능성 문구 없음 {len(no_claim)} · 섭취량 없음 {len(no_dose)} · "
          f"수치 파싱 실패 {len(unparsed)} · 주의사항 없음 {len(no_cautions)}")
    if dup_names:
        print(f"  동명 원료 {len(dup_names)}건: {sorted(dup_names)}")
    if dropped:
        print(f"  원료 아닌 블록 제외 {len(dropped)}건: {dropped}")
    if missing_codes:
        print(f"  ⚠ 코드 결번 {len(missing_codes)}건: {missing_codes} — 파서가 놓친 "
              f"원료인지 고시에서 폐지된 번호인지 원문에서 확인할 것.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
