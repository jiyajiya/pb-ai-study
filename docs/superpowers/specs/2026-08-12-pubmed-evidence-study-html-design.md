# pubmed-evidence 스터디 HTML 재작성 — 실습 우선 구조

날짜: 2026-08-12
대상 파일: `pharmacist/pubmed-evidence/pubmed-evidence-study.html` (전면 교체)

## 완료조건 (참이 되면 끝)

1. 수강생이 이 HTML 하나만 열고, 아무것도 다운로드하지 않은 채
   복사 버튼 → 붙여넣기만으로 자기 컴퓨터에 플러그인 전체를 만들어 실행할 수 있다.
2. 파일 생성 8단계마다 즉시 실행하는 실습이 붙어 있고, 각 실습에
   기대 화면(체크포인트)과 "안 될 때" 한 줄이 있다.
3. 이론 산문은 단계당 "왜?" 박스 3줄 이내로 축소된다. 기존 섹션 01~12의
   독립 이론 섹션은 없다.
4. 임베드된 파일 내용은 레포 원본과 바이트 단위로 일치한다
   (SKILL.md의 경로 2곳 치환 제외 — 실습판임을 화면에 명시).

## 결정 사항 (사용자 확인 완료)

- 실습 배치: **프로젝트 로컬** (`실습폴더/.claude/agents`, `.claude/skills`, `scripts/`).
  플러그인 설치는 마지막 단계에서 설명만.
- 복사 방식: **탭 2종** — [터미널] `mkdir -p … && cat > 경로 <<'PB_EOF'` 한 덩어리 /
  [Claude에게] 저장 지시문 + 전문.
- 전체 파이프라인(6단계): **참가자 전원 실행** (NCBI 키 사전 발급 안내 유지).

## 페이지 구조

- 고정 헤더: 8단계 진행 내비게이션.
- 좌측 고정 사이드바(데스크톱): 실습 폴더 파일 트리 = 진도표.
  단계 통과 시(IntersectionObserver) 해당 파일에 ✓. 파일 클릭 → 전문 뷰어 패널 + 복사 버튼.
- 본문: STEP 0~7 섹션. 각 섹션 리듬 —
  ① 이번에 만드는 것(한 줄) ② 복사 블록(탭 2종, 접힌 미리보기, 줄 수 표기)
  ③ 실행 지시(복사 칩 포함) ④ ✓ 체크포인트(기대 출력 예시) + "안 될 때"
  ⑤ "왜?" 3줄 이내.
- 발표자 노트 토글(기존 방식 유지, 단계별로 재배치).
- 스타일: 기존 다크 테마 CSS 재사용 + 사이드바/복사블록/체크포인트 컴포넌트 추가.

## 커리큘럼 (총 60분)

| STEP | 분 | 만드는 파일 | 실습 | 체크포인트 |
|---|---|---|---|---|
| 0 준비 | 5 | 폴더 골격 | claude·python3·NCBI 키 확인 | `find . -type d` 출력 |
| 1 첫 서브에이전트 | 10 | `.claude/agents/abstract-miner.md` | 새 세션에서 PMID 23853635 채굴 | 5슬롯+quote JSON |
| 2 내가 게이트 | 5 | — | pubmed.ncbi.nlm.nih.gov/23853635 에서 quote·value·unit을 Ctrl+F | 하이라이트 |
| 3 기계 게이트 | 10 | `scripts/verify_evidence.py`, `test_verify_evidence.py` | 테스트(`ok`, 무결성 확인) → raw_slots.json·meta.json 저장 → 게이트 실행 | `verified: true`, 종료코드 0 |
| 4 깨뜨리기 | 10 | — | 백업 후 값·단위·quote 조작 → 게이트 재실행 | `value: null` + 사유 코드 (C1~C4) |
| 5 스캔 담당 | 10 | `.claude/agents/evidence-scout.md` | 새 세션에서 scout 호출 | tone/pmids JSON, 초록 원문 없음 |
| 6 조립 | 10 | `.claude/skills/pubmed-evidence/SKILL.md`(실습판), `references/normalize.md` | 새 세션에서 "마그네슘 수면 근거 뽑아줘" (F2) | evidence-pack.json + verdict |
| 7 마무리 | 5 | — | 플러그인 포장·게이트의 한계·P8 경고 (설명만) | — |

- 예제 고정: F2(마그네슘×수면), PMID 23853635 (17.36 min 사례).
- 에이전트 추가 단계(1·5·6)는 "claude 새로 실행"을 명시 — 세션 시작 시 등록됨을 학습 포인트로.

## 구현 방식

- 생성 스크립트(Python, 세션 스크래치패드)가 레포 원본 파일을 읽어
  HTML 템플릿의 플레이스홀더에 주입. 수작업 전사 금지.
- 원본은 `<script type="text/plain" id="src-*">`로 1회만 임베드,
  터미널/Claude 변형은 JS가 조합. 검증: 내용에 `</script`·`PB_EOF` 부재 확인,
  임베드 결과와 원본 diff 0.
- SKILL.md 치환 2곳: P6 실행 명령의 `${CLAUDE_PLUGIN_ROOT}/scripts/…` →
  `scripts/…`, 직후 경로 규칙 문단 → 실습판 주석. 치환 횟수를 스크립트가 단언.
- 클립보드: `navigator.clipboard` + `execCommand` 폴백 (file:// 대응).

## 범위 밖

- 원본 플러그인 파일 수정, README 수정, kr-claims 자료.
- HTML 자동 재생성 파이프라인(원본 변경 시 드리프트는 알려진 한계로 보고만).
