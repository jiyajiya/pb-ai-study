# normalize.md — P1 정규화 룩업 테이블

P1이 **가장 먼저** 조회하는 표. 여기 있으면 `inferred: false`,
없으면 추론하되 `inferred: true`로 기록한다.

`exclude_terms`는 검색어에서 빼는 것이 아니다. scout의 **성분 일치 검사**
(off-target 판정)에 그대로 넘겨, 다른 물질 논문이 한 표에 섞이는 것을 막는다.

`decompose_default: true`는 "뭉뚱그린 이름으로는 유효한 근거가 안 나오는 성분"이다.
scout이 별도 신호 없이도 `needs_decomposition: true`를 내야 한다.

## 성분

| 한글명 | ingredient_en | mesh_terms | synonyms | exclude_terms | decompose_default |
|---|---|---|---|---|---|
| 오메가3 | omega-3 fatty acids | `Fatty Acids, Omega-3` | n-3 PUFA, fish oil, EPA+DHA | krill oil, alpha-linolenic acid, flaxseed | **true** (EPA / DHA) |
| EPA | eicosapentaenoic acid | `Eicosapentaenoic Acid` | icosapent ethyl, IPE | DHA, docosahexaenoic, krill oil, alpha-linolenic acid, ALA, EPA+DHA 혼합제 | false |
| DHA | docosahexaenoic acid | `Docosahexaenoic Acids` | DHA-rich algal oil | EPA, eicosapentaenoic, krill oil, ALA, EPA+DHA 혼합제 | false |
| 마그네슘 | magnesium | `Magnesium`, `Dietary Supplements` | Mg supplementation, magnesium oxide, magnesium citrate | magnesium sulfate (정맥/산과), magnesium 함유 복합제 | false |
| 유산균 | probiotics | `Probiotics`, `Lactobacillus`, `Bifidobacterium` | lactic acid bacteria, LAB | prebiotic 단독, synbiotic, fecal microbiota transplantation | **true** (균주 단위) |
| 콜라겐 | collagen peptides | `Collagen`, `Dietary Supplements` | hydrolyzed collagen, collagen hydrolysate, collagen tripeptide | injectable collagen filler, collagen scaffold, collagen 조직공학, 구조단백질로서의 collagen |  false |

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
