---
name: evidence-card
description: 성분명과 증상을 입력하면 PubMed 근거를 수집·검증해 카드뉴스용 evidence-pack.json을 만든다. "마그네슘 수면 근거 뽑아줘", "오메가3 중성지방 카드뉴스 자료", "이 성분 논문 근거 정리해줘" 같은 요청에 사용한다. 건기식·영양제 성분과 특정 효능을 함께 언급하며 콘텐츠 제작이나 근거 확인을 요청하면 명시적 호출이 없어도 사용한다. 숫자 환각을 스크립트로 차단하는 것이 이 스킬의 핵심 목적이다.
---

# evidence-card (MVP: P1-P3 + P6)

## 목적

약사가 카드뉴스를 만들 때 30분 걸리는 "근거 찾고 숫자 뽑고 다시 확인" 과정을
3분으로 줄인다. 최종 산출물은 카피가 아니라 **검증된 숫자 카드**다.

## 불변 원칙

1. **검증되지 않은 숫자는 출력하지 않는다.** `verified: false`인 슬롯은
   `null`로 표시하고 사유를 남긴다. 그럴듯한 숫자를 만들어내는 것은
   이 도구의 유일한 치명적 실패다.
2. **오케스트레이터가 보는 원문은 검증 가능한 최소 단위로 제한한다.**
   초록 전문은 서브에이전트 안에서만 살아 있다 폐기되고, 메인에는
   슬롯에 묶인 `quote` 문장만 올라온다. 이건 오염 "차단"이 아니라
   **표면 축소**다 — quote는 원문 그대로이고, 그래야 P6이 대조할 수 있다.
3. **숫자 검증은 LLM이 하지 않는다.** `scripts/verify_numbers.py`만이 판정한다.

## 실행 절차

### P1. 정규화 (오케스트레이터 직접 수행)

입력: `<성분 한글명> <증상 한글명>`

1. `references/normalize.md` (스킬 폴더 기준)의 매핑 테이블을 먼저 조회한다.
2. 테이블에 없으면 영문 학술용어를 추론하되, **추론했다는 사실을
   `normalized.inferred: true`로 기록**한다.
3. 산출:

```json
{
  "ingredient_ko": "마그네슘",
  "ingredient_en": "magnesium",
  "outcome_ko": "수면",
  "outcome_en": "sleep quality",
  "mesh_terms": ["Magnesium", "Sleep Quality", "Sleep Initiation and Maintenance Disorders"],
  "synonyms": ["Mg supplementation", "magnesium oxide", "magnesium citrate"],
  "exclude_terms": ["magnesium sulfate (정맥/산과)"],
  "decompose_default": false,
  "inferred": false
}
```

`exclude_terms`와 `decompose_default`는 표에서 그대로 옮긴다.
표에 없어 추론한 경우 `exclude_terms`는 빈 배열로 둔다 — 지어낸 제외어는
유효한 논문을 조용히 버린다.

정규화 결과를 사용자에게 한 줄로 보여주고 진행한다. `inferred: true`면
"추론한 검색어"임을 함께 밝힌다.
검색어가 틀리면 이후 전부가 틀리므로, 이 지점에서만 사용자 개입을 허용한다.
사용자가 검색어를 확정하면 `references/normalize.md`에 한 줄 추가한다.

### P2. 근거 스캔 (evidence-scout 서브에이전트 1회)

- `evidence-scout` 서브에이전트에 위임한다 (Task 도구).
- P1 산출 JSON을 그대로 입력으로 넘긴다.
- 반환값에서 `tone`, `pmids`, `needs_decomposition`만 사용한다.
- `off_target: true`인 PMID는 다른 물질의 논문이다. 표에도 넣지 않는다.
- **`needs_decomposition: true`이면 여기서 멈추고** 사용자에게
  하위 분해 항목(균주명·제형 등)을 제시한 뒤 재실행 여부를 묻는다.
  (예: "유산균"은 균주 단위로 내려가야 유효한 근거가 나온다)

### P3. 숫자 채굴 (abstract-miner 서브에이전트 병렬)

- `pmids` 중 **`off_target: false`인 것만** 골라 `abstract-miner`
  서브에이전트를 **동시에** 호출한다.
- 최대 8개. 그 이상이면 scout이 매긴 순위 상위 8개만.
- 순차 실행 금지 — 앞 논문의 숫자가 뒤 논문 추출에 오염을 일으킨다.

### P3-1. 중간 산출물 저장 (P6의 입력)

P6 스크립트는 컨텍스트가 아니라 **파일**을 읽는다. 두 파일을 작업
디렉토리에 저장한 뒤 P6으로 넘어간다. 저장하지 않으면 P6은 실행되지 않는다.

**`raw_slots.json`** — miner 반환값을 **손대지 않고** 배열로 모은 것.

```json
[ { "pmid": "...", "design": "...", "direction": "...",
    "subgroup_only": false, "slots": { ... } }, ... ]
```

miner가 `{"pmid": "...", "error": "fetch_failed"}`를 반환한 것도 그대로
배열에 넣는다. 스크립트가 검증 불가로 집계해 팩의 경고에 올린다.
값을 고치거나 보기 좋게 다듬지 마라 — 그 순간 검증이 무의미해진다.

**`meta.json`** — P1 정규화 결과와 P2 scout 판정에서 **아래 5개 키만** 옮긴다.

```json
{
  "topic": "마그네슘 × 수면",
  "tone": "T2",
  "tone_reason": "R4: RCT 5편 중 3편 positive이나 모두 결핍군 한정",
  "evidence_map": { "meta_analysis": 1, "rct": 5, "...": 0 },
  "guideline_hit": false
}
```

`topic`은 P1 입력의 `<성분 한글명> × <증상 한글명>`이다. 나머지 4개는
scout 반환값에서 그대로 복사한다. **지어내지 마라** — scout이 주지 않은
키는 넣지 않는다. 스크립트는 없는 키를 `null`로 채우고 진행한다.

### P6. 검증 (스크립트)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_numbers.py" \
  --input raw_slots.json --meta meta.json --output evidence-pack.json
```

입력은 P3-1에서 저장한 두 파일이다.

경로는 반드시 `${CLAUDE_PLUGIN_ROOT}` 기준으로 쓴다.
상대경로는 사용자의 작업 디렉토리에 따라 깨진다.

- 스크립트가 PubMed에서 초록을 **독립적으로 재조회**해 대조한다.
- LLM이 판단에 개입하지 않는다.
- 종료 코드 **0**: 팩 생성 완료 (슬롯 폐기는 정상 흐름이라 0이다).
  **2**: 팩을 신뢰할 수 없음 — 입력이 비었거나, 검증 불가 논문이
  검증 성공 논문보다 많다. 이때는 팩을 사용자에게 제시하지 말고
  무엇이 조회되지 않았는지 먼저 보고한다.
- 폐기 개수는 종료 코드가 아니라 stdout과 팩의 `verification`에 있다.

### 출력

`evidence-pack.json` 저장 후, 사용자에게는 **표 한 장**으로 요약한다.

| 슬롯 | 값 | PMID | 검증 |
|---|---|---|---|
| 효과 크기 | ... | 12345678 | ✅ |
| 용량·기간 | ... | 12345678 | ✅ |
| 대상 조건 | ... | 23456789 | ✅ |
| 형태·세부 | — | — | ❌ 초록에 명시 없음 |

효과 크기는 값 옆에 **비교 대상**(위약/실약/복용전후)을 함께 적는다.
`comparator`가 null이면 "비교 대상 미상"으로 적는다 — 위약 대비라고
단정하지 마라. 팩의 `warnings`는 그대로 사용자에게 전달한다.
단위 기준이나 비교 대상이 논문마다 다르면 한 표에 나란히 놓지 않는다.

폐기된 슬롯은 반드시 표시한다. 빈 슬롯은 실패가 아니라 정보다 —
효과크기 슬롯이 비었다는 것은 그 자체로 T3(정정형) 신호다.

## 톤 라우팅

scout이 반환한 `tone`에 따라 후속 콘텐츠 방향이 갈린다. MVP에서는
카피를 쓰지 않으므로 **판정 결과만 전달**한다.

| 톤 | 의미 | 카드뉴스 방향 |
|---|---|---|
| T1 | 근거 강함 + 가이드라인 존재 | 설명형. 단, 안전성 슬라이드 필수 |
| T2 | 조건부 (하위집단·혼재) | 조건형. 근거→조건→반론 3장 세트 |
| T3 | 근거 약함 / 부정 우세 | 정정형. 통념→실제→대안 |

## 하지 않는 것 (MVP 범위 밖)

- 카피 작성 (P7)
- 광고법 검사 (P8) — **이것 없이 발행하면 안 된다는 경고를 출력에 포함할 것**
- 반론 논문 수집 (P4), 안전성 추출 (P5)

## 회귀 테스트

`tests/fixtures.md` (스킬 폴더 기준)의 4개 주제로 검증한다.
톤 판정이 기대값과 다르면 라우터가 깨진 것이다.
