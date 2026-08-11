# evidence-card

성분 × 효능 조합의 PubMed 근거를 수집·검증해 카드뉴스용 `evidence-pack.json`을 만든다.
**숫자 환각을 스크립트 게이트로 차단하는 것**이 이 플러그인의 존재 이유다.

## 구조

```
evidence-card/                          ← 플러그인 루트
├── .claude-plugin/
│   └── plugin.json                     ← 매니페스트. 이 폴더에는 이것만.
├── skills/
│   └── evidence-card/
│       ├── SKILL.md                    ← 오케스트레이터 (메인 컨텍스트)
│       ├── references/
│       │   └── normalize.md            ← 한글↔영문 성분명 매핑 (필요시 로드)
│       └── tests/
│           └── fixtures.md             ← 회귀 테스트 4종
├── agents/
│   ├── evidence-scout.md               ← 서브에이전트 (격리 컨텍스트)
│   └── abstract-miner.md               ← 서브에이전트 (격리 컨텍스트, 병렬)
└── scripts/
    └── verify_numbers.py               ← 결정론적 검증 게이트 (LLM 아님)
```

### 왜 이 배치인가

| 위치 | 이유 |
|---|---|
| `agents/`가 플러그인 **루트** | 스킬 폴더 하위는 서브에이전트 스캔 대상이 아니다. 루트에 둬야 등록된다 |
| `scripts/`가 플러그인 **루트** | `${CLAUDE_PLUGIN_ROOT}`로 참조. 스킬 안에 두면 경로가 지저분해진다 |
| `references/`, `tests/`가 **스킬 안** | 스킬 본문에서만 쓰는 부속 문서. 필요할 때만 읽힌다 |
| `plugin.json`만 `.claude-plugin/` | 다른 디렉토리를 여기 넣으면 **에러 없이 조용히 무시된다** |

## 컨텍스트 흐름

```
[메인 컨텍스트]                    [격리 컨텍스트]
                                   
SKILL.md 로드
  │
  ├─ P1 정규화 (직접)
  │
  ├─ P2 위임 ────────────────────▶ evidence-scout
  │                                  · PubMed 30편 스캔
  │                                  · 초록 원문이 여기 쌓임
  │   ◀──── JSON 요약만 반환 ────    · 종료 시 전부 폐기
  │        {tone, pmids}
  │
  ├─ P3 위임 (병렬) ─────────────▶ abstract-miner ×N
  │                                  · 각자 초록 1편
  │   ◀──── 4슬롯 + quote ────────   · 서로 격리 → 숫자 교차오염 없음
  │
  └─ P6 Bash 실행 ───────────────▶ verify_numbers.py
      ◀──── evidence-pack.json ──    · PubMed 재조회 후 대조
                                     · LLM 판단 개입 없음
```

메인 컨텍스트에는 **초록 원문이 한 번도 들어오지 않는다.**
이것이 서브에이전트를 쓰는 실질적 이유다 — 역할 분담이 아니라 오염 차단.

## 설치

### 방법 A — 로컬 플러그인 (개발 중 권장)

```bash
# 마켓플레이스 없이 로컬 경로에서 직접
claude plugin install ./evidence-card
```

### 방법 B — 플러그인 안 쓰고 흩어놓기 (빠른 테스트)

```bash
mkdir -p ~/.claude/skills/evidence-card ~/.claude/agents
cp -r skills/evidence-card/* ~/.claude/skills/evidence-card/
cp agents/*.md               ~/.claude/agents/
cp -r scripts                ~/.claude/skills/evidence-card/
# 이 경우 SKILL.md의 ${CLAUDE_PLUGIN_ROOT}를 실제 경로로 바꿔야 한다
```

방법 B는 팀 공유가 안 되고 경로 수정이 필요하다. 혼자 테스트할 때만.

## 사전 준비

```bash
export NCBI_API_KEY="..."   # 없으면 초당 3회 제한 → 병렬 8개가 바로 걸림
```

발급: NCBI 계정 → Account Settings → API Key Management

## 확인

```
/plugin              # evidence-card 가 목록에 보이는지
```

서브에이전트가 등록됐는지는 세션에서 직접 호출해 확인한다.
플러그인은 뜨는데 에이전트가 없다면 `agents/` 위치를 다시 확인할 것.

## MVP 범위

포함: P1 정규화 / P2 근거스캔 / P3 숫자채굴 / P6 검증

미포함: P4 반론논문 · P5 안전성 · P7 카피작성 · **P8 광고법 검사**

> ⚠️ P8이 없으므로 이 플러그인의 출력물을 그대로 발행하면 안 된다.
> 성분 설명과 제품 효능 표방의 경계는 사람이 검토해야 한다.
