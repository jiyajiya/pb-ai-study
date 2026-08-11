# kr-claims — 식약처 고시 대조 (플러그인)

「건강기능식품의 기준 및 규격」 고시전문에서 **인정 기능성 문구**와
**일일섭취량**을 추출해, 논문 근거 팩과 대조한다.

`pubmed-evidence`와 `ingredient-analysis` 양쪽에서 쓴다.
**호출은 경로가 아니라 스킬 이름으로 한다** — `${CLAUDE_PLUGIN_ROOT}`는
자기 플러그인만 가리키므로 다른 플러그인이 이쪽 스크립트 위치를 알 수 없다.
설치 위치가 바뀌어도 스킬 이름은 그대로다.

```
kr-claims/                      ← 플러그인 루트
├── .claude-plugin/
│   └── plugin.json             ← 매니페스트 (마켓플레이스는 상위 pharmacist/)
├── skills/kr-claims/
│   └── SKILL.md                ← 호출 진입점. 출력 해석 규칙이 여기 있다
├── scripts/
│   ├── build_kr_claims.py     ← raw/고시전문.txt → kr_claims.json (관리자용)
│   ├── check_kr_claims.py     ← 조회 / 대조 (스킬이 실행)
│   └── test_kr_claims.py      ← 네트워크 없이 도는 계약 검사
├── data/
│   └── kr_claims.json         ← 84KB. **플러그인에 동봉되어 배포된다**
└── raw/                       ← 고시 원문. 개정 시에만 생겼다 지운다 (git 미추적)
```

**저장소에 남는 고시 자료는 `data/kr_claims.json` 하나다.** 원문 txt를 어딘가에
같이 두면 에이전트가 그쪽을 직접 읽고, 파싱을 거치지 않은 값이 콘텐츠로 흘러든다.

## 설치

두 플러그인은 상위 `pharmacist/`의 마켓플레이스 하나에 함께 등록돼 있다.

```bash
claude plugin marketplace add /절대/경로/pharmacist
claude plugin install kr-claims@pharma-bros
claude plugin install pubmed-evidence@pharma-bros    # 함께 쓸 때
```

`kr-claims`만 설치해도 조회 모드는 동작한다. `pubmed-evidence`는 이게 없으면
후속 대조 단계를 건너뛴다 — 서로 **필수 의존이 아니다.**

## 데이터가 배포 시점에 얼어붙는다

`data/kr_claims.json`은 스냅샷이다. 고시는 개정되고, 개정 때마다 일일섭취량이
바뀌는 원료가 나온다.

- 리포트 첫 줄에 기준 고시 번호·시행일·추출 확인일이 실린다.
- 추출 확인일이 **180일**을 넘으면 스크립트가 경고를 맨 앞에 올린다.
- 갱신하려면 아래 1·2절로 재빌드하고 **`plugin.json`의 `version`을 올려
  재배포**한다. 버전을 안 올리면 설치본이 갱신되지 않는다.

## 이 도구가 하지 않는 것

- **카피 표현이 인정 범위 안인지 판정하지 않는다.** 의미 판정이라 기계로 못 한다.
  인정 문구를 원문 그대로 꺼내 놓을 뿐이고, 대조는 사람이 한다.
- **미등재를 "인정되지 않음"으로 읽지 않는다.** 이 고시는 **고시형 원료**만
  담는다. 개별인정형(대부분의 신소재)은 「건강기능식품 기능성 원료 인정 현황」
  이라는 별도 문서에 있다. 없으면 "확인 필요"다.
- **고시 전체를 담지 않는다.** 뽑아 두는 것은 (원료 × 기능성)의 인정 문구·
  일일섭취량·섭취 시 주의사항뿐이다. 총론(최소함량 규정), 규격, 원료 목록,
  개정 이력은 들어 있지 않다. 그게 필요하면 사람이 고시 원문을 봐야 한다.

## 왜 (원료 × 기능성)이 키인가

같은 원료라도 고시가 기능성마다 다른 섭취량을 정해 둔다.

```
2-16  EPA 및 DHA 함유 유지
  (가) 혈중 중성지질 개선 ･ 혈행 개선 : EPA와 DHA의 합으로서 0.5 ~ 2 g
  (나) 기억력 개선                  : EPA와 DHA의 합으로서 0.9 ~ 2 g
  (다) 건조한 눈을 개선하여 눈 건강   : EPA와 DHA의 합으로서 0.6 ~ 2.24 g
```

원료당 범위 하나로 저장하면 중성지질 콘텐츠에 기억력 기준이 붙는다.
이 키는 pubmed-evidence의 입력(`성분 × 증상`)과 같아서 그대로 조인된다.

**`basis`("EPA와 DHA의 합")를 반드시 함께 본다.** 논문이 EPA 단독 용량을
쓰는데 고시는 합계 기준이면 두 숫자를 직접 비교할 수 없다. 리포트는
비교 결과에 basis를 항상 실어 보낸다.

## 1. 고시전문 받기 (개정 시에만)

1. https://www.mfds.go.kr/brd/m_211/list.do 접속
2. '건강기능식품' 검색 → 「건강기능식품의 기준 및 규격」 고시전문 **최신글**
3. 첨부 zip 다운로드 (아래 명령은 `kr-claims/`에서 실행한다)

```bash
# 파일명이 cp949라 unzip이 실패한다 → python으로 해제
python3 -c "
import zipfile, os
z = zipfile.ZipFile('gosi.zip'); os.makedirs('raw/x', exist_ok=True)
for i, n in enumerate(z.namelist()):
    try: dn = n.encode('cp437').decode('cp949')
    except Exception: dn = n
    print(i, dn)
    open(f'raw/x/part{i}.hwpx','wb').write(z.read(n))
"

# hwpx는 zip+XML이다 → 텍스트 추출
python3 -c "
import zipfile, re
z = zipfile.ZipFile('raw/x/part0.hwpx')
names = sorted(n for n in z.namelist() if 'ection' in n and n.endswith('.xml'))
t = '\n'.join('\n'.join(re.findall(r'<hp:t[^>]*>(.*?)</hp:t>',
      z.read(n).decode('utf-8','ignore'), re.S)) for n in names)
t = re.sub(r'</?hp:[a-zA-Z]+[^>]*?/?>', ' ', t)
t = t.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>')
open('raw/고시전문.txt','w').write(re.sub(r'[ \t]+',' ',t))
print('완료')
"
```

`raw/`는 **빌드 재료일 뿐 저장소에 남기지 않는다**(`.gitignore` 대상).
27만 자짜리 원문을 곁에 두면 에이전트가 그걸 직접 읽으려 들고, 그러면
컨텍스트가 터지거나 파싱을 거치지 않은 값이 흘러든다.
**고시에서 오는 값은 전부 `data/kr_claims.json`을 통해서만 나간다.**

## 2. kr_claims.json 만들기 (개정 시에만)

```bash
python3 scripts/build_kr_claims.py \
  --input raw/고시전문.txt \
  --output data/kr_claims.json \
  --notice "제2026-43호" --effective 2026-06-11 \
  --url "https://www.mfds.go.kr/brd/m_211/view.do?seq=14973"
```

`--notice`·`--effective`는 반드시 채운다. 이 값이 없으면 리포트가
"어느 시점 기준인지"를 말할 수 없고, **기준 시점이 안 남으면 옛 수치를
현행으로 읽는다.** 개정 고시와 직전 고시의 섭취량이 달라진 원료가 무엇인지는
JSON에 남지 않으므로, 재빌드 후 주요 원료는 사람이 눈으로 대조한다.

### 커버리지를 반드시 본다

```
원료 97개 / 행 120개 → data/kr_claims.json
  기능성 문구 없음 21 · 섭취량 없음 2 · 수치 파싱 실패 4
```

- **기능성 문구 없음 21** — 대부분 영양성분(1-x)이다. 템플릿이 달라
  「기능성 내용」 라벨이 블록 안에 없다. 필요하면 사람이 채운다.
- **수치 파싱 실패 4** — 복합 조건("리놀레산은 4.0 g 이상 **또는** 리놀렌산은
  0.6 g 이상")처럼 한 규칙으로 열 수 없는 표기다. `raw`는 그대로 남아 있고
  `parsed: false`라서 비교 단계가 스스로 물러선다.

**빈 값을 "해당 없음"으로 읽지 마라.** 못 읽은 것과 없는 것은 다르다.

## 3. 팩과 대조하기 (콘텐츠마다)

```bash
python3 scripts/check_kr_claims.py \
  --pack evidence-pack.json \
  --claims kr-claims/data/kr_claims.json \
  --ingredient 오메가3 \
  --output kr-report.json
```

`--ingredient`를 생략하면 팩의 `topic`("마그네슘 × 수면")에서 앞부분을 쓴다.

`--pack`을 생략하면 **조회 모드**다. 용량 비교 없이 해당 원료의 고시 내용만 꺼낸다.
`ingredient-analysis`의 0라운드가 쓰는 형태다.

```bash
python3 scripts/check_kr_claims.py \
  --claims data/kr_claims.json --ingredient 마그네슘 --output 마그네슘-고시.json
```

| 종료 코드 | 뜻 |
|---|---|
| 0 | 등재 확인 + 용량 비교 성공 + 상한 초과 없음 |
| 0 | **조회 모드**(`--pack` 없음) — 대조를 안 했으므로 판정할 것이 없다 |
| 2 | 발행 전 확인 필요 — 미등재 / 상한 초과 / 비교 불가 |

0을 "통과"로 읽으려면 **조회 모드가 아니었는지 먼저 봐야 한다.**
`--pack`을 빠뜨린 실행도 0으로 끝난다.

`below`(인정 최소치 미만)는 2를 내지 않는다. 원료 하나에 기능성이 여러 개면
다른 기능성 기준에서 미달로 나오는 게 정상이라, 2로 올리면 늘 2가 된다.

### 실제 출력 예

논문이 500 mg을 쓴 팩으로 대조하면 (종료 코드 2):

```
stderr:   상한 초과 1건
stdout: '마그네슘' → 고시 등재 1건 / 인정 문구 행 1개 / 용량 비교 1건
```

**터미널 요약은 건수까지다.** 어느 수치가 무엇을 넘었는지는 리포트 JSON의
`dose_checks[]`에 있다.

```json
{ "pmid": "12345678", "paper_dose": "500 mg", "status": "above",
  "range_raw": "94.5 ~ 250 mg", "basis": null, "functional_claim": null }
```

`--output`을 생략하면 이 리포트 전체가 stdout으로 나온다. 경고성 줄은
stderr로 가므로 터미널에서는 요약과 순서가 뒤바뀌어 보인다 — 파이프로
넘길 때 `2>&1`을 빠뜨리면 "상한 초과" 줄만 사라진다.

논문이 쓴 500 mg은 국내 인정 상한(250 mg)의 2배다. 이 수치를 그대로
카드뉴스에 옮기면 국내 기준을 벗어난 용량을 권하는 셈이 된다.

여기서 "인정 문구 행 1개"는 **행이 하나 있다**는 뜻이지 문구가 채워져 있다는
뜻이 아니다. 마그네슘(1-16)은 위 커버리지의 「기능성 문구 없음 21」에 드는
영양성분이라 `functional_claim`이 비어 있다 — 용량 비교는 되지만 문구는
사람이 고시에서 확인해야 한다. 빈 값과 없는 값은 다르다.
**근거 검증(P6)이 통과시킨 숫자여도 여기서 걸린다** — 두 게이트가 보는
것이 다르기 때문이다.

## 두 게이트의 역할 분담

| | pubmed-evidence P6 | kr-claims |
|---|---|---|
| 묻는 것 | 이 숫자가 그 초록에 실재하는가 | 이 숫자를 국내에서 써도 되는가 |
| 기준 | PubMed 초록 | 식약처 고시 |
| 실패 형태 | 환각·조작 | 광고법 위반·용량 일탈 |

둘은 서로를 대신하지 못한다. P6을 통과한 500 mg이 여기서 상한 초과로
걸리는 것이 정상 동작이다.

## 보너스 — 안전성 슬라이드 재료

고시의 **섭취 시 주의사항**이 리포트에 함께 실린다.

```
의약품(항응고제, 항혈소판제, 혈압강하제 등) 복용 시 전문가와 상담할 것
```

초록에서 부작용을 뽑는 것은 위험하다 — 언급이 없다는 것이 안전하다는
뜻이 아니기 때문이다. 병용 주의는 초록이 아니라 **여기서** 가져온다.

## 회귀 테스트

```bash
python3 scripts/test_kr_claims.py    # 네트워크 불필요
```

케이스는 전부 고시전문에서 실제로 관찰한 표기다. 파서가 조용히 틀리는
지점(천 단위 쉼표, 괄호 환산값, 복합 조건, micro sign)이 들어 있다.
