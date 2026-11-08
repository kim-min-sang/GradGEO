# GradGEO 구현 인수인계 명세 — v2

- 기준일: 2026-10-20
- v1(GEO_Implementation_Handoff_2026-10-20.txt) 대비 논리 결함을 수정한 판본이다.
- 목적: 이 문서를 다른 AI 코딩 모델에 그대로 전달해 연구용 구현을 시작한다.
- 수정된 곳은 본문에 `【v2-Cn】`으로 표시했고, 근거는 맨 앞의 변경 이력에 있다.

> **【v2.1 업데이트 (2026-10-24) — 아래 본문보다 우선한다】**
> 1. **맥락 공변량 c는 주 설계에서 뺀다. head 입력은 z뿐이다.** C1의 원래 걱정 중 "encoder가 관련성을 z에 몰래 담는다"는 2단계 학습(C2)으로 해결됐다. 남은 걱정인 "관련성 효과를 전략 효과로 착각한다"는 dev 개입 게이트(C4)로 직접 검증한다. c는 다음 경우에만 ablation으로 추가한다.
>    - 경쟁 강도 c: 질의별 확률 보정이 크게 어긋날 때 (사건이 후보 풀 200 안의 상대 순위라서 영향이 작을 것으로 예상)
>    - 관련성 c: 게이트에서 gradient 방향이 실제 순위 변화와 맞지 않을 때
>    - 도메인: "도메인 있음 vs 없음" ablation(비용 0). Rewriter에도 도메인을 주지 않는다. StageAware처럼 스스로 판단하게 해서 비교 조건을 같게 한다.
> 2. **Query Grouping은 아키텍처 단계에서 뺀다.** 남는 일은 하나다: 질의 임베딩 유사도가 매우 높은 쌍(기본 ≥ 0.9; Quora 같은 paraphrase 데이터 대비)을 같은 split에 묶는다. 클러스터링, 그룹 단위 배치, 그룹 기반 hard negative, 그룹 단위 평가 확장은 쓰지 않는다.
> 3. split: SAGEO 질의 2,700개(9개 도메인 × 300)를 도메인별 같은 비율로 학습 60 / 검증 10 / dev 개입 10 / 테스트 20으로 나눈다(테스트 ≈ 540). SAGEO는 학습하는 부분이 없어서 2,700개를 모두 테스트에 썼다.
> 4. 단위 표기: 학습 = (q, D) listwise, 최적화 = (q, d | D) (타깃 d 하나만 수정하고 D의 나머지는 고정).

---

## v2 변경 이력 (v1 대비)

| ID | 심각도 | 문제 | 수정 |
|---|---|---|---|
| C1 | 치명 | p_R·p_K가 z(18개 전략 특징)만의 함수다. 그런데 Retrieval/Rerank 통과 여부는 대부분 **질의-문서 주제 관련성**이 결정하고, 관련성은 전략 특징이 아니다. 그러면 (a) head가 거의 예측을 못 하거나, (b) encoder가 관련성을 z 안에 몰래 담아 해석성이 깨진다. | **편집하지 않는 맥락 공변량 c(q,d)**를 도입한다. head는 (z, c)를 받고, 최적화할 때 c는 고정하며 gradient는 z로만 흐른다. v1의 `optional_query_context` 자리를 구체화한 것이라 금지된 54D fusion과는 무관하다. **⚠ c에는 재작성이 바꾸지 않는 "사전(pre-treatment)" 변수만 넣는다.** 넣을 것: 질의-문서 연결 정보(SAGEO: 해당 질의의 Google 검색 후보에 포함되는지, 그때의 Google 순위 — 크롤링 시점에 고정된 값이라 재작성의 영향을 받지 않음), 도메인, 질의 난이도/경쟁 문서 점수 분포처럼 타깃 문서와 무관한 값. 넣으면 안 되는 것: 원본 BM25 점수·순위, reranker 점수. 이들은 z(예: 키워드 정합성)의 효과가 나타나는 **결과(매개변수)**라서, c에 넣으면 z가 가져야 할 효과를 c가 대신 흡수해 버린다. |
| C2 | 치명 | L_total이 stage loss를 encoder(LoRA)까지 역전파한다. 그러면 z_i가 "전략 i의 강도"라는 의미를 잃고 판별에 유리한 latent로 표류한다. "z_i = 원본 특징 강도"라는 정의와 학습 목표가 서로 충돌한다. | **기본은 2단계 학습**이다. ① encoder는 L_feat만으로 학습(특징 측정기 역할), ② encoder를 freeze하고 head를 학습한다. end-to-end joint 학습은 ablation으로만 두고, 이때는 z-feature 일치도(검증셋 MAE/Spearman)를 반드시 같이 보고한다. |
| C3 | 중대 | 900:100 음성 샘플링을 하면 p_R이 부풀려진다. ∇log p_s = (1−p_s)·∇logit_s이므로 **보정 오류가 단계 간 gradient 가중치를 직접 왜곡**한다(p_R 과대 → Retrieval 신호 과소). | **R의 모집단을 "후보 풀"로 재정의**한다(§3.4). 후보 풀 = BM25 Top-M (기본 M=200, config). 질의의 Google 후보는 학습에 섞지 않고, test 타깃을 고를 때 관련성의 대리 지표로만 쓴다. 그 안의 실제 비율로 학습하므로 인위적인 보정이 거의 필요 없다. 계산 때문에 서브샘플링을 했다면 상수 offset 대신, 전체 후보 풀을 쓰는 검증셋으로 temperature scaling을 학습한다. 보정은 보고 항목이 아니라 **최적화의 전제조건**이다. |
| C4 | 중대 | 최대 위험(가설 A: 관측 선호 ≠ 개입 효과)을 마지막 test에서야 처음 확인한다. | R(BM25)과 K(Qwen3-Reranker)는 **로컬·결정론적이라 개입 효과를 싸게 직접 측정**할 수 있다. 별도의 dev-intervention set에서 "gradient 부호 vs 실제 순위 변화" 일치도를 MVP의 go/no-go 게이트로 둔다. final blind test와는 분리한다. |
| C5 | 중대 | 문서별(document-specific) 최적화라는 핵심 주장을 검증할 baseline이 빠져 있다. | **(i) 모든 전략 최대화(z*=1), (ii) 모집단 평균 gradient 방향(문서 무관)**을 필수 baseline으로 추가한다. (ii)를 이기지 못하면 "문서별 최적화" 주장은 성립하지 않는다. |
| C6 | 중대 | head가 사실상 선형이면 g = Σ_s (1−p_s)·w_s가 되어, 3개의 고정 방향을 문서별 가중치로 섞은 것에 불과하다. | 그 자체로 해석 가능한 기준 모델이므로 명시해 둔다. 문서 특이성을 원하면 Score 계층에 z×c 상호작용(작은 MLP 또는 bilinear)이 필요하다. 선형 vs 비선형 head를 ablation한다. |
| C7 | 중 | LLM은 18개 차원의 연속 목표(예: 0.63)를 동시에 맞출 수 없다. | 전략마다 **이산 앵커 레벨(예: 0/0.25/0.5/0.75/1) 루브릭**을 정의하고, z*를 레벨로 양자화해 전달한다. 기본값은 "목표 레벨이 바뀐 차원만, 최대 top-k개"이다. 인터페이스는 계속 `Rewrite(d, z*)`이며, 현재 측정값 z를 참고 맥락으로 함께 주는 것은 허용한다(delta를 전달하는 것은 아님). |
| C8 | 중 | log p 공간에서는 p_R≈0인 문서의 gradient가 폭주한다. 1-step 고정 η는 스케일에 취약하다. | 기본값은 **정규화 step(g/‖g‖) + 차원별 max_delta + [0,1] 투영**이다. 다단계 PGD는 옵션으로 둔다. |
| C9 | 중 | Generation 인용은 확률적이고 위치 편향이 있다. 1회 실행 binary 레이블은 노이즈가 크다. | 동일 입력을 N회 반복해 인용 빈도를 soft label로 쓰고(BCE 타깃 ∈ [0,1]), **반복마다 GE 입력 순서를 무작위로 섞는다.** 그러면 위치가 z와 무관한 외생 변수가 되어 c에 넣을 수 있다(섞지 않으면 위치는 rerank의 결과라서 매개변수가 됨). |
| C10 | 중 | test 타깃 문서 선정 기준이 없다. 주제가 무관한 문서는 어떻게 재작성해도 올라오지 않아 결과를 희석한다. | **주 실험**: SAGEO 프로토콜 그대로, rerank Top-10(생성 단계 입력) 중에서 무작위로 타깃을 고른다. **확장 실험**: 해당 질의의 Google 후보 중 Top-10에 못 든 문서도 타깃으로 쓴다("관련은 있는데 놓친 문서"; Google 결과를 관련성의 대리 지표로 사용, 사람이 만든 gold 레이블은 불필요). 원본의 단계별 도달 위치(R 탈락 / K 탈락 / G 탈락 / 인용)별로 층화해 보고한다. |
| C11 | 중 | Top-100/Top-10은 "예시"인데, 연쇄법칙은 cutoff가 **실제 파이프라인과 같을 때만** 성립한다. | cutoff는 평가 환경(SAGEO Arena 설정)의 실제 값과 일치시켜 config로 둔다. |
| C12 | 경 | Query Grouping이 아키텍처 Stage 1로 들어가 있지만, 실제 역할은 split·배치·hard negative 구성이다. | 데이터 준비 단계로 내린다(기여로 주장하지 않음). 필수 용도는 group-aware split이다. |
| C13 | 경 | CL의 anchor와 positive가 정의되어 있지 않다. h_s(18D)에 대한 SupCon이 무엇을 더해 주는지 불분명하다. | 기본 CL = **질의를 anchor로 한 listwise InfoNCE(질의 내 logit 대상)**이다. 질의 난이도를 상쇄해 상대 선호를 학습한다. h_s에 대한 SupCon은 옵션/ablation이다. |
| C14 | 경 | SAGEO 서술과 수치(10절)는 이 문서 작성 과정에서 원문과 대조 검증하지 않았다. | 논문 작성 전에 원문과 대조할 것. 대조하기 전에는 인용하지 않는다. |

---

## 0. 다른 AI에게 주는 최상위 지시

너는 정보검색(IR), RAG, 대조학습, 딥러닝 최적화, GEO(Generative Engine Optimization) 논문 구현을 담당하는 시니어 ML 연구 엔지니어다.

아래는 연구자가 확정한 아키텍처다. 핵심 설계를 임의로 바꾸지 말고, 연구 가설과 아직 미정인 구현 요소를 구분해 재현 가능한 학습·추론·테스트 코드를 설계하라.

진행 순서:
1. 확정 사항, 미정 사항, 기술적 위험을 짧게 재정리한다.
2. 데이터 스키마, 모듈 구조, Loss/수식, 입출력 인터페이스를 설계한다.
3. MVP를 구현한다. 【v2-C4】 MVP에는 dev-intervention 게이트를 포함한다.
4. 유닛 테스트, 학습 로그, 평가 지표, 재현용 설정을 제공한다.
5. ablation과 대규모 실험을 쉽게 추가할 수 있도록 모듈화한다.

이 문서에 없는 것: 모델 체크포인트, GPU, 데이터셋, API 키, 18개 전략의 확정 목록. 이런 것을 확보한 것처럼 가정하거나 성능 숫자를 꾸며내지 말고, config / 인터페이스 / 명시적 TODO로 남긴다. "인용률 향상 보장", "SAGEO보다 우수함이 증명됨" 같은 표현은 쓰지 않는다.

---

## 1. 연구 목적과 핵심 가설

목표:
- 전체 RAG 파이프라인(Retrieval → Reranking → Generation)에서 특정 문서의 최종 인용 가능성을 높일 GEO 재작성 전략과 그 강도를 추정한다.
- 원본 문서들의 단계별 생존/탈락 관측으로 RAG가 선호하는 문서 특성을 학습한다.
- 18개 GEO 전략 특징을 해석 가능한 연속 벡터 z ∈ [0,1]^18로 표현한다.
- 3개의 Stage-specific head로 단계별 조건부 통과 확률을 학습하고, 그 곱으로 최종 인용 확률을 근사한다.
- ∇_z log p_final로 문서별 목표 전략 벡터 z*를 계산한다.
- 쓰지 않는 것: 54D concat/fusion MLP, RL policy, 학습 중 수정본 GE 반복 평가 루프.

핵심 가설 (검증 대상이며 확립된 사실이 아님):
> 원본 문서의 단계별 선택/인용 패턴으로 학습한 미분 가능한 선호 모델의 gradient가, 새로운 문서에 적용할 유효한 GEO 수정 전략을 안내할 수 있다.

【v2-C1】 이 가설은 **관련성 등 편집 불가 요인 c를 통제한 상태에서**, 편집 가능한 전략 특징 z의 효과에 대한 주장이다.

---

## 2. 확정 아키텍처

```
[데이터 준비] Query Grouping (Qwen3 Embedding) — group-aware split, 배치, hard negative   【v2-C12】
      ↓
Stage A — Strategy Feature Encoder (특징 측정기)
   Qwen3 + LoRA → Linear → Sigmoid,  z(q,d) ∈ [0,1]^18
   학습: L_feat만 사용 (기본). 이후 freeze.                                               【v2-C2】
      ↓
Stage A' — Context Covariates (편집 불가, 최적화 중 고정)  【v2-C1】 ※ v2.1: 주 설계에서 제외, ablation 전용
   c(q,d) = [Google 후보 포함 여부·Google 순위, 도메인, 질의 난이도·경쟁 문서 점수 분포, (G만) 무작위로 섞은 GE 입력 위치, ...]
   ※ 원본 BM25/reranker 점수는 z 효과의 결과(매개변수)이므로 c에서 제외
      ↓
Stage B — Three Stage-specific Heads (독립 가중치, 입력 z와 c 공유)
   Head R / K / G : (z, c) → h_s ∈ R^18, logit_s → p_s
   Loss: listwise CL(질의 anchor) + BCE (R은 후보 풀 기준)                               【v2-C3, C13】
      ↓
Stage C — log p_final = log p_R + log p_K + log p_G
      ↓
Stage D — Gradient-based Strategy Optimization (z만 변수, c와 모든 파라미터 고정)
   z* = Proj_[0,1]( z + η · g/‖g‖ ), 차원별 max_delta, 편집 불가 전략 mask                 【v2-C8】
      ↓
Stage E — LLM Rewriter:  d' = Rewrite(d, z*)   (z* 레벨 양자화, top-k 변경 차원)           【v2-C7】
      ↓
Stage F — Test: 실제 RAG 재실행 (BM25 → Qwen3 Reranker → GE)
```

| 구분 | 내용 |
|---|---|
| Training | 원본 문서의 RAG 로그만 사용한다. |
| Validation | 원본 홀드아웃에서 확률·랭킹·보정을 평가한다. |
| Dev-intervention 【v2-C4】 | 소규모 수정본에 대해 **R/K만** 실제로 재실행한다(로컬이라 저비용). gradient 방향의 타당성을 확인하는 용도다. η, top-k 등 하이퍼파라미터는 여기서 정한다. |
| Final blind test | 처음 보는 (q,d)에 대해 전략 산출 → 재작성 → 전체 RAG 재실행. 한 번만 연다. |

---

## 3. 데이터 수집·표현·레이블

### 3.1 문서 표현
- 최소 필드: query_id, query_text, doc_id, document_body
- 가능하면 추가: title, meta_description, headings, jsonld, body_passages (구조 필드와 본문을 분리)
- passage 단위로 랭킹되는 경우 passage→doc_id 매핑을 유지한다. 인용·Hit 집계 단위(문서/passage)를 명시하고 일관되게 쓴다.
- 【v2 확정】 파이프라인은 SAGEO와 같이 passage(256토큰 chunk, overlap 64) 단위로 돌린다. 레이블과 최적화는 **문서 단위**로 한다. 문서 순위 = 그 문서 passage 중 최고 순위(SAGEO `find_doc_rank`와 동일).
  - 후보 풀: 최고 passage가 BM25 Top-200 안인 문서
  - y_R: 최고 passage가 BM25 Top-100 안
  - y_K: 최고 passage가 rerank Top-10 안
  - y_G: 그 문서의 passage가 하나라도 인용됨
- 효율: reranker 점수는 (질의, passage) 쌍마다 독립이다. 원본 Top-200 passage 점수를 캐시해 두고, 실험마다 재작성된 타깃 문서의 passage만 다시 채점한다.

### 3.2 질의별 RAG 로그
- 후보 문서 ID, Retrieval 순위/점수
- Rerank 입력 집합, Rerank 순위/점수
- GE 입력 문서 ID와 그 **입력 순서**
- 답변 텍스트, 인용 문서 ID
- 모델 버전, 프롬프트 버전, corpus snapshot, seed
- 【v2-C9】 GE는 동일 입력으로 N회(config, 예: 3~5) 반복 실행해 모든 실행 결과를 기록한다.

### 3.3 단계별 레이블
【v2-C11】 cutoff K_R, K_K는 실제 평가 파이프라인의 값과 일치시킨다. 아래 100/10은 예시다.

| 레이블 | 양성 조건 | 정의되는 모집단 |
|---|---|---|
| y_R(q,d) | Retrieval Top-K_R에 포함 | 후보 샘플 전체 |
| y_K(q,d) | Rerank Top-K_K에 포함 | y_R = 1인 문서만 |
| y_G(q,d) | N회 실행 중 인용 비율 (soft, ∈[0,1]) 【v2-C9】 | y_R = y_K = 1인 문서만 |

해당 모집단 밖에서는 null로 두고 loss mask를 0으로 한다. 0으로 채우지 않는다. 인용 수는 질의마다 가변이다(0~K_K).

### 3.4 R의 모집단: 후보 풀 【v2-C3】
- 정의: p_R ≈ P(Top-K_R 진입 | q, d ∈ 후보 풀(q)), 후보 풀(q) = BM25 Top-M(q)
  - M은 config. 기본 200 → 양성 100(1~100위) : 음성 100(101~200위). 비용은 2,700 × 200 = 54만 쌍.
  - R의 사건 경계는 반드시 실제 파이프라인 cutoff(K_R=100)여야 한다. 예를 들어 Top-100 안을 50:50으로 나누면 "Top-50 진입"이라는 파이프라인에 없는 사건을 학습하게 되어 p_R·p_K·p_G 연쇄가 깨진다.
- 근거: 재작성 타깃은 관련 문서로 한정된다(C10). 코퍼스의 나머지 문서는 최적화 대상이 아니므로 p_R을 추정할 필요가 없다. 따라서 p_final은 "후보 풀 안의 문서"에 대해서만 정의된다.
- 양성: Top-K_R
- 음성: 후보 풀 − Top-K_R, 즉 BM25 K_R+1~M위 문서. 주제는 비슷한데 탈락한 hard negative라서, head가 주제가 아니라 **전략 차이**를 학습한다.
- 코퍼스 전체에서 무작위로 뽑은 음성은 사용하지 않는다. 그런 음성은 "주제가 맞는가"만 가르친다.
- 손실별 샘플 사용:
  - CL(listwise InfoNCE): 질의마다 후보 풀 전체를 사용한다. 비율에 둔감하므로 1:1로 맞추지 않는다.
  - BCE: 후보 풀 안의 실제 비율로 학습한다.
- 계산 때문에 음성을 서브샘플링했다면: 서브샘플링하지 않은(후보 풀 전체) 검증셋으로 temperature scaling을 학습해 보정한다.
- K(Top-K_R 안에서)와 G(Top-K_K 안에서)는 원래 실제 모집단 비율이므로 서브샘플링하지 않고 전부 사용한다.

### 3.5 근접 중복 질의 처리 (데이터 준비) 【v2-C12 → v2.1】
- Qwen3 query embedding으로 질의 간 유사도를 계산하고, 유사도 ≥ 0.9(config)인 쌍은 같은 split에 묶는다. 원본 질의와 강한 변형 질의가 split을 넘지 않게 하는 것이 목적이다.
- 클러스터링, 그룹 단위 배치, 그룹 기반 hard negative는 쓰지 않는다.
- 서로 다른 질의의 레이블을 하나의 정답처럼 합치지 않는다.

### 3.6 JSONL 인터페이스 계약 (예시 값)
```json
{
  "query_id": "q001", "query": "...", "group_id": "g12",
  "doc_id": "d001",
  "document": {"title": "...", "meta": "...", "headings": ["..."], "jsonld": "...", "body": "..."},
  "is_relevant": true,
  "retrieval_rank": 28, "retrieval_score": 12.3, "y_R": 1,
  "rerank_rank": 7, "rerank_score": 0.81, "y_K": 1,
  "ge_input_position": 7, "cite_count": 2, "ge_runs": 5, "y_G": 0.4,
  "feature_labels": [0.5, null, 0.75, "..."],
  "split": "train"
}
```
Top-K_R에 들지 못한 문서는 rerank_*, y_K, ge_*, y_G가 모두 null이다.

---

## 4. Stage A — 해석 가능한 18D 특징 (측정기)

정의:
```
e(q,d) = Encoder_Qwen3_LoRA(q, d)
z(q,d) = sigmoid(W · e(q,d) + b) ∈ [0,1]^18
```

- 각 차원은 미리 정의한 하나의 GEO 전략/특징에 대응한다. 18개 전략의 이름, 정의, 측정 기준은 **사용자가 확정**한다. 확정 전까지는 strategy_schema.yaml로 교체 가능하게 만든다.
- z_i는 "원본 문서에 이미 존재하는 해당 특징의 강도"다. 앞으로 가할 변경량이 아니다.
- 【v2-C7】 strategy_schema.yaml의 각 전략 항목에 들어갈 것:
  - 정의
  - 앵커 레벨 루브릭 (0 / 0.25 / 0.5 / 0.75 / 1 각각이 어떤 텍스트 상태인지 + 예시)
  - 질의 의존 여부 (예: entity mirroring은 q에 의존)
  - 적용 가능 필드 (구조 / 본문)
  - 편집 가능 여부, 안전 플래그
- 【v2-C1】 BM25/reranker 신호를 z가 설명할 수 있으려면, 질의 의존 특징(키워드·엔티티 정합성 등)이 스키마에 포함되어야 한다.
- 특징 레이블 출처: 규칙 기반 측정기 + LLM 판정자(루브릭 앵커 사용). 일부 표본은 사람이 검수해 판정자 신뢰도(일치도)를 보고한다.
- `L_feat = masked_MSE(z, feature_labels)` (또는 masked_BCE). 레이블이 없는 차원은 mask한다.
- 【v2-C2】 **기본 학습: L_feat만 사용하고 이후 freeze한다.** joint(stage loss를 encoder까지 역전파) 학습은 ablation이며, 이때도 z-feature 일치도를 반드시 보고한다.
- 참고: 추론 시 gradient는 z에 대해서만 계산하므로 encoder의 미분 가능성은 쓰이지 않는다. encoder의 역할은 "판정자 레이블의 저비용 근사(amortization)"다. 따라서 MVP에서는 encoder 없이 판정자 점수를 z로 바로 써도 된다(§13).

---

## 5. Stage B — 독립된 3개 Head

```
h_s     = Head_s(z) ∈ R^K              s ∈ {R, K, G}, 가중치는 head마다 별도   【v2.1: c 제외】
logit_s = b_s(h_s)                      (ablation에서만 a_s(c) + b_s(h_s, c))   【v2-C6】
p_s     = sigmoid(logit_s)
```

- (ablation 전용) a_s(c): 편집 불가 맥락의 기여분으로, 최적화 중에는 상수다.
- b_s: 전략 특징의 기여분. 문서 특이성은 비선형 head(z 차원 간 상호작용)와 (1−p_s) 가중치에서 나온다. ablation에서는 z×c 상호작용을 허용한다. b_s가 z에 대해 선형이면 g = Σ_s(1−p_s)·w_s가 된다(C6). 선형/비선형 head를 ablation한다.
- h_R, h_K, h_G를 54D로 concat하지 않는다. Head 간 표현을 합산하거나 평균하는 별도 규칙도 만들지 않는다.
- 모든 logit은 z에 대해 미분 가능해야 한다.

학습 집합:

| Head | 양성 | 음성 |
|---|---|---|
| R | 후보 풀 중 Top-K_R | 후보 풀 중 나머지 (§3.4) |
| K | Top-K_R 중 Top-K_K | Top-K_R 중 나머지 |
| G | Top-K_K 문서, soft label y_G | (soft label이므로 별도 음성 집합 없음) |

Loss:
```
L_CL_s  = 질의 q를 anchor로 한 listwise InfoNCE  【v2-C13】
        = − Σ_{d+} log [ exp(logit_s(d+)/τ) / Σ_{d ∈ 후보(q)} exp(logit_s(d)/τ) ]
          (G는 soft label이므로 ListNet 형태의 분포 매칭으로 대체 가능)
L_s     = L_CL_s + λ_BCE_s · BCE_s(p_s, y_s)
L_total = Σ_s w_s · L_s      (기본 2단계 학습에서는 L_feat가 Stage A에서 따로 학습됨)
```

- 한 질의 안에 양성만 있거나 음성만 있으면 CL은 건너뛰고 BCE만 적용한다.
- h_s에 대한 SupCon은 옵션/ablation이다.
- CL은 질의 안의 상대 선호를, BCE는 보정된 절대 확률을 담당한다. CL만으로는 확률이 되지 않는다.
- G 표본이 적다는 이유만으로 G에 역비례하는 큰 가중치를 주지 않는다.

---

## 6. Stage C — 조건부 확률 결합

```
p_R ≈ P(R | q, d, env)
p_K ≈ P(K | R, q, d, env)
p_G ≈ P(G | R, K, q, d, env)
log p_final = log p_R + log p_K + log p_G      (각 p는 epsilon으로 clamp)
```

- 연쇄법칙이 성립하려면 각 head가 **정의된 조건부 모집단에서** 학습되어야 하고, cutoff가 실제 파이프라인과 같아야 한다(C11).
- 【v2-C3】 원본 검증셋에서 단계별 reliability diagram, Brier, ECE를 측정한다. 후보 풀 기준 검증셋으로 temperature scaling을 거친 확률만 최적화에 사용한다. 보정이 틀리면 단계 간 gradient 가중치 (1−p_s)가 틀어진다.
- 경쟁 문서 집합에 의존하므로 이 모델은 근사적 surrogate다. c에 경쟁 맥락(예: 질의별 점수 분포 통계)을 넣어 확장할 수 있다.

---

## 7. Stage D — Gradient 기반 전략 최적화 (RL 없음)

```
g  = ∇_z log p_final(z; c)      — c와 모든 파라미터는 고정, z는 leaf tensor
   = Σ_s (1 − p_s) · ∇_z logit_s
z* = Proj_[0,1]( z + η · m ⊙ g / (‖m ⊙ g‖ + ε) )     【v2-C8】
     (차원별 |z*_i − z_i| ≤ max_delta,  m = 편집 가능 mask)
```

- (1 − p_s) 가중치 덕분에, 이미 통과할 가능성이 높은 단계의 신호는 자동으로 줄어들고 병목 단계의 신호가 커진다. 이것이 문서별 최적화의 메커니즘이다.
- 1-step이 기준 구현이다. 다단계 PGD와 early stopping은 옵션이다.
- η, max_delta, top-k, 반복 수는 **dev-intervention set에서만** 정한다. 정한 뒤 고정하고 기록한다.
- 점검할 것: sigmoid 포화, off-manifold z* (예: 학습 분포에서 z*의 kNN 거리가 비정상적으로 큰 경우를 로그), 단계 간 gradient 상충 (∇log p_R · ∇log p_K < 0인 비율).
- strategy_top_k는 "Generation에서 인용되는 문서 수"와 무관한 편집 예산이다.
- 【v2 확정】 **문서별 자동 k(주 방법)**: 축 i의 1차 예측 이득 g_i·Δz_i(Δz_i = 양자화된 z*_i − z_i)가 편집 비용 λ보다 큰 축만, 이득이 큰 순서로 최대 k_max(기본 5)개를 고른다. 즉 Σ_{i∈S} g_i·Δz_i − λ·|S|를 최대화하는 집합 S를 고른다. k = 0(수정하지 않음)도 허용한다. λ는 dev-intervention set에서만 정한다. 비교용 고정 k = 1, 3, 5.
- z*는 surrogate 기준의 국소 최적일 뿐이다. 실제 GE에서의 최적이라고 주장하지 않는다.

---

## 8. Stage E — LLM Rewriter

인터페이스:
```
d_prime = Rewrite(document=d, target_strategy=z_star, context=RewriteContext)
```
- 금지: `Rewrite(d, z_star - z)` 형태로 delta를 전달하는 것.
- 【v2-C7】 RewriteContext에 들어가는 것:
  - 질의 q
  - strategy_schema의 루브릭 앵커
  - z*를 앵커 레벨로 양자화한 값
  - 편집 가능 필드
  - (선택) 현재 측정값 z — 참고용이며 delta가 아니다
  - 변경 대상 차원 = §7의 자동 k 규칙으로 고른 집합 S (고정 k ablation에서는 |g_i·Δz_i| 상위 k개)
- 제약: 사실 보존. 출처·인용·통계 위조 금지. 주장 왜곡 금지. JSON-LD 문법 유효성. 핵심 엔티티 유지. 과도한 키워드 반복, 프롬프트 인젝션, 검색 조작용 내용 금지.
- baseline과 본 방법은 **동일한 Rewriter 모델과 프롬프트 골격**을 쓴다. 바뀌는 것은 전략 지시 부분뿐이다.
- 재작성 후 측정: z(d')와 z*의 차원별 오차(실현율), 사실성 검사, 편집 거리.
- Rewriter 호출 비용은 존재한다. 호출 수, 토큰 수, 지연시간을 로그로 남긴다.

---

## 9. Stage F — 평가

### 9.1 Training / Validation
원본 로그만 사용한다. 지표: AUC/AP, BCE, Brier/ECE, 단계별 랭킹 품질(nDCG/Recall), z-feature 일치도, group 일반화.

### 9.2 Dev-intervention 게이트 【v2-C4】
- 소규모 dev 질의/문서에서 전략 하나씩 레벨을 올리거나 내려 재작성한다.
- BM25와 reranker만 재실행해 실제 Δrank를 측정한다(로컬이라 API 비용 0).
- 지표: 예측 Δlog p_R, Δlog p_K와 실제 Δrank의 부호 일치율 및 순위 상관.
- 판정: 일치율이 우연 수준(≈50%)이면 가설 A를 재검토한 뒤에야 GE 비용을 들인 실험으로 넘어간다.
- 이 단계에서 η, top-k를 정한다. final test는 아직 열지 않는다.

### 9.3 Final blind test (한 번만)
1. 【v2-C10】 타깃 선정: 처음 보는 질의에 대해 **후보 풀 안의 관련 문서** 중에서 고르며 SAGEO Arena 프로토콜을 따른다. 원본의 도달 단계(R 탈락 / K 탈락 / G 탈락 / 인용)로 층화한다.
2. 고정 코퍼스에서 원본 d의 baseline을 기록한다(GE는 N회 반복).
3. z* → d'를 한 번 재작성한다.
4. 타깃 d만 d'로 교체해 재색인한다. 원본과 수정본을 동시에 넣지 않는다.
5. 같은 q, 같은 Retriever/Reranker/Generator/seed/프롬프트로 전체 RAG를 재실행한다(N회).
6. 단계별 진입 여부/순위, 인용 빈도를 기록한다.
7. 지표: Citation Rate, Retrieval H@20, Rerank H@10, ΔRank. 층(stratum)별로도 보고한다.
8. 품질·사실성·안전성, 호출 수, 비용, 지연시간을 기록한다.
9. 통계: 질의 group 단위 cluster bootstrap 신뢰구간, paired 비교, 다중 seed.

### 9.4 필수 baseline 【v2-C5】
모두 같은 Rewriter를 사용한다.
- 원본 (수정 없음)
- 무작위 z* (같은 편집 예산)
- **z* = 모든 전략 최대 (all-in)**
- **모집단 평균 gradient 방향** (학습셋 평균 g를 모든 문서에 동일 적용) — 문서별 최적화 주장을 검증하는 핵심 비교
- 고정 GEO 단일 전략 / SAGEO 2-전략 조합 / SAGEO StageAware / AutoGEO
- 단일 최종 인용 head (p_final 직접 학습)
- 질의 사용 baseline: AgenticGEO, MAGEO (둘 다 (q, d)를 보는 방법이라 질의 정보 조건이 같다)

**【v2.1 확정】 질의 정보 2×2 비교 (주 결과표)**

|  | 질의 안 씀 | 질의 씀 |
|---|---|---|
| StageAware | 원본 StageAware (SAGEO 프롬프트 그대로) | StageAware + Q |
| GradGEO | GradGEO − Q | GradGEO (전체) |

- 같은 열 안의 비교(정보 조건이 같음)로 방법 효과를 보이고, 같은 행 안의 비교로 질의 정보의 효과를 본다. "질의를 알아서 이긴 것"이라는 반론을 막는 핵심 표다.
- StageAware + Q: 원래 프롬프트에 `Target query: {q}`와 "이 질의에 대해 최적화하라"는 문장만 추가하고 나머지는 그대로 둔다. 수정한 프롬프트 전문은 부록에 공개한다.
- GradGEO − Q: z에서 질의 종속 축(본문 질의 정합성, 앞부분 답변성, 구조 필드 질의 정합성 등 `dependency: query`인 축)을 빼고 head를 다시 학습한다. 남은 z는 문서만의 함수이므로 질의를 쓰지 않는 방법이 된다.
- 모든 칸을 같은 코퍼스, 같은 타깃, 같은 Rewriter 모델로 우리 환경에서 다시 돌린다. 코퍼스를 다시 크롤링하므로 SAGEO 논문 수치와 직접 비교하지 않는다.

---

## 10. SAGEO Arena와의 차별점

> 【v2-C14】 이 절의 서술과 수치는 v1에서 옮겨 온 것으로, 아래 '확인된 사실'을 제외하고는 원문과 대조 검증되지 않았다.
>
> 확인된 사실(2026-10-21, arXiv HTML v2에서 확인): 질의 2,700개(9개 데이터셋 × 300), 질의당 Google Custom Search 결과 최대 100개(평균 63개)를 모아 문서 171,003개로 코퍼스를 구성. BM25 Top-100 → reranker → Top-10을 생성 단계에 입력. 타깃은 생성 단계에 들어간 Top-10 중 무작위 1개. qrels(사람이 판정한 관련성 레이블)는 사용하지 않음. 따라서 baseline Rerank H@10 = 1.00은 타깃 선정 방식상 당연한 값이다. Table 2·3의 Baseline 행(Retrieval H@20 0.58, Rerank H@10 1.00, Cite 0.50)도 원문과 일치함을 확인했다. Table 4의 retriever별 Retrieval H@20 baseline: BM25 0.58 / Dense 0.68 / Hybrid 0.67. Table 3(조합 4개 + StageAware) 수치도 원문 HTML과 일치함을 확인했다(2026-10-22). Table 2(Both) 주요 행: AutoGEO 0.44 / −20.15 / 0.58 / −1.93 / Cite 0.44, Quote 0.79 / +8.87 / 0.85 / −0.29 / Cite 0.51, All-in-One 0.69 / +1.96 / 0.77 / −0.62 / Cite 0.52 (순서: Ret H@20 / Ret ΔRank / Rerank H@10 / Rerank ΔRank / Cite). 평가 질의는 2,700개(도메인당 300). HTML에서 옮긴 값이라 인용 전에 PDF와 한 번 더 대조할 것.
>
> 나머지 서술과 수치는 논문에 쓰기 전에 원문(arXiv 2602.12187, Sections 3.3, 5.1~5.3, Appendix B·E; github.com/happysnail06/SAGEO_Arena)과 반드시 대조할 것.

### 10.1 SAGEO의 방법 (v1 서술 유지)
- 파이프라인: Retrieval(BM25 계열 + 필드별 검색/RRF) → Reranking(Qwen3-Reranker-4B) → Generation(GPT-5-mini). 구조 필드와 본문을 구분한다.
- 실험: 8개 기존 전략, All-in-One/AutoGEO, 2-전략 조합(EasyLang+Quote, EasyLang+Stats, Fluency+Quote, Fluency+Stats).
- StageAware: 단계별 실험·사례 분석에서 얻은 원칙을 **고정 재작성 프롬프트**에 반영한 것이다. 단계마다 Rewriter를 따로 순차 호출한 것이 아니다.
- SAGEO는 단계 간 상충을 실험적으로 관찰하고 대응했다. "단계 간 관계를 고려하지 않았다"고 쓰면 안 된다.

### 10.2 v1에 기재된 Table 3 (원문 대조 필요)

| 전략 | Ret H@20 | Ret ΔRank | Rerank H@10 | Cite |
|---|---|---|---|---|
| Baseline | 0.58 | - | 1.00 | 0.50 |
| EasyLang+Quote | 0.67 | −0.25 | 0.78 | 0.54 |
| EasyLang+Stats | 0.65 | −1.77 | 0.76 | 0.51 |
| Fluency+Quote | 0.66 | −0.63 | 0.77 | 0.54 |
| Fluency+Stats | 0.65 | −1.63 | 0.76 | 0.53 |
| StageAware | 0.75 | +4.86 | 0.80 | 0.58 |

StageAware의 Rerank H@10(0.80)은 baseline(1.00)보다 낮다. "모든 단계를 개선했다"고 쓰지 않는다.

### 10.3 논문에 쓸 수 있는 차별점
1. 해석 가능한 연속 전략 표현 z ∈ [0,1]^18 (앵커 루브릭 기반)
2. 단계별 선호 학습 (3개 head, 관련성 c 통제)
3. 조건부 확률 결합 p_final = p_R · p_K · p_G
4. ∇_z log p_final을 통한 문서별 공동 최적화 — 병목 단계에 자동으로 가중됨
5. 최적화 과정에 GE 반복 피드백이나 RL이 없음

대비 문장 (예시):
> SAGEO Arena empirically identifies stage-dependent effects and codifies them into a fixed StageAware rewriting prompt. Our method instead learns differentiable stage-specific preference models, controlling for non-editable relevance, and optimizes a document-specific continuous strategy target against an estimated final citation objective.

금지 표현:
- "SAGEO가 단계 간 상충을 전혀 고려하지 않음"
- "조건부 확률 곱으로 전략 간 인과적 상호작용을 학습함"
- "18D 임베딩 자체가 학습 파라미터"
- 실험 전의 "SAGEO보다 우수함"

---

## 11. 기술적 위험

| ID | 위험 |
|---|---|
| A | 관측 선호 ≠ 개입 효과. 최대 위험이다. 【v2-C4】 dev-intervention 게이트로 조기 검증하고, final test에서 확정한다. |
| A' | 【v2-C1】 관련성 교란. c를 통제하지 않으면 z의 gradient가 "주제를 바꿔라" 같은 방향을 가리키거나 의미가 없어진다. |
| B | CL만으로는 확률이 아니다. BCE, 조건부 레이블, 후보 풀 정의, calibration이 필요하다. |
| C | 해석 가능성은 자동으로 생기지 않는다. 루브릭 + 판정자 신뢰도 + (joint 학습 시) z 의미 표류 감시가 필요하다. |
| D | 재작성 실현율 z(d') vs z*를 측정해야 한다. |
| E | 경쟁 문서, cutoff, chunking, 질의 의도, GE 입력 위치에 의존한다. |
| F | selection bias, 클래스 불균형, 인용의 확률성(N회 반복으로 완화), 단일 클래스 질의. |
| G | 국소 선형 근사, off-manifold, 포화, 이산적 편집. |
| H | group leakage, 문서 중복. |
| I | 최초 원본 RAG 레이블을 구축하는 비용(GE N회 반복 포함)은 존재한다. |
| J | 구조/본문을 구분하지 않으면 SAGEO와 공정 비교가 불가능하다. |
| K | test leakage. dev-intervention set과 final blind test를 분리한다. |
| L | white-hat 제약: 사실 보존, 위조 금지. |

---

## 12. 구현 산출물

```
configs/
  experiment.yaml            # cutoff K_R/K_K, 음성 샘플링, GE 반복 N, η/max_delta/top-k
  strategy_schema.yaml       # 18개 전략: 정의, 앵커 루브릭, 질의 의존 여부, 필드, 편집 가능, 안전 (사용자 확정)
data/
  schema.py
  build_stage_labels.py      # 조건부 null 처리, soft y_G
  context_features.py        # c(q,d)                                    【v2-C1】
  feature_labeling.py        # 규칙 + LLM 판정자 → feature_labels        【v2】
  query_grouping.py          # group-aware split
  dataset.py
models/
  strategy_encoder.py        # Qwen3 + LoRA + 18D (L_feat 전용, freeze 가능)
  stage_heads.py             # (z, c) → h_s, logit_s
  citation_model.py          # temperature scaling, log p_final
training/
  contrastive_losses.py      # listwise InfoNCE, ListNet(soft)
  train_encoder.py           # Stage A
  train_heads.py             # Stage B
  validate.py                # calibration 포함
optimization/
  gradient_strategy.py       # 정규화 step, mask, max_delta, 투영, 상충 진단
rewriting/
  rewriter_interface.py
  prompt_builder.py          # 앵커 레벨 양자화, top-k
rag/
  retrieval.py
  reranking.py
  generation.py              # N회 반복, 인용 파싱
  evaluation.py
experiments/
  dev_intervention.py        # R/K 개입 게이트                           【v2-C4】
  compare_baselines.py       # all-in, 평균 gradient 포함                【v2-C5】
  ablation.py
tests/
  (v1 목록) + test_candidate_pool.py, test_context_fixed_in_grad.py
```

### 최소 수용 테스트
1. `z.shape[-1] == 18`, 모든 좌표 ∈ [0,1].
2. 3개 head는 독립 모듈이며 각각 18D 표현과 scalar 확률을 반환한다.
3. K loss는 Top-K_R 밖에서, G loss는 Top-K_K 밖에서 0이다(mask).
4. p_final == p_R · p_K · p_G이며 log 공간 계산과 일치한다.
5. gradient는 z의 18개 좌표로만 흐른다. c, encoder, head 파라미터는 변하지 않는다. 【v2-C1】
6. 최적화 과정에 RL과 54D concat/fusion이 없다.
7. Rewriter에 전달되는 목표값은 z*다(delta 아님).
8. Mock RAG에서 타깃 문서만 교체되고 ID 추적이 유지된다.
9. 수정본의 전체 RAG 실행은 test 모드에서만 가능하다. dev 모드에서는 R/K만 실행된다.
10. 비용, 호출 수, latency가 로그로 남는다.
11. 【v2-C3】 R 학습 샘플이 전부 후보 풀 안에서 나온다. 서브샘플링을 했다면, 합성 데이터에서 temperature scaling 후 후보 풀 기준의 실제 양성 비율이 복원된다.

### 권장 ablation
- 3 head vs 단일 head vs head별 독립 최적화
- CL+BCE vs BCE only vs CL only
- 【v2-C1】 c 있음 / 없음
- 【v2-C2】 2단계 학습 vs joint (z 의미 표류 지표와 함께)
- 【v2-C6】 선형 vs 비선형 head
- gradient vs 무작위 vs all-in vs 평균 방향 vs greedy vs 고정 전략
- feature supervision 유무, 스키마 변형
- η / step 수
- 【v2 확정】 편집 개수: 고정 k = 1, 3, 5 vs 자동 k(λ 기반). 자동 k에서는 문서별 k 분포도 보고한다. sweep은 dev에서 R/K만(비용 0) + 소규모 GE 파일럿으로 하고, 최종 테스트는 정한 설정으로 1회만 돌린다.
- 【v2 확정】 head별 선호 분석: 전략 축 × head(R/K/G)별 gradient 부호 표와 상충 비율(∇log p_R·∇log p_G < 0인 문서 비율). "3 head vs 단일 head" ablation의 이유를 설명하는 근거로 쓴다.
- Query grouping 유무
- body-only / structural-only / both

---

## 13. 구현 우선순위 【v2】

MVP는 가장 싼 경로로 가설 A부터 확인한다.

1. **환경**: SAGEO Arena 코드, 코퍼스, 질의를 재사용해 원본 RAG 로그를 수집한다(GE N회 반복).
2. **z**: encoder 없이 규칙 + LLM 판정자 점수를 z로 바로 쓴다(18개 스키마 확정 후).
3. **c 계산 + head 학습**: 처음에는 로지스틱/작은 MLP로 시작하고, R은 후보 풀 기준으로 학습한다.
4. **Dev-intervention 게이트**(R/K, 로컬): gradient 부호 일치율을 확인한다. → go/no-go
5. 통과하면: Qwen3+LoRA encoder로 z를 amortize한다(판정자 근사, 비용 절감).
6. Rewriter와 루브릭 실현율을 측정한다.
7. Final blind test와 baseline/ablation을 실행한다.

### 최종 확정 문장 (v2)
```
[데이터 준비: Query Grouping]
→ 18D Strategy Feature z (루브릭 기반 측정기, 기본 freeze) + 편집 불가 맥락 c
→ 3 Stage-specific CL+BCE Heads (z, c 입력; 보정 확률)
→ log p_final = log p_R + log p_K + log p_G
→ gradient-based z* optimization over z only (c 고정; no 54D fusion, no RL)
→ d' = Rewrite(d, z*)
→ dev-intervention gate (R/K, local) → held-out full RAG blind test
```
