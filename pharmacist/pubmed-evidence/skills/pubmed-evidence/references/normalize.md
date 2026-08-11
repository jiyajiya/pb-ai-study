# normalize.md — P1 정규화 룩업 테이블

P1이 **가장 먼저** 조회하는 표. 여기 있으면 `inferred: false`,
없으면 추론하되 `inferred: true`로 기록한다.

`synonyms`는 **같은 물질의 다른 이름**이다. P1이 그대로 scout에 넘기고,
scout은 성분 일치 검사에서 "이 이름은 다른 물질이 아니다"의 근거로 쓴다.
검색어를 넓히는 데는 쓰지 않는다 — 넓히면 off-target이 같이 딸려 온다.

`exclude_terms`는 검색어에서 빼는 것이 아니다. scout의 **성분 일치 검사**
(off-target 판정)에 그대로 넘겨, 다른 물질 논문이 한 표에 섞이는 것을 막는다.
이 칸에는 **초록에서 매칭할 영문 용어만** 쓴다. 한국어 부연은 각주로 뺀다 —
한 칸에 섞이면 무엇이 매칭 대상인지 계약이 모호해진다.

`decompose_default: true`는 "뭉뚱그린 이름으로는 유효한 근거가 안 나오는 성분"이다.
scout이 별도 신호 없이도 `needs_decomposition: true`를 내야 한다.

## 성분

| 한글명 | ingredient_en | mesh_terms | synonyms | exclude_terms | decompose_default |
|---|---|---|---|---|---|
| 오메가3 | omega-3 fatty acids | `Fatty Acids, Omega-3` | n-3 PUFA, fish oil, EPA+DHA | krill oil, alpha-linolenic acid, flaxseed | **true** (EPA / DHA) |
| EPA | eicosapentaenoic acid | `Eicosapentaenoic Acid` | icosapent ethyl, IPE | DHA, docosahexaenoic, krill oil, alpha-linolenic acid, ALA, EPA+DHA combination | false |
| DHA | docosahexaenoic acid | `Docosahexaenoic Acids` | DHA-rich algal oil | EPA, eicosapentaenoic, krill oil, ALA, EPA+DHA combination | false |
| 마그네슘 | magnesium | `Magnesium`, `Dietary Supplements` | Mg supplementation, magnesium oxide, magnesium citrate | magnesium sulfate[^1], multi-ingredient formulation | false |
| 유산균 | probiotics | `Probiotics`, `Lactobacillus`, `Bifidobacterium` | lactic acid bacteria, LAB | prebiotic-only[^3], synbiotic, fecal microbiota transplantation | **true** (균주 단위) |
| 콜라겐 | collagen peptides | `Collagen`, `Dietary Supplements` | hydrolyzed collagen, collagen hydrolysate, collagen tripeptide | injectable collagen filler, collagen scaffold, tissue engineering, structural protein[^2] |  false |

[^1]: 정맥투여·산과 영역. 경구 보충제와 용량대가 다르다.
[^2]: 구조단백질로서의 collagen을 다룬 논문. 보충제 섭취 연구가 아니다.
[^3]: 프리바이오틱만 투여한 군. 유산균 자체의 효과가 아니다.

## 고시 등재명 (`kr_notice_name`)

**PubMed 검색어와 식약처 고시 등재명은 다른 축이다.** 위 표는 논문을 찾는
이름이고, 이 표는 국내 기준을 찾는 이름이다. 하나로 합치면 어느 쪽 이름인지
계약이 모호해진다.

P1이 이 값을 `kr_notice_name`으로 실어 `meta.json` → 팩까지 내려보낸다.
P6-1의 `kr-claims`가 `--ingredient`에 **그대로** 쓴다.

| 한글명 | kr_notice_name | 고시 코드 |
|---|---|---|
| 오메가3 | `EPA 및 DHA 함유 유지` | 2-16 |
| EPA | `EPA 및 DHA 함유 유지` | 2-16 |
| DHA | `EPA 및 DHA 함유 유지` | 2-16 |
| 마그네슘 | `마그네슘` | 1-16 |
| 유산균 | `프로바이오틱스` | 2-51 |
| 콜라겐 | *(공란)* | — |

EPA·DHA가 같은 등재명을 가리키는 것은 오류가 아니다. 고시는 둘을 나누지 않고
"EPA와 DHA의 합"으로 섭취량을 정한다 — 그래서 kr-claims 리포트에
`basis_unconfirmed`가 붙는다. **논문이 EPA 단독 용량을 쓰면 그 숫자는 합계
기준 범위와 애초에 같은 것을 재고 있지 않다.**

### 공란과 미상은 다르다

| 상태 | 뜻 | P6-1이 하는 일 |
|---|---|---|
| 이름이 채워짐 | 고시형 등재 원료다 | `--ingredient`로 넘긴다 |
| **공란** (표에 있고 값이 빈 칸) | **고시형에 없음을 확인했다.** 개별인정형이다 | kr-claims를 부르지 않고, 개별인정형 확인이 남았다고 알린다 |
| **표에 행이 없음** | 아직 확인하지 않았다 | 미상으로 넘긴다. 지어내지 마라 |

콜라겐이 공란인 것은 결손이 아니라 **확인된 사실**이다. 저분자콜라겐펩타이드는
개별인정형이라 「건강기능식품의 기준 및 규격」 고시에 없다.

**등재명을 추론하지 마라.** 틀린 등재명은 *다른 원료의* 인정 문구와 섭취량을
조용히 가져온다 — 빈 값보다 나쁘다. 빈 값은 "확인 필요"로 표시되지만,
틀린 값은 그럴듯한 표가 되어 그대로 옮겨 적힌다.
확인했으면 이 표에 한 줄 추가한다.

## 증상·효능

| 한글명 | outcome_en | mesh_terms |
|---|---|---|
| 중성지방 | triglycerides | `Triglycerides`, `Hypertriglyceridemia` |
| 수면 | sleep quality | `Sleep Quality`, `Sleep Initiation and Maintenance Disorders` |
| 과민성대장증후군 | irritable bowel syndrome | `Irritable Bowel Syndrome` |
| 피부탄력 | skin elasticity | `Skin Aging`, `Elasticity` |

## 표에 없을 때

추론하되 다음을 지킨다.

- `inferred: true` 필수. 사용자에게 정규화 결과를 보여줄 때 "추론"임을 밝힌다.
- `exclude_terms`는 빈 배열로 둔다. 지어내지 마라 — 잘못된 제외어는
  유효한 논문을 조용히 버린다.
- **`kr_notice_name`은 `null`로 둔다.** 고시 등재명은 추론 대상이 아니다 —
  틀린 등재명은 다른 원료의 인정 문구·섭취량을 조용히 가져온다.
  P6-1이 "등재명 미상"으로 처리하고 사람에게 넘긴다.
- 사용자가 그 검색어로 확정하면, 이 표에 한 줄 추가한다.
  그래야 다음 실행이 재현된다. 고시 등재명을 함께 확인했으면
  「고시 등재명」 표에도 한 줄 추가한다.
