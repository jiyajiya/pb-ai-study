# pubmed-evidence

성분 × 효능 조합의 PubMed 근거를 수집해 `evidence-pack.json`을 만든다.
**숫자 환각을 스크립트 게이트로 차단하는 것**이 이 플러그인의 존재 이유다.
카피도 카드도 만들지 않는다 — 산출물은 인용문·PMID·무결성 정보가 붙은
검증 결과 레코드이고, 문안은 하류(`ingredient-analysis`)가 쓴다.

## 구조

```
pubmed-evidence/                          ← 플러그인 루트
├── .claude-plugin/
│   ├── plugin.json                     ← 플러그인 매니페스트
│   └── marketplace.json                ← 로컬 설치용 마켓플레이스 등록
├── skills/
│   └── pubmed-evidence/
│       ├── SKILL.md                    ← 오케스트레이터 (메인 컨텍스트)
│       ├── references/
│       │   └── normalize.md            ← 한글↔영문 성분명 매핑 (필요시 로드)
│       └── tests/
│           └── fixtures.md             ← 회귀 테스트 4종
├── agents/
│   ├── evidence-scout.md               ← 서브에이전트 (격리 컨텍스트)
│   └── abstract-miner.md               ← 서브에이전트 (격리 컨텍스트, 병렬)
├── scripts/
│   ├── verify_evidence.py              ← 결정론적 검증 게이트 (LLM 아님)
│   └── test_verify_evidence.py         ← 네트워크 없이 도는 계약 검사
├── pubmed-evidence-study-pharmacist.html  ← 스터디 자료 (약사용)
└── pubmed-evidence-study-dev.html         ← 스터디 자료 (개발자용)
```

HTML 두 개는 플러그인 동작과 무관한 스터디 자료지만, 플러그인 루트에 있으므로
설치하면 함께 딸려 온다.

### 왜 이 배치인가

| 위치 | 이유 |
|---|---|
| `agents/`가 플러그인 **루트** | 스킬 폴더 하위는 서브에이전트 스캔 대상이 아니다. 루트에 둬야 등록된다 |
| `scripts/`가 플러그인 **루트** | `${CLAUDE_PLUGIN_ROOT}`로 참조. 스킬 안에 두면 경로가 지저분해진다 |
| `references/`, `tests/`가 **스킬 안** | 스킬 본문에서만 쓰는 부속 문서. 필요할 때만 읽힌다 |
| `.claude-plugin/`에 매니페스트만 | 다른 디렉토리를 여기 넣으면 **에러 없이 조용히 무시된다** |

## 컨텍스트 흐름

```
[메인 컨텍스트]                    [격리 컨텍스트]
                                   
SKILL.md 로드
  │
  ├─ P1 정규화 (직접)
  │
  ├─ P2 위임 ────────────────────▶ evidence-scout
  │                                  · 검색 4회 × 최대 40건 스캔
  │                                  · 초록 원문이 여기 쌓임
  │   ◀──── JSON 요약만 반환 ────    · 종료 시 전부 폐기
  │        {tone, pmids}
  │
  ├─ P3 위임 (병렬) ─────────────▶ abstract-miner ×N
  │                                  · 각자 초록 1편
  │   ◀──── 5슬롯 + quote ────────   · 서로 격리 → 숫자 교차오염 없음
  │
  └─ P6 Bash 실행 ───────────────▶ verify_evidence.py
      ◀──── evidence-pack.json ──    · PubMed 재조회 후 대조
                                     · 철회·이해충돌도 같은 응답에서 확인
                                     · LLM 판단 개입 없음
```

### 이 구조가 실제로 하는 일 — 오염 "차단"이 아니라 **표면 축소**

정확히 말하면, 메인 컨텍스트에 초록 원문이 안 들어오는 게 아니다.
miner가 돌려주는 `quote`는 **글자 하나 안 바꾼 원문 문장**이고, 그건
메인 컨텍스트에 들어온다. 위 그림의 `◀── 5슬롯 + quote ──`가 그것이다.

바뀌는 것은 **양과 형태**다.

| | 서브에이전트 없이 | 이 구조 |
|---|---|---|
| 메인에 들어오는 원문 | 스캔한 초록 전문 (검색 4회 × 최대 40건) | 논문당 최대 5문장 |
| 그 문장의 출처 | 섞여 있음 | 슬롯·PMID에 묶여 있음 |
| 검증 가능성 | 불가 (무엇을 대조할지 모름) | 가능 (quote 단위로 대조) |

**quote가 메인에 들어오는 것은 부작용이 아니라 요구사항이다.** P6이
대조할 대상이 없으면 검증 자체가 성립하지 않는다. 서브에이전트를 쓰는
실질적 이유는 원문을 못 보게 하는 게 아니라, 메인이 보는 원문을
**검증 가능한 최소 단위로 줄이는 것**이다.

그리고 숫자의 최종 판정은 어느 쪽이든 LLM이 하지 않는다 — `verify_evidence.py`가
PubMed를 재조회해 대조한다. 컨텍스트 격리는 그 검증을 **가능하게 만드는
설계**이지, 그 자체가 안전장치는 아니다.

## 설치

### 방법 A — 로컬 마켓플레이스로 설치 (개발 중 권장)

`claude plugin install`은 **마켓플레이스에서만** 설치한다. 로컬 경로를
직접 넘길 수 없으므로, 마켓플레이스를 먼저 등록한다.

```bash
claude plugin marketplace add /절대/경로/pharmacist
claude plugin install pubmed-evidence@pharma-bros
claude plugin install kr-claims@pharma-bros        # 국내 기준 대조까지 할 때
```

`marketplace add`에 넘기는 경로는 레포 루트가 아니라
**`.claude-plugin/marketplace.json`이 있는 디렉토리**다. 이 레포에는 그런 곳이
둘이다 — 두 플러그인을 함께 등록하는 `pharmacist/`와, 이 플러그인 하나만
등록하는 `pharmacist/pubmed-evidence/`. 위처럼 `pharmacist/`를 쓰는 것을 기본으로
한다. 이걸 단독으로 떼어 쓸 때만 아래를 쓴다.

```bash
claude plugin marketplace add /절대/경로/pharmacist/pubmed-evidence
claude plugin install pubmed-evidence@pubmed-evidence
```

**둘을 다 등록하지는 마라.** 같은 플러그인이 두 마켓플레이스에 잡혀
어느 쪽 사본이 설치됐는지 추적하기 어려워진다.
매니페스트가 유효한지는 설치 전에 확인할 수 있다:

```bash
claude plugin validate .
```

### 방법 B — 플러그인 안 쓰고 흩어놓기 (빠른 테스트)

```bash
mkdir -p ~/.claude/skills/pubmed-evidence ~/.claude/agents
cp -r skills/pubmed-evidence/* ~/.claude/skills/pubmed-evidence/
cp agents/*.md               ~/.claude/agents/
cp -r scripts                ~/.claude/skills/pubmed-evidence/
# 이 경우 SKILL.md의 ${CLAUDE_PLUGIN_ROOT}를 실제 경로로 바꿔야 한다
```

방법 B는 팀 공유가 안 되고 경로 수정이 필요하다. 혼자 테스트할 때만.

## 사전 준비

```bash
export NCBI_API_KEY="..."   # 없으면 초당 3회 제한 → 병렬 8개가 바로 걸림
```

발급: NCBI 계정 → Account Settings → API Key Management

`export`는 **그 터미널 세션에서만** 유효하다. **같은 셸에서 `claude`를 실행**해야
키가 전달된다 — 다른 창에서 세션을 켜면 키 없이 돌다가 병렬 8개에서 429가 나고,
그 실패는 "조회 실패"로 조용히 팩 경고에 묻힌다. 매번 다시 치기 싫으면
`~/.zshrc`에 영구 등록해둔다.

## 확인

```
/plugin              # pubmed-evidence 가 목록에 보이는지
```

서브에이전트가 등록됐는지는 세션에서 직접 호출해 확인한다.
플러그인은 뜨는데 에이전트가 없다면 `agents/` 위치를 다시 확인할 것.

## 게이트가 보증하는 것과 보증하지 못하는 것

| | 막는다 | 못 막는다 |
|---|---|---|
| **조작** | 초록에 없는 숫자·단위를 만들어내기 (C1~C4) | — |
| **오배치** | — | 같은 문장의 **다른** 숫자를 잘못된 슬롯에 넣기 |
| **철회 논문** | 철회된 논문은 슬롯이 전부 통과해도 팩에서 뺀다 | 팩 생성 이후에 일어난 철회 |
| **정오표·우려 표명** | 붙어 있다는 사실을 `warnings`에 올린다 | — 제외하지 않는다. 수치가 정정 대상인지는 사람이 원문에서 본다 |
| **이해충돌** | 진술 원문을 팩에 싣는다 | 진술이 없는 논문의 실제 후원 관계 |

**오배치**가 "**같은 문장의** 다른 숫자"로 한정되는 이유: C1이 quote를 단일
문장으로 강제하기 때문이다. 두 문장짜리 quote는 그 자체로 폐기되므로
(`quote_multiple_sentences`), 오배치가 일어날 수 있는 범위는 항상 quote
문장 하나 안이다 — 다른 문장의 숫자가 섞여 들어올 길이 없다.

```
quote = "Dose was 500 mg daily and triglycerides fell 0.3 mmol/L."
effect = { value: "500", unit: "mg" }     ← 용량인데 효과 크기 자리
→ verified: true
```

`500`도 `mg`도 quote 안에 있고 그 quote는 초록에 있으므로 C1~C4를 전부 통과한다.
게이트가 보증하는 것은 **"이 값이 그 인용문 안에 있고, 그 인용문이 그 PMID의
초록에 있다"**이지 **"이 값이 그 슬롯의 올바른 값이다"**가 아니다.

기준이 초록인 것도 한계다. 스크립트는 efetch로 받은 **초록만** 재조회한다 —
PMC 본문도 출판사 원문도 보지 않는다. 본문에만 있고 초록에 없는 숫자는
지어낸 값과 구별되지 않고 똑같이 탈락한다.

이해충돌도 같은 원칙이다. 팩은 진술 원문을 그대로 실을 뿐 "제조사 후원 연구"라고
**판정하지 않는다.** PubMed의 `GrantList`는 사실상 정부 과제만 색인되고 산업계
후원은 원문 Funding 섹션에만 있는 경우가 많아서, 그 라벨을 기계로 붙일 근거가
없기 때문이다. 진술이 없다는 것도 "이해충돌 없음"이 아니라 "받은 정보가 없음"이다.

결정론적 검사로 막을 수 있는 것은 여기까지다. 의미 판정까지 스크립트로 옮기려면
결국 LLM을 다시 불러야 하고, 그러면 게이트가 게이트가 아니게 된다.
**막을 수 있는 것만 기계로 막고, 남는 것은 사람에게 명시적으로 넘긴다** —
이 한계는 팩의 `warnings`에 매번 실려 나간다.

## MVP 범위

포함: P1 정규화 / P2 근거스캔 / P3 숫자채굴 / P6 검증

미포함: P4 반론논문 · P5 안전성 · P7 카피작성 · **P8 광고법 검사**

> ⚠️ P8이 없으므로 이 플러그인의 출력물을 그대로 발행하면 안 된다.
> 성분 설명과 제품 효능 표방의 경계는 사람이 검토해야 한다.
