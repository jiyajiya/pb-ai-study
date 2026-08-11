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
- 사용자가 그 검색어로 확정하면, 이 표에 한 줄 추가한다.
  그래야 다음 실행이 재현된다.
