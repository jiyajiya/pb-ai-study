#!/usr/bin/env python3
"""워크숍 HTML이 들고 있는 파일 사본을 원본에서 다시 채운다.

같은 파일이 HTML 안에 세 군데 있다 — `#src-*` 스냅샷, 카드의 줄 수 라벨,
그리고 starter zip. 손으로 옮기는 방식이라 P6-1에서 셋이 한꺼번에 어긋났고,
참가자에게는 원본에 있는 「고시 등재명」 표가 통째로 없는 실습본이 나갔다.
그 부류를 여기서 닫는다.

    python3 scripts/sync_workshop.py           # 원본에서 다시 채운다
    python3 scripts/sync_workshop.py --check   # 어긋나 있으면 종료 코드 1

실습본 SKILL.md만 플러그인판과 두 곳이 다르다. 그 차이는 아래 WORKSHOP_PATCH에
적어 두고 매번 다시 적용한다 — 원본이 바뀌어도 손댈 곳은 이 두 줄뿐이다.
"""
import base64
import io
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "pubmed-evidence-workshop.html")

# 실습은 플러그인 설치 없이 프로젝트 로컬에 파일을 흩어놓고 돌린다.
# ${CLAUDE_PLUGIN_ROOT}가 없는 환경이므로 검사기 실행 경로만 바꾼다.
WORKSHOP_PATCH = [
    ('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_evidence.py" \\',
     "python3 scripts/verify_evidence.py \\"),
    ("경로는 반드시 `${CLAUDE_PLUGIN_ROOT}` 기준으로 쓴다.\n"
     "상대경로는 사용자의 작업 디렉토리에 따라 깨진다.",
     "(실습판) 프로젝트 로컬 배치이므로 프로젝트 루트 기준 상대경로를 쓴다.\n"
     "플러그인으로 포장할 때는 위 경로를 `${CLAUDE_PLUGIN_ROOT}` 기준으로 되돌린다."),
]

FILES = {
    "miner":     ("agents/abstract-miner.md", []),
    "scout":     ("agents/evidence-scout.md", []),
    "verify":    ("scripts/verify_evidence.py", []),
    "test":      ("scripts/test_verify_evidence.py", []),
    "skill":     ("skills/pubmed-evidence/SKILL.md", WORKSHOP_PATCH),
    "normalize": ("skills/pubmed-evidence/references/normalize.md", []),
}

# starter zip에는 원본 6개가 전부 완성본으로 들어간다. 참가자는 붙여넣어
# 만들지 않고, 들어 있는 것을 열어 읽고 호출한다.
ZIP_ENTRIES = {
    "pubmed-workshop/scripts/verify_evidence.py": "verify",
    "pubmed-workshop/scripts/test_verify_evidence.py": "test",
    "pubmed-workshop/.claude/agents/abstract-miner.md": "miner",
    "pubmed-workshop/.claude/agents/evidence-scout.md": "scout",
    "pubmed-workshop/.claude/skills/pubmed-evidence/SKILL.md": "skill",
    "pubmed-workshop/.claude/skills/pubmed-evidence/references/normalize.md":
        "normalize",
}

# zip 안에만 있는 파일. 원본이 저장소에 없으므로 여기에 둔다.
ZIP_LITERALS = {
    "pubmed-workshop/README.txt": """\
pubmed-workshop — 약사 스터디 실습 폴더
========================================

이 폴더를 Claude Code(데스크톱 앱 또는 터미널)로 여세요.

들어 있는 것 — 전부 완성본입니다. 만들 것은 없습니다.
- .claude/agents/abstract-miner.md : 서브에이전트 1호 (초록에서 숫자 오려내기)
- .claude/agents/evidence-scout.md : 서브에이전트 2호 (논문 지형 정찰)
- .claude/skills/pubmed-evidence/SKILL.md          : 스킬 (전체 진행 순서)
- .claude/skills/pubmed-evidence/references/normalize.md : 용어 사전
- scripts/verify_evidence.py      : 검사기 (실습 3단계에서 돌립니다)
- scripts/test_verify_evidence.py : 검사기의 시험지
- .claude/settings.json           : NCBI API 키를 넣는 곳 (실습 0단계)
  * Mac에서 .claude 폴더는 숨김 처리라 안 보일 수 있습니다. 정상입니다.

실습에서 여러분이 하는 일은 **이것들을 읽고 호출하는 것**입니다.
지시문을 직접 만들지 않습니다 — 대신 안을 열어 무엇이 적혀 있는지 봅니다.

실습 안내 문서(pubmed-evidence-workshop.html)를 열고 STEP 0부터 따라가세요.
""",
}

# 새로 넣는 항목의 타임스탬프. 고정해야 재실행이 무변경이다.
ZIP_DATE = (2026, 8, 12, 3, 13, 0)

# 원본 zip은 전 항목이 0o600이라 폴더에 실행 비트가 없다. 비어 있을 때는
# 아무도 몰랐지만, agents/에 파일이 들어간 지금은 압축을 풀어도 폴더에
# 들어갈 수 없다 (drw-------). 풀어 쓰는 zip이므로 여기서 정규화한다.
DIR_MODE = 0o40755 << 16
FILE_MODE = 0o100644 << 16


def die(msg):
    sys.exit("sync_workshop: " + msg)


def load(key):
    path, patches = FILES[key]
    text = open(os.path.join(ROOT, path), encoding="utf-8").read()
    for old, new in patches:
        n = text.count(old)
        if n != 1:
            die(f"{path}: 실습판 치환 대상이 {n}번 나온다 (1이어야 한다). "
                f"원본이 바뀌었으면 WORKSHOP_PATCH를 고쳐라: {old[:40]!r}")
        text = text.replace(old, new)
    if "</script" in text:
        die(f"{path}: '</script'가 들어 있어 HTML 임베드가 그 자리에서 끊긴다")
    if not text.endswith("\n"):
        text += "\n"
    return text


def set_block(html, key, text):
    pat = re.compile(r'(<script type="text/plain" id="src-%s">).*?(\n</script>)'
                     % re.escape(key), re.S)
    if not pat.search(html):
        die(f"HTML에 #src-{key} 블록이 없다")
    return pat.sub(lambda m: m.group(1) + text.rstrip("\n") + m.group(2), html, count=1)


def card_span(html, key):
    """data-src="KEY" 카드의 [시작, 끝) — <div> 깊이를 세어 자른다."""
    marker = 'data-src="%s"' % key
    i = html.find(marker)
    if i < 0:
        die(f"HTML에 {marker} 카드가 없다")
    start = html.rindex("<div", 0, i)
    depth = 0
    for m in re.finditer(r"<div\b|</div>", html[start:]):
        depth += 1 if m.group().startswith("<div") else -1
        if depth == 0:
            return start, start + m.end()
    die(f"{marker}: 카드를 닫는 </div>를 못 찾았다")


# 카드 안의 아무 "N줄"이나 바꾸면 안 된다 — 설명문에도 줄 수가 나온다
# ("다른 곳은 검사기 실행 경로 2줄뿐입니다"). 라벨 세 자리만 짚는다.
LABEL = re.compile(r'(class="ln">|· |<b>)(\d+)줄')


def set_labels(html, key, lines):
    start, end = card_span(html, key)
    card = LABEL.sub(lambda m: "%s%d줄" % (m.group(1), lines), html[start:end])
    return html[:start] + card + html[end:]


def rebuild_zip(b64, texts):
    src = zipfile.ZipFile(io.BytesIO(base64.b64decode(b64)))
    seen = set(src.namelist())
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            key = ZIP_ENTRIES.get(info.filename)
            body = ZIP_LITERALS.get(info.filename)
            if key:
                body = texts[key]
            # 타임스탬프는 그대로 둔다 — 고정돼야 재실행이 무변경이다.
            info.external_attr = (DIR_MODE if info.filename.endswith("/")
                                  else FILE_MODE)
            z.writestr(info, body.encode("utf-8") if body is not None
                       else src.read(info.filename))
        # zip에 아직 없는 항목은 뒤에 붙인다 (원본이 새로 관리 대상이 된 경우).
        for name, key in ZIP_ENTRIES.items():
            if name not in seen:
                info = zipfile.ZipInfo(name, ZIP_DATE)
                info.external_attr = FILE_MODE
                z.writestr(info, texts[key].encode("utf-8"))
    return base64.b64encode(out.getvalue()).decode("ascii")


def main():
    check = "--check" in sys.argv[1:]
    html = old = open(HTML, encoding="utf-8").read()
    texts = {k: load(k) for k in FILES}

    stale = []
    for key, text in texts.items():
        before = html
        html = set_labels(set_block(html, key, text), key, text.count("\n"))
        if html != before:
            stale.append(key)

    m = re.search(r'(?<=id="starter-zip">)[A-Za-z0-9+/=]+(?=</script>)', html)
    if not m:
        die("starter zip 블록을 못 찾았다")
    rebuilt = rebuild_zip(m.group(), texts)
    if rebuilt != m.group():
        stale.append("starter-zip")
        html = html[:m.start()] + rebuilt + html[m.end():]

    if check:
        if stale:
            sys.exit("sync_workshop: 워크숍 HTML이 원본과 어긋나 있다 — "
                     + ", ".join(stale)
                     + "\n  고치려면: python3 scripts/sync_workshop.py")
        print("sync_workshop: ok — 워크숍 HTML이 원본과 같다")
        return
    if html == old:
        print("sync_workshop: 변경 없음")
        return
    open(HTML, "w", encoding="utf-8").write(html)
    print("sync_workshop: 갱신 — " + ", ".join(stale))


if __name__ == "__main__":
    main()
