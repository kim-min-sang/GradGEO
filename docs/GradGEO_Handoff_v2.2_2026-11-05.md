# GradGEO 구현 인수인계 명세 — v2.2

- 기준일: 2026-11-05 (C37까지 반영: 2026-11-12)
- 이전 판: `GradGEO_Handoff_v2_2026-10-20.md`(v2 본문 + 맨 앞 v2.1 업데이트 블록). **이 문서는 그 둘을 대체한다.** 이전 판은 경위 기록용으로만 남긴다.
- 전략 정의·판정·Rewriter 프롬프트 문장: `configs/strategy_schema.yaml` v0.10. 이 문서와 스키마가 다르면 스키마의 축 정의를, 이 문서의 파이프라인 설명을 따른다.
- 목적: 이 문서를 다른 AI 코딩 모델에 그대로 전달해 연구용 구현을 시작한다.
- v2.2에서 바뀐 곳은 본문에 `【C15】`처럼 표시했고, 근거는 아래 변경 이력에 있다. v2의 C1~C14 중 아직 유효한 내용은 본문에 흡수했다.

---

## v2.2 변경 이력 (v2·v2.1 대비)

| ID | 바뀐 것 | 내용 | 이유 |
|---|---|---|---|
| C15 | 학습 단위 | 학습 예시를 (질의, 문서)에서 **(질의, 메타 + passage)**로 바꾼다. passage마다 자기 z와 자기 레이블(R/K/G)을 갖는다. 평가는 SAGEO처럼 문서 단위(문서 순위 = 가장 높은 passage의 순위). | SAGEO 엔진은 문서가 아니라 passage를 검색·재정렬·인용한다. 문서 단위 예시에서는 단계마다 "그 문서를 대표하는 passage"가 달라지는 불일치가 생겼다. |
| C16 | 판정 단위 | 판정 LLM 입력 = **메타(제목·메타 설명·JSON-LD·헤딩) + passage 1개**. 엔진(재정렬기·GE)이 보는 단위와 같다. 핵심 주장 목록, 본문 구간 S0~S4, 잠금 질문을 모두 없앴다. 본문 축은 판정 LLM이 "정의에 맞는 문장 번호"만 답하고 레벨은 코드가 개수로 정한다. | 판정이 문서 전체를 보고 엔진이 passage를 보면, 판정이 재는 것과 엔진이 반응하는 것이 다르다. 핵심 주장·구간은 무엇을 고르냐에 따라 점수가 바뀌는 운 요소였다. |
| C17 | 전략 축 | 후보 19개 → 17개 → **20개**. 위치 축 2개(앞부분 정보 밀도, 앞부분 답변성) 제외. "질의 측면 포괄성 [Q]"는 추가하지 않음. 환경 B로 바꾸면서 SFE 축 3개(목록·표, 강조, 문단 길이)를 추가했다(2026-11-06, 모두 규칙 계산). 링크는 추출에서 빼므로 축이 없다. | SAGEO는 문서 순위를 가장 높은 passage로 정하고(MaxP), 엔진 입력에 passage 위치가 없어 위치 효과가 나타날 경로가 없다. SAGEO 질의는 대부분 한 가지만 묻는다. |
| C18 | 처방·재작성 범위 | 타깃 문서의 **대표 passage 하나**의 z로 처방(g, z*)을 계산하고, **그 passage만** 고친다(선택된 축에 문서 단위 필드가 있으면 그 필드도). 대표 passage = 원본 실행에서 그 문서의 가장 높은 passage(검색 탈락 문서는 BM25 기준, 검색 통과 문서는 재정렬 기준). | 처방을 계산한 글과 고치는 글이 같아야 실현율을 정확히 잰다. 문서 순위는 가장 높은 passage가 정한다. SAGEO 방법들은 질의를 모르고 고치므로 문서 전체를 고치지만, GradGEO는 질의를 알아서 그 질의에서 중요한 passage를 고를 수 있다. |
| C19 | 재작성 규칙 | 원문에 없는 수치·인용·출처 추가를 **허용**한다(GEO·AutoGEO·SAGEO 비교 방법과 같은 조건). 얼버무린 주장을 단정적으로 바꾸는 것도 허용한다. 남는 공통 규칙은 "이미 있는 사실을 바꾸거나 반대로 만들거나 지우지 않는다". 날조 자동 검사(숫자·따옴표·NER 대조)와 재료 없는 축 잠금은 없앴다. | GradGEO는 모든 전략을 실제로 적용해 문서별 최적 전략을 찾는 구조다. 비교 방법들과 같은 조건이어야 한다. |
| C20 | 의미 검사·길이 | 의미 보존 점수(KPR/KPC, BERTScore)를 재지 않고, 숫자 길이 제한(±10%)도 두지 않는다. Rewriter에는 숫자 기준 없는 "원래와 비슷한 길이" 권고만 준다(2026-11-06). 길이 비율·편집량만 코드로 기록해 보고한다(거르거나 되돌리지 않음). Rewriter는 타깃마다 한 번만 부른다. | GradGEO는 passage 하나만 고치므로 문서 전체를 비교하면 거의 항상 같게 나와 의미가 없다. 길이 제한이 없으면 내용을 더하면서 기존 문장을 지울 압력도 사라진다. 비교 대상인 SAGEO 코드에도 의미 검사가 없고, SAGEO의 both 설정 프롬프트에는 길이 규칙도 없다. (연구자 확인 2026-11-05) |
| C21 | 환경 | 본 실험은 **환경 B만** 쓴다(2026-11-06 연구자 확정, 10-31의 "A만"을 바꿈). 환경 B = SAGEO 엔진(검색 결합 방식만 C29로 바꿈)·문서 집합·다른 필드는 그대로, 본문만 원본 HTML에서 trafilatura 마크다운으로 다시 뽑아 목록·표·강조·문단·본문 안 소제목을 보존(링크는 SAGEO 기본 추출처럼 뺌). 청커는 SAGEO 청커에 "줄바꿈도 단위 경계, 줄바꿈 보존"만 더했다(`rag/chunking.py`, 평문에서는 SAGEO와 결과가 같음). | SAGEO(환경 A)는 원본 본문의 구조는 지우지만 재작성본은 LLM 출력 그대로 저장해 목록·굵게가 남는다(`sageo_methods.py` post_processing, `optimizer.py`). 원본과 재작성본의 형식이 달라지고, 원본에 구조 변동이 없어 head가 구조 효과를 배울 수 없다. B는 이 비대칭을 없애고, 웹 문서를 LLM에 넣는 많은 도구처럼 마크다운을 쓴다. 한 환경만 쓰므로 비용은 A와 거의 같다. 논문 근거는 일관성·현실성이며 "우리 전략에 맞춘 엔진"이 아니다. |
| C22 | 타깃 선정 | 테스트 질의마다 층 3개(검색·재정렬·생성)에서 무작위로 1개씩. 생성 층 = SAGEO 프로토콜. 관련성은 UMBRELA 2점 이상. | 검색 탈락 문서까지 대상으로 삼아 "검색 탈락 문서 구제"를 보이고, 생성 층으로 SAGEO와 직접 대응시킨다. (2026-10-29 확정) |
| C23 | head 입력 | head 입력 = **[z \| 질의 임베딩 PCA(0·16·32차원 중 검증으로 선택, 0 = z만)]**. gradient는 z로만 흐른다. 임베딩 모델은 Qwen3-Embedding-0.6B(질의 PCA, [Q] 축 코사인, split의 근접 중복 질의 묶기에 공통). 데이터셋·도메인 구분 입력은 쓰지 않는다(실제 사용 때 새 질의에는 데이터셋 표시가 없다). 맥락 공변량 c는 ablation에서만. | (2026-10-24, 10-28 확정. 2026-11-06: 임베딩 모델 연구자 확인, 데이터셋 입력 제외는 연구자 제안, 차원 후보 0은 기존 "z만" ablation을 검증 선택에 합친 것) |
| C24 | 손실 | head마다 **순위가중 all-pairs 쌍비교 + λ·BCE**. v2의 listwise InfoNCE를 대체한다. | (2026-10-22 확정) |
| C25 | 비교 방법 | SAGEO의 All-in-One을 추가한다. 무작위 처방 정의를 명시한다. "같은 처방·본문 전체 범위"(②)와 GradGEO−Q 칸은 **보류**: 목록에만 두고 본 실험 직전에 연구자에게 다시 묻는다. | 두 칸은 비용이 추가된다(연구자 결정, 2026-11-05). |
| C26 | 레벨 계산 | 본문 축 레벨 = 문장 개수 5단계(0/1/2/3/4개 이상). 문제 문장을 세는 축(유창성·쉬운 표현·권위 어조·조각 독립성)은 반대 방향. 신뢰도 미달이면 있다/없다로 바꾼다. | passage 길이가 거의 같아(256토큰) 개수 ≈ 밀도다. passage 문장 수 중앙값이 8이라 비율 경계(1/10)는 0.75 레벨이 거의 생기지 않는다. (연구자 확인 2026-11-05) |
| C27 | 정제 ③ | "한 레벨에 몰린 비율 ≤ 90%" → "가장 많은 레벨이 아닌 예시가 학습셋에 1,000개 이상". | passage 단위는 예시 수가 수십 배라, 비율 기준이면 예시가 충분한 드문 특징(인용구·출처)까지 잘린다. 표본 passage 32,994개 정규식 추정: 단위 있는 숫자 14%, 따옴표 구절 14%, 출처 표현 10%. (연구자 확인 2026-11-05) |
| C28 | 색인 단위 | **URL당 한 번만 색인한다.** SAGEO는 (질의, URL) 쌍마다 문서를 하나씩 색인해, 여러 질의에서 온 같은 웹페이지가 전역 인덱스에 여러 번 들어간다. 문서 = URL, 그 URL이 나온 질의 목록은 메타데이터로 남긴다. | 코퍼스 172,632쌍 중 26,416개(15.3%)가 다른 질의 쪽 복사본을 가진다(URL 9,799개, 최대 66개: ofac.treasury.gov FAQ). 복사본이 한 질의 순위에서 자리를 여러 번 차지하고, 타깃을 고쳐도 다른 복사본은 원본으로 남아 원본과 수정본이 함께 색인된다(SAGEO도 타깃 doc_id만 바꿈). URL당 1회 크롤링이라 복사본 내용은 같다. (연구자 확정 2026-11-06) |
| C29 | 검색 결합 | **passage 하나를 열 5개(본문 passage, 제목, 메타 설명, 소제목, JSON-LD)짜리 한 줄로 색인하고, 열마다 BM25를 매겨 점수를 더한다(가중치 모두 1.0, Elastic `most_fields`와 같은 방식).** SAGEO의 열별 순위 합(가중 RRF, 열마다 Top-200)을 대체한다. SAGEO 설계 중 의미 단위(passage + 구조 정보), 필드 분리, 같은 가중치는 그대로다. 코드: `rag/build_unit_index.py`, `rag/retrieval.py`(기본 `--engine unit`). SAGEO 방식은 `--engine sageo`로 비교용으로 남긴다. | SAGEO 코드는 열마다 200위까지만 본다. 그래서 본문 200위 밖 passage는 본문 점수가 0이고 문서 메타 점수만 받아, 한 문서의 passage가 모두 같은 점수가 된다. 논문 식 (2)는 모든 요소에 순위가 있다고 쓰며, 200위 제한과 순위로 합친 이유는 논문 v1·v2 어디에도 없다. 질의 300개 실측(열마다 300위): Top-300 passage 중 자기 본문 점수가 있는 것 26%, 동점 73%, 100위 선을 동점 묶음이 가로지르는 질의 58%(묶음 중앙값 24개, 99.9%가 한 문서). passage 단위 head가 문서 안 순서로 갈린 레이블을 배우게 된다. 본문 순위만 끝까지 매긴 SAGEO 식(B0)은 동점 24%, 본문 몫 7%로 A와 거의 같았다. B1: 동점 7%, 100위 선 0.7%. Top-100 점수 몫은 본문 20%, 제목 19%, 설명 17%, 소제목 22%, JSON-LD 22%로 열 간 점수 크기가 비슷하다. A와 Top-100 문서 84% 겹침. 신호 보존 시험(문서 하나 고치기): 제목을 지우면 Top-100 밖으로 A 56%, B1 65%, 한 텍스트로 섞은 대조 엔진(B2) 7%. 같은 질의 단어를 제목/본문에 넣으면 100위 안 진입이 A 57/67%, B1 90/71%, B2 91/91%였다. B2는 제목과 본문을 구별하지 못하고, B1은 SAGEO가 걱정한 "섞으면 필드 신호가 흐려진다"를 일으키지 않는다. 코드 `prep/engine_check/`, 결과 `data/sageo/engine_check/`. (연구자 확정 2026-11-08) |
| C30 | 후보 풀 깊이, 질의 단어 축 | **N = 300.** **#11 (a)·#17의 "질의 핵심어 등장 비율"을 핵심어마다 IDF 가중으로 센다**(가중치 = passage 단위 색인의 BM25 idf). split 구현: `prep/split.py` → `data/sageo/split.json`. | N: B1 사전 점검에서 UMBRELA 2점 이상 비율 1~100 51% / 101~200 39% / 201~300 30% / 301~500 28%, 질의당 관련 문서 101~N위 N=200 5.1개, 300 8.9개, 500 15.7개. 질의를 제목/본문에 넣었을 때 Top-100 진입은 101~300위 출신 91%/70%, 301~500위 출신 77%/43%라 passage 하나를 고치는 GradGEO에 301위 아래는 맞지 않고, 500은 판정 대상 passage가 1.45배(81.2만). IDF: 같은 개수만 세면 "what"(idf 1.95)이 "deficiency"(5.38)와 같은 몫을 받는다. 무료 축 8개 R head(`experiments/r_head_free.py`, 검증 질의 270개) 질의 안 AUC: 개수 0.80 → IDF 0.87, #11+#17만 0.81 → 0.89. Rewriter 문장은 그대로. (연구자 확정 2026-11-08) |
| C31 | #17 눈금 | **#17(구조 필드 질의 정합성)은 분위수 변환 없이 원래 비율(IDF 가중 등장 비율)을 z로 쓴다.** 수준 경계는 다른 continuous 축과 같은 0.25 / 0.75. #11은 분위수 유지. | 미니 비교(dev 194개 타깃, 검색 단계 H@100): 분위수 눈금에서는 수준 1.0("핵심어가 모두 있음") = 비율 0.58 이상이라, 비율 중앙값 0.68인 38개가 "이미 최고"로 #17 처방에서 빠졌고 GradGEO-R은 그 38개에서 25~57%, #17을 받은 156개에서 99.4%(StageAware+Q 98.7%). 원래 비율로 바꾸면 #17 처방 156 → 182개(LLM 호출 전 확인), 검증 질의 안 AUC 0.869 → 0.878. 스키마 `v0.6 수정` 참고. (연구자 확정 2026-11-09) |
| C32 | 처방 규칙 | **현재 수준에서 출발해, 고칠 축은 g 비중(abs(g_i) / ‖g‖ ≥ τ)으로, 목표 수준은 head 예측(log p_final 최대, 칸 수 제한 없음)으로 정한다**(§8.2). η·max_delta·λ 대신 τ·k_max. #17 수준 경계 0.3 / 0.9, 수준 0 문장 "few or none"(스키마 v0.7). | 이전 규칙은 실제 z 값에 걸음(η 0.5를 g 비중대로 나눔, #17은 보통 0.45)을 더한 뒤 반올림해서, 같은 수준 0.5라도 비율 0.26인 문서는 처방이 없고 0.70인 문서는 있었다(Rewriter는 둘 다 ▶ 0.5로 본다). 미니 비교(dev 194개, LLM 호출 전): #17 처방 182 → 191(+1칸 137, +2칸 54), #11 60 → 79, GradGEO-R이 놓친 11개 중 #17 처방 3 → 8. 재작성 후 H@100 0.943 → 0.974(189/194, StageAware+Q 0.979 = 190), H@20 0.928(StageAware+Q 0.887); 이전 규칙에서 놓친 6개가 모두 들고 잃은 타깃은 없다. 남은 5개는 메타 비율이 이미 1.0(3개)이거나 처방을 받았지만 Rewriter가 목표에 못 미친 경우(2개). (연구자 확정 2026-11-09) |
| C33 | split, GE 반복 | **split = 학습 60 / 검증 20 / 테스트 20(질의 1,620 / 540 / 540).** 이전의 검증 10과 dev 개입 10을 검증 하나로 합쳤다(학습·테스트 질의는 그대로). 이전 dev는 `val_mini`(검증의 부분집합)로 남아 미니 비교가 쓴다. head 설정(조기 종료, PCA 차원, 축 제거)과 처방 설정(τ, k_max, 수준 경계)을 모두 검증 split에서 정한다. **GE는 질의마다 1회만 돌린다**(학습 레이블·검증·테스트 모두, 입력 순서는 질의마다 무작위). y_G는 0/1. | 검증을 둘로 나눠도 두 번째 검증 결과에도 운이 섞여, 데이터를 떼어 줄 만큼 이득이 없다. 하이퍼파라미터는 검증 하나에서 grid로 정하는 게 표준이다. GEO 논문 중 학습/검증/테스트를 나눈 것은 GEO(8,000 / 1,000 / 1,000)와 E-GEO(1,000 / 500 / 2,000)뿐이고 검증을 둘 둔 논문은 없다(AutoGEO·AgenticGEO·MAGEO는 검증 없음, SAGEO는 학습 없음). 테스트 540은 방법 간 차이 판정에 필요해 유지(최종 실험 비용은 테스트 크기 × 비교 방법 수로 정해진다). GE 1회: 1회 레이블의 기댓값이 인용 확률과 같아 head가 배우는 대상은 같고 잡음은 질의 수로 평균된다; 같은 비용이면 질의를 늘리는 쪽이 낫다. SAGEO도 원본·최적화본 GE를 1회씩 돌린다. (연구자 확정 2026-11-10) |
| C34 | 재정렬 실행 | **Qwen3-Reranker-4B는 2025-06-09 revision(`f16fc5d`)으로 고정하고, 프롬프트는 SAGEO `QwenReranker` 그대로**(system/user 메시지, 10,000토큰 자르기, yes/no 접미사, p(yes)/(p(yes)+p(no)), SAGEO 출력의 `<\|im_end\|>` 두 번 포함). 프롬프트마다 질의·passage가 들어갔는지 검사한다. 점수는 GPU 서버 작업(`rag/reranking.py` prepare·package → `data/sageo/rerank_job/`, `rag/rerank_score.py`)으로 한 번에 캐시: mini(미니 비교 타깃 질의 Top-100 + 재작성본 passage), 학습·검증 Top-100, 검증 101~300위, test Top-100(타깃 층 선정용). | 2026-04-16 커밋(Sentence Transformers 연동)이 `chat_template.jinja`(역할 query/document)를 추가했고, 최신 transformers는 이것을 우선 쓴다. SAGEO 코드의 메시지는 이 템플릿에서 질의·문서가 빈칸으로 렌더링된다(0.6B로 확인). SAGEO(2026-02)는 그 이전 파일로 돌렸으므로 이전 revision이 SAGEO와 같은 환경이다(가중치 변경 없음). 입력 약 38.8만 쌍, 약 3.5억 토큰(쌍당 평균 약 870). |
| C35 | 측정 규칙 수정 (스키마 v0.9) | **#17은 필드마다 점수 min(핵심어 비율 / 0.75, 1)을 매기고(비율 0.75 이상 = 그 필드 만점) 네 필드 점수의 평균을 쓴다. 수준 경계 0.33 / 0.999라서 네 필드 모두 만점일 때만 1.0(처음 둔 0.99는 한 필드가 비율 0.72~0.75여도 1.0이라 학습 쌍 1,259개가 잘못 1.0이었다; 2026-11-12 수정).** **#14는 내용 타입이 보이는 필드를 2개 이상 채웠거나 전부 채웠으면 1.0(FAQPage: mainEntity).** **#13은 0 = 없거나 이 페이지 주제를 말하지 않음, 0.5 = 주제만(안내형), 1.0 = 주제에 대한 실제 사실(수치·주장).** | #17: 비율 평균은 how·do·i·my 같은 작은 단어 때문에 내용어가 네 필드에 다 있어도 1.0이 안 됐다(학습 질의 400개 Top-100에서 해당 486쌍 중 140쌍). 필드 수준 평균에 경계 0.75만 두면 "세 필드 만점 + 한 필드 절반"(0.875)이 1.0이 되어 그 필드가 보강되지 않는다(82쌍). 네 필드 조건이면 28쌍 / 0쌍. 불용어 목록을 늘리는 안(140 → 0 / 6쌍)은 단어 선택 근거와 어간 충돌(may→mai 등) 때문에 택하지 않았다. 필드 수준을 0 / 0.5 / 1로 끊어 평균하면 R head 질의 안 AUC가 0.881 → 0.813(무료 8축 + PCA32, 검증 540질의)으로 떨어져 필드 안 비율을 살린 연속값으로 둔다(0.883, pooled 0.689 → 0.695, 관련 문서만 0.653 → 0.672). #14: 질문·답이 채워진 FAQPage만 있는 문서 1,606개가 0.5에 갇혀 있었다. #13: '대상 이름'을 사실로 쳐서 안내형('Learn about …')이 0.5인지 1.0인지 애매했다. 수준마다 예/아니오 질문 하나를 더하는 구조로 판정 일치도를 높인다. 어느 쪽이 인용에 유리한지는 미리 정하지 않고 head가 데이터에서 배운다. (연구자 확정 2026-11-12) |
| C36 | LLM 모델 | **GE·Rewriter·판정 LLM·UMBRELA 모두 `gpt-5.4-mini-2026-03-17`**(configs/llm.yaml, 코드는 이 파일만 읽는다). 추론 강도: GE none(SAGEO의 minimal에 해당하는 가장 낮은 값), Rewriter·판정·UMBRELA medium(SAGEO가 지정하지 않아 gpt-5-mini 기본값 medium이었다). | gpt-5-mini(SAGEO 모델)는 2026-12-11 종료라 리뷰 중 추가 실험을 같은 모델로 못 한다. gpt-5.4-mini는 이름에 mini가 붙은 최신 모델(2026-03-17)이고, 종료 공지가 없으며 날짜 고정 버전이 있다. gpt-5-mini의 공식 대체인 gpt-5.6-terra는 날짜 없는 이름만 있고 배치 가격이 약 2.7배, gpt-5.6-luna는 nano 등급이다. SAGEO는 v1 제출(2026-02-12) 때 gpt-5.4-mini가 없었다. 두 번째 GE(SAGEO v2 §5.4는 Claude Sonnet 4.6)는 메인 결과 뒤에 정한다. 확인(2026-11-12): 날짜 고정 ID, flex 등급, 추론 강도 none·medium 호출 성공. (연구자 확정 2026-11-12) |
| C37 | 처방 방향, 가시성 마스크 | **축마다 처방 방향을 정한다(스키마 v0.10 `direction`). 올리기만 9개: #1·#3·#9·#14(내리려면 이미 있는 사실을 지워야 한다), #4·#11·#15·#16·#17(내리면 문서가 망가진다). 양방향 11개: #2·#5·#6·#7·#8·#10·#12·#13·#18·#19·#20, 단 #12는 0.75, #13은 0.5 아래로 내리지 않는다(`lower_floor`).** 내릴 때 Rewriter는 그 축의 `safety_down` 허용·금지를 본다. **가시성 마스크: 그 단계 입력에 아예 안 보이는 축은 그 단계 head 입력에서 뺀다(`hidden_from`; #15는 R·K, #19는 R).** head 학습은 방향과 무관하고 처방 단계에서만 거른다. 무작위 처방(§11.1)은 축 고르기만 무작위이고, k·허용 방향·목표 수준(head가 정함)은 GradGEO와 같다. | (1) 공통 규칙 C19(이미 있는 사실을 지우지 않는다)는 비교 방법과 같은 조건이다: GEO 프롬프트 "without adding or removing any core information", "do not delete any information", SAGEO 거의 모든 방법 "Preserve ALL information - do not truncate or summarize", AutoGEO-Mini 학습 보상의 원문 핵심 내용 재현율·모순(API판 프롬프트에는 없음). C19를 빼면 BM25 길이 보정 때문에 내용을 지우기만 해도 R 점수가 오르는 지름길이 생기고, 지울 수 없는 비교 방법과 불공정해지며, C20으로 보존 검사도 없다. 방향 규칙은 이 규칙과 '망가뜨리기 금지'에서 따라 나온다. (2) 미니 타깃 194개(v0.9 R head): GradGEO 처방 270개는 모두 올리기(#17 191, #11 79)였지만 기울기는 194개 모두에서 #15를 내리라고 가리켰다. SAGEO 코드에서 BM25 색인(`extract_headings_text`)과 재정렬기 입력(`QwenReranker.format_passage`)은 헤딩 태그를 지우고 GE 입력(`generator.format_passage`)만 `<h2>` 태그를 남기므로 R에서 #15의 효과는 0이고 이 신호는 상관이다. #19는 Lucene 분석기가 `*`를 버린다. R head에서 #15·#19를 빼도 검증 질의 안 AUC 0.8825 → 0.8820(pooled 0.695, 관련 문서만 0.672 그대로). (3) 무작위 처방: 기울기 방향으로 움직일 수 있는 축 중 head 점수가 오르지 않는 경우는 임계점 미만 735개 중 7개뿐이라 '유지'로 빠지지 않는다. head 이득 중앙값은 GradGEO가 고른 이동 0.67, 가능한 이동 전체 0.063. (4) #13은 C35에서 안내형·사실형 중 무엇이 유리한지 head가 배운다고 했으므로 1.0 → 0.5를 허용, #7은 GEO도 전문 용어 추가와 쉽게 쓰기를 각각 전략으로 둔다(같은 다이얼의 양쪽), #2는 올리기에서 이미 간접 → 직접 인용 변환을 허용한다. (5) 적용 결과: 처방 head = 마스크 6축 + PCA32(검증 질의 안 AUC 0.882, 마스크 없는 8축 0.8825). 미니 처방: GradGEO-R 270개는 이전과 같이 모두 올리기(#17 191, #11 79), 무작위 270개 중 내리기 76개(#8 56, #18 10, #20 10, 모두 양방향 축). (연구자 확정 2026-11-12) |

---

## 0. 다른 AI에게 주는 최상위 지시

너는 정보검색(IR), RAG, 대조학습, 딥러닝 최적화, GEO(Generative Engine Optimization) 논문 구현을 담당하는 시니어 ML 연구 엔지니어다.

아래는 연구자가 확정한 설계다. 핵심 설계를 임의로 바꾸지 말고, 확정 사항과 미정 사항(§17)을 구분해 재현 가능한 학습·추론·테스트 코드를 설계하라.

진행 순서:
1. 확정 사항, 미정 사항, 기술적 위험을 짧게 재정리한다.
2. 데이터 스키마, 모듈 구조, 손실·수식, 입출력 인터페이스를 설계한다.
3. 사전 점검(§10.2)과 판정 파일럿(§5.4)을 먼저 구현한다. 결과 표는 연구자에게 보여 주고 값을 확정받는다.
4. MVP를 구현한다. MVP에는 개입 점검(§10.3, 검증 split)을 포함한다.
5. 유닛 테스트, 학습 로그, 평가 지표, 재현용 설정을 제공한다.

규칙:
- 모델 체크포인트, GPU, API 키, 최종 축 목록을 확보한 것처럼 가정하지 않는다. 성능 숫자를 꾸며내지 않는다. config, 인터페이스, 명시적 TODO로 남긴다.
- "인용률 향상 보장", "SAGEO보다 우수함이 증명됨" 같은 표현을 쓰지 않는다.
- 논문·문서 초안에 "원문에 없는 내용 추가를 허용했다", "오프라인 샌드박스 환경이다" 같은 윤리·환경 해명 문장을 넣지 않는다(연구자 지시).
- 비용이 드는 실험 칸 중 §11.3의 보류 칸은 연구자에게 다시 묻기 전에는 실행하지 않는다.
- 비밀값(`.env.local`의 API 키, 프록시 주소)은 출력하거나 로그에 남기지 않는다.

---

## 1. 연구 목적과 핵심 가설

목표:
- 다단계 RAG(검색 → 재정렬 → 생성)에서 특정 문서의 최종 인용 가능성을 높일 재작성 전략과 그 강도를 타깃마다, 그 문서의 대표 passage + 메타 단위로 추정한다.
- 원본 문서들의 단계별 통과/탈락 관측으로 각 단계가 선호하는 passage 특성을 학습한다.
- 전략 특징을 해석 가능한 벡터 z ∈ [0,1]^K로 표현한다(후보 20개 → 정제 후 K ≤ 12).
- 단계별 head 3개로 조건부 통과 확률을 학습하고, 그 곱으로 최종 인용 확률을 근사한다.
- ∇_z log p_final로 문서(의 대표 passage)마다 목표 전략 z*를 계산한다.
- 쓰지 않는 것: 54D concat/fusion MLP, RL policy, 최적화 중 GE 반복 평가 루프.

핵심 가설(검증 대상이며 확립된 사실이 아님):
> 원본 passage들의 단계별 선택/인용 패턴으로 학습한 미분 가능한 선호 모델의 gradient가, 새로운 문서에 적용할 유효한 재작성 전략을 안내할 수 있다.

---

## 2. 아키텍처

```
[환경] SAGEO Arena 파이프라인 (환경 B: 마크다운 본문)                              【C21】
   BM25(passage 256토큰·문장 단위 겹침 ≤64, passage마다 본문 + 메타 4열, 열별 BM25 점수 합) Top-100   【C29】
   → Qwen3-Reranker-4B → Top-10 passage → GE(gpt-5.4-mini, C36)
      ↓
Stage A — 전략 특징 z (측정기)                                                     【C16】
   단위: (질의, 메타 + passage 1개) — 엔진과 같은 단위. 판정 LLM은 질의 없이 메타 + passage만 본다
   문서 종속 축 ← 판정 LLM(문장 번호 → 코드가 레벨) 레이블로 학습한 인코더(Qwen3 + LoRA), 이후 freeze
   질의 종속 [Q] 축 ← 임베딩·규칙으로 즉석 계산
      ↓
Stage B — 단계별 Head 3개 (독립 가중치)                                            【C23, C24】
   입력 [z | PCA(질의 임베딩)] → logit_R, logit_K, logit_G → p_R, p_K, p_G
   손실: 순위가중 all-pairs 쌍비교 + λ·BCE, 조건부 모집단에서 학습
      ↓
Stage C — log p_final = log p_R + log p_K + log p_G  (검증셋 temperature scaling 후)
      ↓
Stage D — 처방: 타깃 문서의 대표 passage p*에서                                     【C18】
   g = ∇_z log p_final(z(q,p*)); 고칠 축 = abs(g_i)/‖g‖ ≥ τ인 축(최대 k_max), 목표 수준 = g 방향 수준 중 log p_final 최대(§8.2, C32)
   타깃(대표 passage + 메타)별 자동 k(k = 0 허용)
      ↓
Stage E — Rewriter(gpt-5.4-mini, C36): p*만(+선택된 문서 단위 필드) 재작성 → 본문에 끼워 넣기   【C18, C19】
   → 환경 B 청커로 재청킹 → 타깃 문서만 교체해 재색인
      ↓
Stage F — 평가: 같은 질의로 전체 파이프라인 재실행 (GE 1회【C33】), 길이 비율·편집량 기록   【C20】
```

| 구분 | 내용 |
|---|---|
| Training | 원본 문서의 파이프라인 로그만 쓴다. |
| Validation | 원본 홀드아웃에서 확률·순위·보정을 평가한다. |
| 개입 점검(검증 split) | 소규모 재작성본에 대해 **검색·재정렬만** 실제로 다시 돌린다(로컬, API 비용 없음). gradient 방향의 타당성 확인. τ, k_max 등 처방 설정은 여기서 정한다. |
| Final blind test | 처음 보는 질의에서 처방 → 재작성 → 전체 파이프라인 재실행. 한 번만 연다. |

---

## 3. 환경 (SAGEO Arena, 환경 B)

### 3.1 코드로 확인한 사실 (`third_party/SAGEO_Arena`)

| 항목 | 내용 | 위치 |
|---|---|---|
| 본문 추출 | SAGEO: `trafilatura.extract(html)` 뒤 줄바꿈을 공백으로 바꾼다 → 문단 정보 없음. **환경 B**: 저장한 원본 HTML에서 `trafilatura.extract(html, output_format="markdown", include_formatting=True, include_tables=True, include_links=False)`, 줄바꿈 유지. 추출 실패·빈 결과·시간 초과·평문 대비 단어 수 절반 미만이면 그 문서는 평문 본문을 쓴다(`body_format`에 사유 기록) | `src/crawling/crawler.py` (extract_body_text), `crawl/extract_markdown.py` |
| 청킹 | SAGEO: 문장 분리 정규식 `(?<=[.!?])\s+`, 256토큰(cl100k), 직전 chunk 끝 문장들을 64토큰 이내로 겹침. 256토큰을 넘는 한 문장은 토큰 창(보폭 192)으로 자름. 문장은 공백 하나로 이어 붙인다. **환경 B**: 같은 규칙에 줄바꿈도 단위 경계로 쓰고(소제목·목록·표 줄은 마침표 없이 끝남), 다른 줄의 단위는 원래 줄바꿈(\n 또는 \n\n)으로 잇는다. 평문 3,000문서에서 SAGEO와 결과 동일 확인 | `src/modules/retriever_bm25.py` chunk_text, `rag/chunking.py` |
| 검색 | SAGEO: 필드(title, meta_description, headings, body_text, jsonld_text)별 BM25 Top-200(`per_field_k`; 본문은 passage, 나머지 4개는 문서 단위) → 가중 RRF(k=60, 가중치 모두 1.0). body chunk 점수 = 본문 기여 + 부모 문서의 다른 필드 기여. 다른 필드에만 걸린 문서는 그 문서의 모든 chunk가 같은 점수로 추가됨. **GradGEO【C29】**: passage마다 열 5개(본문 passage, title, meta_description, headings, jsonld_text)인 한 줄로 색인(`data/sageo/index_b_unit`, 4,945,253줄, Lucene 단일 스레드), 열별 BM25(k1 0.9, b 0.4) 점수 합, 가중치 1.0, Top-N을 바로 뽑음 | `retriever_bm25.py` search, weighted_rrf_fusion; `rag/build_unit_index.py`, `rag/retrieval.py` |
| 컷 | 검색 Top-100 chunk(`RETRIEVAL_K = 100`), 생성 입력 Top-10 chunk(`GENERATION_K = 10`) | `src/scripts/main.py:182-183` |
| 재정렬기·GE 입력 | 제목 + 메타 설명 + 필터된 JSON-LD + 헤딩(헤딩당 500자, 합계 3,500자, `<h1>`~`<h6>` 태그 포함) + 본문 chunk 1개 | `src/modules/reranker.py` format_passage, `src/modules/generator.py` format_passage |
| 문서 순위 | 단계 결과에서 그 문서 chunk 중 가장 높은 순위 (MaxP) | `src/evaluation/metrics.py` find_doc_rank |
| 인용 | GE 인용 번호는 context passage 번호 → passage 단위 인용 레이블 가능 | `src/evaluation/evaluate.py` _get_citation_rank |
| 재작성 범위 | `--scope body / structured / both`, 모두 문서 전체. passage용 프롬프트(`prompt_template_passage`)는 코드에 있으나 실행 스크립트에서 고를 수 없어 실험에 쓰이지 않음 | `main.py:187-191`, `src/modules/utils/sageo_methods.py` |
| 재작성 프롬프트 | GEO 계열은 GEO 원래 문구(Citations "You may invent sources…", Statistics "even if hypothetical"). StageAware는 인용·통계·출처 전략을 빼고 "Do NOT invent facts" | `sageo_methods.py` (StageAware 1443행~) |

### 3.2 코퍼스와 질의
- 질의 2,700개 = 9개 데이터셋 × 300 (`data/sageo/queries/*_queries_300.jsonl`).
- 코퍼스 재크롤링: `data/sageo/corpus/documents_filtered.jsonl` = (질의, URL) 172,632쌍(SAGEO 171,003보다 1,629 많음), 고유 URL 156,015개. 원본 HTML은 `data/sageo/crawl/raw_html/{httpx,browser}/k[:2]/k.html.gz`(k = sha1(url), script/style 제거), 고유 URL 전부(156,015개) 있음.
- **색인 단위는 URL**【C28】: 같은 URL의 (질의, URL) 쌍들은 문서 하나로 합쳐 한 번만 색인하고, 나온 질의 목록(`source_query_ids`)을 남긴다(고유 URL 156,015개).
- **환경 B 코퍼스**: `data/sageo/corpus/documents_filtered_md.jsonl`(같은 172,632쌍, body_text만 마크다운, `body_format` 필드 추가), 집계는 `documents_filtered_md.stats.json`. 표본 300개: 추출 실패 0.7%, 단어 수 평문 대비 중앙값 1.003배.
- 문서 필드: title, meta_description, jsonld, headings(태그 포함 문자열 목록), body_text.
- 같은 문서 1,758개에서 A·B 비교(2026-11-06): passage 수 B/A 0.99배, 토큰 1.04배(문서당 passage 중앙값 8, 평균 36). 이전 표본 2,000문서 기준: 문서당 passage 중앙값 6개(평균 16.5, p90 27), passage당 문장 중앙값 8개(p10 3, p90 16). 헤딩 있는 문서 98%, 문서당 헤딩 중앙값 15개(푸터 내비게이션 헤딩도 섞임).
- 길이: 코퍼스 p99 = 241,659자. 초과 문서는 타깃·판정 파일럿 표본에서 뺀다(경쟁 문서로는 코퍼스에 남긴다).

### 3.3 하드웨어
- 로컬(RTX 3060 Ti 8GB)에서는 Qwen3-Reranker-4B fp16을 못 돌린다. GPU 서버(jpu-192 권장) 사용. vLLM `max_model_len = 10000`.

---

## 4. 데이터와 레이블 【C15】

### 4.1 단위
- 학습 예시 = (질의 q, 메타 + passage p). 메타는 그 문서의 제목·메타 설명·JSON-LD·헤딩(엔진에 보이는 형태)이고 같은 문서의 모든 passage에 똑같이 붙는다. passage는 환경 B 청커 결과 그대로.
- 후보 풀(q) = 질의 q의 BM25 순위 Top-N passage. **N = 300**(사전 점검 §10.2로 결정)【C30】. 검색 통과 기준은 SAGEO와 같은 Top-100.
- 문서 = URL(색인은 URL당 한 번, C28). 평가 단위는 문서다. 문서의 단계별 순위 = 그 단계에서 그 문서 passage 중 가장 높은 순위(SAGEO와 동일).

### 4.2 레이블과 조건부 모집단

| 레이블 | 양성 조건 | 모집단 |
|---|---|---|
| y_R(q,p) | p의 BM25 순위 ≤ 100 | 후보 풀(Top-N passage) |
| y_K(q,p) | p가 재정렬 Top-10 | Top-100 passage |
| y_G(q,p) | GE 1회 실행에서 p를 인용했으면 1, 아니면 0【C33】 | Top-10 passage |

- 모집단 밖은 null, loss mask = 0. 0으로 채우지 않는다.
- GE는 질의마다 Top-10 입력을 1회 실행하고, 입력 순서를 질의마다 무작위로 섞는다(위치 편향을 z와 무관한 잡음으로 만든다). 잡음은 반복 대신 질의 수로 평균한다【C33】.
- 재정렬기 점수는 (질의, passage)마다 독립이므로 **Top-N 전체**의 원본 점수를 캐시한다. 학습에는 Top-100만 쓰고, 101~N위 점수는 검증 split에서 K head의 선택 편향 점검(101~N위 예측 정확도)에만 쓴다.
- 한 passage(같은 텍스트)가 여러 질의의 후보 풀에 들어가면 z의 문서 종속 축은 한 번만 판정해 재사용한다. [Q] 축과 레이블은 질의마다 따로다.

### 4.3 split
- 데이터셋별 같은 비율로 학습 60 / 검증 20 / 테스트 20 (질의 1,620 / 540 / 540)【C33】. 검증 중 270개(`val_mini`, 이전 dev split)는 미니 비교가 쓰는 부분집합이다. 학습·테스트 질의는 C33 이전과 같다.
- 질의 임베딩 유사도 ≥ 0.9인 쌍은 같은 split에 묶는다. 그 밖의 질의 그룹핑은 쓰지 않는다.

### 4.4 JSONL 인터페이스 (예시 값)
```json
{
  "query_id": "q001", "query": "...", "dataset": "msmarco", "split": "train",
  "doc_id": "d001", "passage_id": "d001#3", "passage_index": 3,
  "meta": {"title": "...", "meta_description": "...", "jsonld_view": "...", "headings_view": ["<h1>...</h1>"]},
  "passage": "...", "sentences": ["...", "..."],
  "bm25_rank": 37, "y_R": 1,
  "rerank_rank": 4, "rerank_score": 0.81, "y_K": 1,
  "ge_runs": 1, "cite_count": 1, "y_G": 1,
  "z_labels": {"statistics": 0.5, "fluency": 1.0, "title_informativeness": 0.75, "...": null},
  "doc_relevance_umbrela": 2
}
```
Top-100 밖 passage는 rerank_*·y_K·ge_*·y_G가 null이다. 문서 단위 평가 기록은 별도 파일로 둔다.

---

## 5. Stage A — 전략 특징 z 【C16, C17】

### 5.1 축 (정의 전문은 `configs/strategy_schema.yaml` v0.5, z 인덱스 순서는 스키마 순서)

| # | id | 이름 | 출처 | 단위 | 측정 | 레벨 |
|---|---|---|---|---|---|---|
| 1 | statistics | 수치 근거 | GEO | passage | 문장 번호 | 개수 5단계 |
| 2 | quotation | 인용구 | GEO | passage | 문장 번호 | 개수 5단계 |
| 3 | cite_sources | 출처 | GEO | passage | 문장 번호 | 개수 5단계 |
| 4 | fluency | 유창성 | GEO | passage | 문장 번호 | 반대 개수 5단계 |
| 5 | easy_language | 쉬운 표현 | GEO | passage | 문장 번호 | 반대 개수 5단계 |
| 6 | authoritative_tone | 권위 어조 | GEO | passage | 문장 번호 | 반대 개수 5단계 |
| 7 | technical_terms | 전문 용어 | GEO | passage | 문장 번호 | 개수 5단계 |
| 8 | unique_words | 어휘 다양성 | GEO | passage | 규칙(MTLD) | 연속 |
| 9 | concrete_claims | 구체적 주장 | SAGEO-SA | passage | 문장 번호 | 개수 5단계 |
| 10 | topic_term_consistency | 주제어 일관성 | SAGEO-SA | passage | 문장 번호 | 개수 5단계 |
| 11 | body_query_alignment [Q] | 본문 질의 정합성 | 새로 만듦 | passage | 임베딩+규칙 | 연속 |
| 12 | title_informativeness | 제목 정보성 | SAGEO-struct | 문서 | LLM 레벨 | 5단계 |
| 13 | meta_description | 메타 설명 | SAGEO-struct | 문서 | LLM 레벨 | 3단계 |
| 14 | jsonld | JSON-LD | SAGEO-struct | 문서 | 규칙 | 3단계 |
| 15 | heading_hierarchy | 헤딩 계층 | SFE | 문서 | 규칙 | 3단계 |
| 16 | chunk_independence | 조각 독립성 | SFE | passage | 문장 번호 | 반대 개수 5단계 |
| 17 | structural_query_alignment [Q] | 구조 필드 질의 정합성 | SAGEO-SA | 문서 | 규칙 | 연속 |
| 18 | list_table | 목록·표 | SFE | passage | 규칙(목록 항목·표 줄 수) | 5단계 |
| 19 | emphasis | 강조 | SFE | passage | 규칙(굵게·기울임 구간 수) | 개수 5단계 |
| 20 | paragraph_length | 문단 길이 | SFE | passage | 규칙(가장 긴 문단의 문장 수) | 3단계 |

- 출처별: GEO 8, SAGEO 6, SFE 5, 새로 만듦 1.
- 18~20번은 `prep/rule_axes.py`로 계산한다. 레벨 경계는 B 코퍼스 표본(passage 116,510개) 분포로 확정했다(2026-11-06): 목록·표 0개 72.1%, 강조 0개 62.8%, 가장 긴 문단 3문장 이하 52.2%·4~7문장 39.7%·8문장 이상 8.1%.
- "문서" 단위 축은 그 문서의 모든 passage가 같은 값을 쓴다.
- z_i는 "원본에 이미 있는 그 특징의 정도"다. 앞으로 가할 변경량이 아니다.

### 5.2 판정 LLM
- 모델 gpt-5.4-mini(C36), 3회 판정의 중앙값(파일럿에서 3회 ≈ 5회이면 3으로 확정).
- 호출 하나 = 문서 하나: 엔진에 보이는 형태의 메타를 한 번 + 판정할 passage 목록 `[P1]…[Pk]`(문장마다 `S1, S2…` 번호). passage가 50개를 넘으면 50개씩 나눠 호출하고 메타를 매번 넣는다. passage끼리 겹친 문장은 엔진이 보는 대로 두 passage에 모두 나온다.
- 지시: "각 passage를 메타와 함께 혼자 보여진 것처럼 판정하라. 다른 passage 정보를 쓰지 마라."
- 판정 LLM에는 질의를 주지 않는다. 판정하는 축은 모두 질의와 무관한 축이고, 질의 종속 [Q] 축은 코드가 (질의, 메타 + passage)로 계산한다. 그래서 같은 passage는 여러 질의에서 판정 한 번으로 재사용된다.
- 출력: passage마다 축별 문장 번호 목록, 문서 단위로 제목·메타 설명 레벨(한 줄 근거 포함).
- 판정 대상 passage: 학습 예시가 되는 passage(어떤 질의의 후보 풀에 든 것). 몇 개를 판정하고 나머지를 인코더로 계산할지는 사전 점검의 고유 passage 수와 비용표를 보고 정한다.
- 영어 판정 프롬프트는 파일럿 직전에 고정한다(같은 뜻이라도 표현이 바뀌면 일치도가 크게 흔들린다).

### 5.3 레벨 계산 (코드) 【C26】
- 개수 5단계: 해당 문장 수 0/1/2/3/4개 이상 → 0/0.25/0.5/0.75/1.0.
- 반대 개수 5단계(문제 문장을 세는 축): 0/1/2/3/4개 이상 → 1.0/0.75/0.5/0.25/0.
- 신뢰도 미달 시 있다/없다로 다시 계산(재판정 없음). 제목은 5단계 → 3단계로 합친다.
- 연속 축은 학습 split 분위수로 [0,1] 변환해 고정한다. 단 #17은 분위수 변환 없이 필드 점수 min(비율 / 0.75, 1)의 평균을 쓴다(C31, C35).
- 규칙 축(JSON-LD, 헤딩 계층)은 SAGEO가 엔진에 실제로 보여 주는 형태(필터·길이 제한 적용 후)로 계산한다.

### 5.4 파일럿과 정제
- 파일럿: 학습 split, 데이터셋 균등, 질의당 문서 1개, URL 중복 제거, seed 42, 500문서 × 5회. 판정 대상 = 그 문서 passage 중 그 질의의 후보 풀에 든 것.
- 정제(결과를 보기 전에 절차·숫자 고정, 스키마 `refinement`):
  1. 보임·편집 가능 (스키마 작성 때 적용)
  2. 신뢰도: 3회 중앙값 기준 Spearman ρ ≥ 0.8, 조작 시험 적중 ≥ 0.8. 미달이면 있다/없다(또는 3단계)로 재계산 후 재검사, 그래도 미달이면 제거
  3. 변동: 가장 많은 레벨이 아닌 예시가 학습셋에 1,000개 이상 【C27】
  4. 중복: 축 간 |Spearman| ≥ 0.7이면 병합, 신뢰도 높은 축을 남김
  5. 쓸모: 검증 split permutation importance(10회 평균 하락)가 세 head 모두에서 기준(R·K AUC 0.01, G 순위상관 0.01) 미만이면 제거
  - 최대 12개. 넘으면 ⑤ 중요도 낮은 순으로 자른다.

### 5.5 인코더
- Qwen3 + LoRA → Linear → Sigmoid. 입력 = 메타 + passage. 문서 종속 축만 예측한다.
- 학습은 판정 레이블에 대한 L_feat(masked MSE)만, 이후 freeze. stage 손실을 인코더까지 역전파하는 joint 학습은 ablation이며, 그때 z-판정 일치도를 함께 보고한다.
- MVP에서는 인코더 없이 판정 점수를 z로 바로 써도 된다.

---

## 6. Stage B — 단계별 Head 3개 【C23, C24】

```
x_s     = [z(q,p) | PCA(질의 임베딩)]     PCA 0·16·32차원 중 검증으로 선택(0 = z만), 학습 질의로만 적합
logit_s = Head_s(x_s),  s ∈ {R, K, G}     가중치는 head마다 별도, 작은 MLP(선형 head는 ablation)
p_s     = sigmoid(logit_s)
```

| Head | 학습 집합 | 순위 기준 | 통과 경계 |
|---|---|---|---|
| R | 후보 풀 Top-N passage | BM25 순위 1~N | 100 |
| K | Top-100 passage | 재정렬 순위 1~100 | 10 |
| G | Top-10 passage | 인용 비율(soft) | — |

손실(질의마다):
```
L_pair_s = Σ_{i,j: r_i < r_j} w_ij · softplus(−(logit_s(i) − logit_s(j))),   w_ij = |log r_i − log r_j|
           질의 안 전체 쌍(N×N)을 계산하고 샘플링하지 않는다
L_s      = L_pair_s + λ_s · BCE(p_s, y_s)      BCE에는 가중치를 두지 않는다(보정 유지)
```
- G의 soft label 쌍비교 세부(가중치를 인용 비율 차이로 둘지 등)는 미정(§17).
- 한 질의에 양성만 또는 음성만 있으면 쌍비교는 건너뛰고 BCE만 쓴다.
- 질의 원-핫(데이터셋 라벨), 맥락 공변량 c(경쟁 강도, 관련성, 도메인)는 ablation에서만 넣는다. 원본 BM25·재정렬 점수는 z 효과의 결과라서 입력에 넣지 않는다.
- 【C37】 가시성 마스크: 그 단계 입력에 아예 안 보이는 축은 그 단계 head의 z에서 뺀다(스키마 `hidden_from`). #15 헤딩 계층은 R·K(BM25 색인과 재정렬기 입력이 헤딩 태그를 지운다), #19 강조는 R(BM25 분석기가 `*`를 버린다). 그 단계의 gradient는 그 축에서 0이 된다.

---

## 7. Stage C — 조건부 확률 결합

```
p_R ≈ P(R | q, p),  p_K ≈ P(K | R, q, p),  p_G ≈ P(G | R, K, q, p)
log p_final = log p_R + log p_K + log p_G      (각 p는 epsilon으로 clamp)
```
- 연쇄법칙이 성립하려면 각 head가 정의된 조건부 모집단에서 학습되고, 경계가 실제 파이프라인과 같아야 한다.
- 검증셋에서 단계별 reliability diagram, Brier, ECE를 재고, temperature scaling을 거친 확률만 최적화에 쓴다. 보정이 틀리면 단계 간 gradient 가중치 (1 − p_s)가 틀어진다.
- 경쟁 passage 집합에 의존하는 근사 surrogate다.

---

## 8. Stage D — 처방 【C18】

### 8.1 대표 passage p*
타깃 문서 d의 대표 passage는 **타깃을 고를 때 쓴 원본 실행 결과**로 정한다(같은 로그라 추가 정보가 아니다).
- 검색 탈락 문서(가장 높은 passage의 BM25 순위 > 100): BM25 순위가 가장 높은 passage
- 검색 통과 문서: 재정렬 순위가 가장 높은 passage
- 동점이면 본문 앞쪽 passage

### 8.2 gradient와 목표
```
g        = ∇_z log p_final(z(q,p*))   — 질의 임베딩과 모든 파라미터는 고정, z만 leaf tensor
         = Σ_s (1 − p_s) · ∇_z logit_s
L_i      = 축 i의 현재 수준(Rewriter에 ▶로 보이는 수준; continuous 축은 3구간, 경계는 스키마 bin_edges, 없으면 0.25 / 0.75)
share_i  = m_i · g_i / ‖g‖,   m_i = 0: 편집 불가, L_i가 이미 g_i 방향의 끝 수준, 또는 그 방향이 허용되지 않음
           (direction: up 축의 내리기, lower_floor 아래로 내리기; C37)
후보 축  = |share_i| ≥ τ 인 축, |share_i| 큰 순서, 최대 k_max
목표 L*_i = g_i 방향의 허용된 수준들(칸 수 제한 없음) 중 log p_final = Σ_s log p_s 를 가장 크게 하는 수준
            (앞서 고른 축의 목표를 넣은 z에서 계산; 어느 수준도 현재보다 높지 않으면 그 축은 뺀다)
```
- 【C32】 고칠 축은 g가, 몇 칸 갈지는 head 예측이 정한다. 출발점은 실제 z 값이 아니라 현재 수준이다. Rewriter는 수준만 보므로, 같은 수준 안에서의 위치(예: 비율 0.36과 0.70)가 처방을 가르지 않게 한다.
- (1 − p_s) 덕분에 이미 통과할 가능성이 높은 단계의 신호는 줄고 병목 단계의 신호가 커진다. 목표 수준도 단계별 확률의 곱(p_final)으로 고르므로, 한 단계가 더 원해도 다른 단계에서 깎이는 만큼이 크면 덜 간다.
- share의 분모는 모든 축의 ‖g‖다. 끝에 닿은 축을 뺀 ‖m ⊙ g‖로 나누면 큰 축이 모두 끝에 닿은 타깃에서 작은 g가 부풀려져 의미 없는 처방이 나온다(미니 비교에서 확인).
- k = 0(고치지 않음)도 허용. 비교용 고정 k = 1, 3, 5.
- 【C37】 처방 방향은 스키마 `direction`이 정한다. 올리기만 축은 g_i < 0이면 처방하지 않고(올리지도 않는다) 자리를 다른 축에 준다. 내리기 처방에서 Rewriter는 `safety_down`을 본다.
- τ, k_max는 검증 split(개입 점검)에서만 정하고 고정·기록한다(미니 비교 임시값 τ 0.25, k_max 5; τ 0.2~0.3에서 처방이 거의 같았다).
- 점검: sigmoid 포화, off-manifold z*(학습 분포 kNN 거리), 단계 간 gradient 상충(∇log p_R·∇log p_G < 0 비율).
- 상충 진단 대상 축 쌍: technical_terms ↔ easy_language, unique_words ↔ topic_term_consistency.
- z*는 surrogate 기준의 국소 최적일 뿐이다. 실제 GE에서의 최적이라고 주장하지 않는다.

---

## 9. Stage E — Rewriter 【C18, C19, C20】

### 9.1 입력과 출력
```
patch = Rewrite(query=q, meta=META(d), passage=p*, targets=z*[S], schema=strategy_schema)
```
- S = §8.2 자동 k로 고른 축 집합. 축마다 루브릭 전체를 보여 주고 현재 레벨 ▶, 목표 레벨 ★를 표시하고 그 축의 허용·금지 목록을 붙인다(목표가 현재보다 낮으면 `safety_down`, C37). "한 단계 올려라" 같은 상대 지시는 쓰지 않는다. 목표는 여러 칸 이동일 수 있다.
- delta(z* − z)를 숫자로 넘기지 않는다.
- 모델 gpt-5.4-mini(C36). 도메인 정보는 주지 않는다.
- 출력은 바꾼 필드만: `{passage, title?, meta_description?, headings?, jsonld?}`. 문서 단위 필드는 S에 그 축이 있을 때만 바꾼다.
- 공통 규칙(스키마 `rewriter.global_safety`): 이미 있는 사실을 바꾸거나 반대로 만들거나 지우지 않는다(크롤링 잔재는 지워도 됨), 얼버무린 주장의 단정화는 허용, 지시된 축만 바꾼다, 키워드 스터핑 금지, 원문 언어 유지, 원래와 비슷한 길이(숫자 기준 없는 권고)【C20】.
- Rewriter는 타깃마다 한 번만 부른다. API 오류·시간 초과로 다시 부르는 것만 허용한다. 결과를 보고 다시 쓰게 하는 반복은 어느 방법에도 넣지 않는다(SAGEO 방법들도 한 번만 재작성한다).

### 9.2 끼워 넣기와 재색인
1. 본문을 환경 B 청커 단위(문장, 또는 줄)로 나누고, p*에 해당하는 단위 구간을 찾는다(`rag/chunking.py` chunk_units가 chunk마다 단위 번호를 돌려준다).
2. 그 구간을 재작성 passage로 바꾼다. p*와 이웃 passage가 겹친 문장(최대 64토큰)도 p*에 속하므로 함께 바뀐다.
3. p*가 한 문장이 256토큰을 넘어 토큰 창으로 잘린 chunk이면 토큰 구간을 바꾼다(드문 경우, 개수를 기록).
4. 바뀐 문서 d'를 환경 B 청커로 다시 청킹하고, 원본 d를 빼고 d'만 넣어 재색인한다. 원본과 수정본을 동시에 넣지 않는다. passage 단위 색인(C29)에서는 d의 줄을 모두 빼고 d'의 줄(메타 열 포함)을 넣는다. 메타를 고치면 그 문서 모든 줄의 메타 열이 함께 바뀐다.
5. 재작성 passage 토큰 수 / 원래 토큰 수 비율을 모두 기록한다. passage가 길어지면 재청킹 때 더한 내용 일부가 다음 passage로 넘어갈 수 있는데, 엔진이 실제로 보는 모습이므로 그대로 둔다(실현율은 §9.3대로 재청킹 후 passage로 잰다). 재작성 실패·잘림은 숨기지 않고 보고한다(SAGEO는 실패 문서를 조용히 DROPPED 처리했다).

### 9.3 실현율
- d'를 재청킹한 뒤, 재작성 텍스트와 가장 많이 겹치는 passage를 다시 판정해 z(p')와 z*를 비교한다.
- 이동 폭별(1칸 / 2칸 이상) 정확 도착률과 방향 적중률을 보고한다. 검증 split에서 실제 최적화 목표로 측정한다.
- 정확히 도착하지 못한 처방은 넘침(목표보다 위)과 모자람(목표보다 아래)으로 나눠 센다. 스키마 v0.8의 "목표 수준을 넘지 말라" 규칙이 듣는지 여기서 본다. 넘침이 많으면 규칙을 강화한다(예: 재작성 후 Rewriter가 스스로 수준을 점검). 첫 확인은 GE·Rewriter 모델을 정한 뒤 미니 비교 재작성에서 한다.
- 정확 도착률이 낮으면 판정은 5단계로 두고 Rewriter 지시만 3구간 루브릭으로 바꾼다.

### 9.4 재작성 기록 (보고만, 거르지 않음) 【C20】
- 의미 보존 점수(KPR/KPC 등)는 재지 않는다. 이미 있는 사실을 지키는 것은 §9.1 공통 규칙(Rewriter 지시)으로만 둔다.
- 방법마다 길이 비율(재작성 후/전 cl100k 토큰 수)과 편집량(편집 거리, 바뀐 토큰 비율)을 코드로 기록해 보고한다. 이 값으로 결과를 거르거나 원본으로 되돌리지 않는다. GradGEO는 passage 하나만 고치므로 문서 전체를 고치는 방법보다 편집량이 적다.
- 같은 Rewriter를 쓰는 비교 칸(무작위 처방, 전부 올리기, 평균 g)의 길이 비율을 GradGEO와 나란히 보고한다. "글을 늘려서 이긴 것"인지 여기서 확인된다.
- 재작성본은 모두 저장한다.

---

## 10. 평가

### 10.1 학습·검증
원본 로그만 쓴다. 지표: AUC/AP, BCE, Brier/ECE, 단계별 순위 품질(nDCG/Recall), z-판정 일치도.

### 10.2 사전 점검 (BM25 직후, 본 실험 코딩 전)
결과 표를 연구자에게 보여 주고 값을 확정받는다.
1. **N = 200 vs 300**: 순위 구간(1~100 / 101~200 / 201~300, 여유 있으면 301~500)별 관련 문서 비율. gpt-5-mini UMBRELA(0~3점), 2점 이상 = 관련. 201~300위에 관련 문서가 꽤 있으면 300, 거의 없으면 200. N = 300이면 재정렬 캐시가 1.5배. passage 단위 엔진(C29)은 Top-N을 바로 뽑으므로 필드별 검색 깊이 설정이 없다(SAGEO 방식에서 쓰던 `per_field_k = N`은 없어졌다). 학습 레이블과 평가가 같은 엔진을 쓰고, 검색 통과 기준은 계속 Top-100이다.
2. 질의별 Top-100 / Top-N 안의 고유 문서 수.
3. 그중 그 질의의 Google 결과 문서 비율.
4. 관련 문서 중 101~N위 출신이 질의당 충분한지("검색 탈락 문서 구제" 주장용).
5. 구간별 동점 passage 비율과 100위 선을 가로지르는 동점 묶음(질의 비율, 묶음 크기). SAGEO 방식에서 이 묶음이 질의 58%에 있어 엔진을 바꿨다(C29). 남는 동점은 참고용이고 따로 처리하지 않는다(§13 G).
6. 후보 풀 합집합의 고유 passage 수 → 판정 비용표(판정할 passage 수 vs 인코더로 계산할 수).

### 10.3 개입 점검 (검증 split)【C33】
- 검증 질의에서 축 하나씩 레벨을 올리거나 내려 대표 passage를 재작성한다.
- 검색·재정렬만 다시 돌려 실제 Δ순위를 잰다(로컬, API 비용 없음).
- 지표: 예측 Δlog p_R, Δlog p_K와 실제 Δ순위의 부호 일치율, 순위 상관.
- 일치율이 우연 수준(약 50%)이면 핵심 가설을 재검토한 뒤에야 GE 비용이 드는 실험으로 넘어간다.
- 여기서 τ, k_max 등 처방 설정을 grid로 정하고 Rewriter 실현율(§9.3)을 잰다. 고정 k sweep도 여기서(검색·재정렬만) + 소규모 GE 파일럿. 최종 테스트는 아직 열지 않는다.

### 10.4 Final blind test (한 번만) 【C22】
1. 타깃 선정: 테스트 질의마다 층 3개에서 1개씩 무작위.
   - 검색 층: 가장 높은 passage가 BM25 Top-N 안인 문서 중
   - 재정렬 층: Top-100 안인 문서 중
   - 생성 층: Top-10(GE 입력) 안인 문서 중 (= SAGEO 프로토콜, 인용 여부는 보지 않음)
   - 뽑은 문서가 UMBRELA 2점 미만이면 다시 뽑는다(무관 문서도 경쟁 문서로는 남는다). 같은 질의에서 층끼리 같은 문서가 뽑히면 다시 뽑는다. 길이 p99 초과 문서는 타깃에서 뺀다.
2. 원본 기준선: 같은 질의로 GE 1회(이 값을 기준으로 쓴다).
3. 방법마다 한 번 재작성 → d만 d'로 바꿔 재색인 → 같은 질의, 같은 검색기·재정렬기·GE·seed·프롬프트로 전체 파이프라인 재실행(GE 1회, 순서 섞음). 재정렬은 캐시를 쓰고 d'의 passage만 다시 채점한다. GE는 d'의 passage가 Top-10에 들 때만 부른다.
4. 지표(층마다): 검색 통과 H@100, 재정렬 통과 H@10, 인용률, Δ순위. SAGEO 비교용으로 Retrieval H@20도 함께 보고한다.
5. 출발 구간별 분석: 검색 층 안의 101~N위 출신만 따로(순위가 결정적이라 편향 없음).
6. 품질: 편집량, 길이 비율, 실현율, 호출 수, 비용, 지연시간.
7. 통계: 질의 단위 cluster bootstrap 신뢰구간, 같은 타깃 쌍비교.

---

## 11. 비교 방법 【C25】

모든 방법은 같은 코퍼스, 같은 타깃, 같은 Rewriter 모델(gpt-5.4-mini, C36), 같은 파이프라인으로 우리 환경에서 다시 돌린다. 코퍼스를 다시 크롤링했으므로 SAGEO 논문 수치와 직접 비교하지 않는다.

### 11.1 같은 축, 고르는 방식만 다른 비교 (GradGEO와 같은 범위: 대표 passage + 선택된 필드, 같은 프롬프트 골격)
이 비교들은 전략 개수를 해명하려는 것이 아니라 "gradient로 타깃마다(대표 passage + 메타 기준) 고른 것이 효과"라는 핵심 주장의 증거다.

| 비교 | 정의 |
|---|---|
| 무작위 처방 | 축을 고르는 방식만 GradGEO와 다르다【C37】. 그 문서에서 GradGEO가 고른 개수 k만큼, 허용된 g 방향으로 움직이면 head 예측 log p_final이 오르는 축 중에서 무작위 순서로 고른다(τ 없음). 방향은 g의 부호, 목표 레벨은 GradGEO와 같은 규칙(head가 정함). 타깃마다 한 번만 뽑는다(seed 고정). |
| 전부 올리기 | 정제 후 남은 축 모두 최고 레벨 |
| 평균 처방 | 학습 split에서 테스트와 같은 규칙(층 3개 무작위)으로 뽑은 타깃들의 대표 passage에서 g를 구해 평균하고, 그 방향을 모든 타깃의 대표 passage + 메타에 똑같이 적용(같은 자동 k 규칙). **"타깃별 최적화" 주장을 검증하는 핵심 비교** |
| 단일 최종 인용 head | p_final을 head 하나로 직접 학습한 모델의 gradient |

### 11.2 기존 방법 (SAGEO 프로토콜: 문서 전체, scope = both, 각 방법의 원래 프롬프트)
- 원본(수정 없음)
- GEO 단일 전략 8개: Authoritative, Cite Sources, Fluency, Quotation, Easy Language, Statistics, Technical Terms, Unique Words
- SAGEO 2-전략 조합 4개: EasyLang+Quote, EasyLang+Stats, Fluency+Quote, Fluency+Stats
- All-in-One: SAGEO 정의 "8개 전략을 동시에 적용 + 굵게·레이아웃 같은 구조 서식"(Puerto et al. 2025를 따름). SAGEO 코드에서는 이름이 `content_improvement`다(`sageo_methods.py` ContentImprovement). C-SEO Bench(Puerto et al. 2025, arXiv 2506.11097)의 "Content Improvement" = "8개 변환을 한 번에 합친 방법"이고, 프롬프트 문구("fluent, authoritative, and persuasive … give structure to the text")가 코드와 같다.
- AutoGEO
- StageAware, StageAware + Q(원래 프롬프트에 `Target query: {q}`와 "이 질의에 대해 최적화하라" 한 문장만 추가, 전문은 부록 공개)
- 질의 사용 방법: AgenticGEO, MAGEO

### 11.3 보류 칸 (연구자에게 다시 묻기 전에는 실행하지 않는다)
| 칸 | 내용 | 비용 |
|---|---|---|
| ② 범위 비교 | GradGEO와 같은 처방을 본문 전체에 적용. "정확한 passage에 넣는 게 낫다"를 검증하고, 범위 효과와 처방 효과를 나눈다. | 비교 1칸(타깃마다 Rewriter 1번 + 파이프라인 재실행) |
| GradGEO − Q | 질의 종속 축을 빼고 head를 다시 학습. 질의를 모르면 대표 passage를 고를 수 없으므로 SAGEO처럼 본문 전체 범위가 논리적. 처방을 어떻게 만들지는 미정. | head 재학습 + 비교 1칸 |

주 결과표(질의 정보 2×2: {StageAware, GradGEO} × {질의 없음, 질의 있음})는 GradGEO − Q 칸이 확정되어야 완성된다.

### 11.4 권장 ablation
- 3 head vs 단일 head
- 쌍비교+BCE vs BCE만
- 선형 vs 비선형 head
- 2단계 학습 vs joint (z 의미 표류 지표와 함께)
- head 입력: z만 vs z + PCA 질의 임베딩(검증에서 고른 차원과 함께 셋 다 보고)
- 맥락 공변량 c 있음/없음
- 고정 k = 1, 3, 5 vs 자동 k(타깃별 k 분포 보고)
- head별 gradient 부호 표와 상충 비율(3 head를 쓰는 이유의 근거)
- 축 묶음: 본문 축만 / 구조 축만 / 전부

---

## 12. SAGEO Arena와의 관계

### 12.1 원문·코드로 확인한 사실
- (arXiv 2602.12187 HTML) 질의 2,700개(9개 데이터셋 × 300), 질의당 Google 결과 최대 100개(평균 63)로 코퍼스 171,003. BM25 Top-100 → 재정렬기 → Top-10을 생성 입력으로. 타깃 = 생성 입력 Top-10 중 무작위 1개. 사람이 만든 관련성 레이블(qrels)은 쓰지 않음.
- 전략 10개 = GEO 8개 + All-in-One + AutoGEO. 개수의 근거는 쓰지 않았다("10 LLM-based optimization strategies derived from prior research").
- All-in-One = "applies all eight strategies simultaneously along with structural formatting such as bolding and improved layout".
- 무작위 전략 비교는 없다.
- 재작성 단위는 문서 전체다. "SAGEO optimizes documents without access to the incoming query." 질의를 모르면 어느 passage가 뽑힐지 모르므로 문서 전체를 고치는 것이 그 전제에서는 맞는 선택이다.
- Table 2·3 Baseline 행(Retrieval H@20 0.58, Rerank H@10 1.00, Cite 0.50)은 원문과 일치 확인. Rerank H@10 = 1.00은 타깃 선정 방식상 당연한 값이다. StageAware의 Rerank H@10(0.80)은 baseline보다 낮다 → "모든 단계를 개선했다"고 쓰지 않는다.
- 위 수치는 HTML에서 옮긴 값이라 인용 전에 PDF와 한 번 더 대조한다.
- 검색(§3.3 식 (2), 부록 B): 의미 단위 = passage + 그 문서의 구조 정보. 요소마다 따로 BM25로 매기고 순위를 RRF로 합친다. 필드를 한 텍스트로 합치지 않는 이유는 "각 요소의 신호가 흐려지지 않게, 요소별 기여를 세밀하게 분석하려고"(Robertson et al. 2004 인용), 같은 가중치는 "필드별 편향이 아니라 전략 효과를 보려고"다. 필드별 검색 깊이(코드 200)와 점수 대신 순위로 합친 이유는 논문 v1·v2 어디에도 없다(2026-11-08 전문 검색). 다른 검색기(dense, hybrid) 강건성 확인은 본문만 고치는 설정에서만 했다(§5.4). 구조 필드 최적화 결과는 SAGEO 엔진에서만 확인된 것이다.

### 12.2 논문에 쓸 수 있는 차별점
1. 해석 가능한 전략 표현 z (루브릭 기반, 문장 번호 → 개수로 계산)
2. 단계별 선호 학습: passage 단위 head 3개, 조건부 모집단
3. 조건부 확률 결합 p_final = p_R · p_K · p_G
4. ∇_z log p_final로 타깃마다(대표 passage + 메타 단위) 다른 처방 — 병목 단계에 자동으로 가중
5. 질의를 알기 때문에 그 질의에서 문서 순위를 정하는 passage를 골라 정확히 고친다
6. 최적화 과정에 GE 반복 피드백이나 RL이 없다

대비 문장 예시:
> SAGEO Arena empirically identifies stage-dependent effects and codifies them into a fixed StageAware rewriting prompt applied to whole documents without access to the query. Our method instead learns differentiable stage-specific preference models over passages and optimizes a document-specific strategy target for the passage that determines the document's rank for the query.

### 12.3 신규성 위험 (관련 연구에서 반드시 차별화)
- GEO-SFE(2603.29979) 식 (7): 특징 벡터를 학습된 인용 예측기에 대해 최적화하는 형태(SF* = argmax Σ α_A·P(cite | SF, A))를 이미 제안. 차별점: 단계 분해 조건부 확률, 단계별 head, 문서별 gradient, passage 단위.
- AgenticGEO의 critic C((q,d), s): (질의, 문서)별 전략 가치를 학습하는 surrogate. 차별점: 연속 z + 단계 분해 + gradient vs 이산 전략 + 진화 탐색, 관찰 데이터 vs 개입 데이터, 다단계 검색 환경.

### 12.4 금지 표현
- "SAGEO가 단계 간 상충을 전혀 고려하지 않음"
- "SAGEO는 passage마다 전략을 정한다"(사실과 다름)
- "조건부 확률 곱으로 전략 간 인과적 상호작용을 학습함"
- 실험 전의 "SAGEO보다 우수함"
- "실제 상용 엔진은 대부분 MaxP다" 같은 단정(상용 엔진 내부는 비공개)
- 원문에 없는 내용 추가 허용·오프라인 환경에 대한 해명 문장
- "SAGEO 검색이 잘못 설계됐다" 같은 단정. 엔진을 바꾼 이유(C29)는 측정값(자기 본문 점수가 없는 passage 비율, 100위 선 동점 묶음)과 SAGEO가 밝힌 설계 의도(의미 단위·필드 분리·같은 가중치)를 지킨다는 점으로만 쓴다

---

## 13. 기술적 위험

| ID | 위험 | 대응 |
|---|---|---|
| A | 관측 선호 ≠ 개입 효과(최대 위험) | 검증 split 개입 점검으로 조기 검증, final test에서 확정 |
| B | 확률 보정 오류가 단계 간 gradient 가중치를 왜곡 | 조건부 모집단, BCE, 검증셋 temperature scaling |
| C | 판정 신뢰도 | 파일럿, 3회 중앙값, 정제 ②, 프롬프트 고정 |
| D | 재작성 실현율 | §9.3 측정, 지시 형식 조정 |
| E | 끼워 넣은 뒤 재청킹으로 조각 경계가 밀려 고친 내용이 두 조각으로 갈림 | 엔진이 실제로 보는 모습이라 그대로 둔다. 실현율을 재청킹 후 passage로 측정, 길이 비율 보고 |
| F | passage 단위에서 드문 특징(인용구·출처)의 예시 부족 | 정제 ③을 개수 기준으로 |
| G | 동점 passage. SAGEO 방식에서는 다른 필드에만 걸린 문서의 passage가 모두 같은 점수라 100위 선을 동점 묶음이 가로질렀다(질의 58%) | C29로 해결(B1: Top-300 동점 7%, 100위 선 0.7%). 남는 동점의 85%는 한 문서 안에서 질의 단어가 같은 횟수로 나오는 passage(Lucene이 길이를 구간으로 묶어 같게 봄, 그중 1/3은 질의 단어 없음), 15%는 가사 사이트처럼 거의 같은 글의 다른 문서. 내용으로 갈린 동점이라 따로 처리하지 않는다. p*는 동점이면 앞쪽 passage(§8.1). 사전 점검 ⑤로 세기만 한다 |
| H | 경쟁 passage, 경계, 청킹, GE 입력 위치에 의존 | 경계는 파이프라인 값과 일치, GE 순서 섞기 |
| I | split 누수, 문서 중복 | 근접 중복 질의 묶기, 검증과 test 분리 |
| J | 비교 방법의 문서 전체 재작성에서 긴 문서 누락·잘림 | 길이 비율·실패 보고(숨기지 않음) |

---

## 14. 구현 산출물

```
configs/
  experiment.yaml            # N, 경계(100/10), GE 1회(C33), 판정 3회, τ/k_max, seed
  strategy_schema.yaml       # v0.5 (20축 정의, 판정, Rewriter 문장, 정제 기준)
prep/                        # 코드 폴더(data/는 데이터 전용, git 제외)
  rule_axes.py               # 18~20번 규칙 축(목록·표, 강조, 문단 길이)
  precheck.py                # §10.2 사전 점검(passage 단위 엔진)
  engine_check/              # C29 근거: A·B0·B1 비교, 열별 점수 몫, 고치기 시험(B2 대조), BM25 직접 계산
  build_candidate_pool.py    # BM25 Top-N passage, 문서 순위(MaxP), 동점 표시
  build_stage_labels.py      # passage 단위 y_R/y_K/y_G, 조건부 null
  judge_passages.py          # 메타 + passage 목록 호출, 문장 번호 → 레벨, 3회 중앙값
  query_features.py          # [Q] 축, 질의 임베딩 PCA(Qwen3-Embedding-0.6B)
  split.py                   # 데이터셋별 60/20/20(검증 안에 val_mini), 근접 중복 질의 묶기
  dataset.py
models/
  strategy_encoder.py        # Qwen3 + LoRA (L_feat 전용, freeze)
  stage_heads.py             # [z | PCA] → logit_s
  citation_model.py          # temperature scaling, log p_final
training/
  pairwise_losses.py         # 순위가중 all-pairs + BCE, G soft label
  train_encoder.py
  train_heads.py
  validate.py                # 보정 포함
optimization/
  representative_passage.py  # p* 선택 (§8.1)
  gradient_strategy.py       # 수준 기반 처방(τ, k_max, head가 목표 수준 선택), mask, 상충 진단
rewriting/
  prompt_builder.py          # 루브릭 ▶/★, 허용·금지 목록
  rewriter_interface.py
  splice.py                  # p* 구간 교체, 재청킹, 길이 비율·편집량 기록
crawl/
  extract_markdown.py        # 환경 B 코퍼스(본문 마크다운 재추출)
rag/
  build_index.py             # URL당 한 번, 환경 B 청커, Lucene 단일 스레드(동점 순서 고정)
  chunking.py                # 환경 B 청커(SAGEO 청커 + 줄바꿈 경계·보존), chunk_units
  build_unit_index.py        # passage 단위 색인(열 5개, C29), Lucene 단일 스레드
  retrieval.py               # 기본: passage 단위 엔진(열별 BM25 점수 합). --engine sageo: SAGEO BM25 + RRF(비교용)
  reranking.py               # 점수 캐시, d' passage만 재채점
  generation.py              # 1회, 순서 섞기, 인용 파싱
  evaluation.py              # 층별 H@100/H@10/H@20/인용률/Δ순위, cluster bootstrap
experiments/
  judge_pilot.py             # §5.4
  intervention_check.py      # §10.3
  compare_methods.py         # §11 (보류 칸은 플래그로 막아 둠)
  ablation.py
tests/
```

### 최소 수용 테스트
1. z의 모든 좌표 ∈ [0,1], 차원 = 정제 후 K.
2. 문서 단위 축 값이 같은 문서의 모든 passage에서 같다.
3. K 손실은 Top-100 밖에서, G 손실은 Top-10 밖에서 0이다(mask).
4. p_final == p_R · p_K · p_G이며 log 공간 계산과 일치한다.
5. gradient는 z로만 흐른다. 질의 임베딩, 인코더, head 파라미터는 변하지 않는다.
6. Rewriter에 전달되는 목표는 z*(레벨)이다. delta가 아니다.
7. 끼워 넣기 후 본문에서 p* 밖의 문장은 원본과 글자 단위로 같다.
8. 재색인 후 코퍼스에 원본 d가 없고 d'만 있다.
9. 원본 실행으로 정한 p*가 §8.1 규칙과 일치한다(검색 탈락/통과 경우 모두).
10. 수정본의 전체 파이프라인 실행은 test 모드에서만 된다. 검증 모드는 검색·재정렬만 돈다.
11. 보류 칸(§11.3)은 플래그 없이 실행되지 않는다.
12. 비용, 호출 수, 지연시간이 로그로 남는다. 비밀값은 로그에 없다.

---

## 15. 구현 순서

0. **연구자 스키마 검수(가장 먼저)**: 연구자가 `configs/strategy_schema.yaml` 전체(20축 정의·루브릭·판정 지시·Rewriter 규칙)를 읽고 확인한다. 확인 전에는 스키마를 쓰는 LLM 호출(판정·재작성)을 하지 않는다.
1. **환경**: 환경 B 코퍼스(`crawl/extract_markdown.py`, 완료: 마크다운 170,204 / 평문 대체 2,428)와 환경 B 청커로 URL당 한 번 필드 인덱스를 만들고(`rag/build_index.py`), passage 단위 인덱스(`rag/build_unit_index.py`, C29)로 전 질의 검색(`rag/retrieval.py`, Top-N 여유 있게 500).
2. **사전 점검**(§10.2) → 연구자에게 표 → N 확정.
3. **재정렬 캐시**(GPU 서버) + **GE 1회**로 원본 레이블 수집.
4. **판정 파일럿**(§5.4) → 신뢰도·정제 결과 표 → 연구자 확인 → 최종 축 확정.
5. **head 학습**: 처음에는 판정 점수를 z로 바로 쓰고 작은 MLP로 시작(파일럿 도메인 후보: MS MARCO, FiQA, E-commerce 같은 3개 도메인 통째).
6. **개입 점검**(§10.3, 검증 split) → go/no-go, τ·k_max·실현율.
7. 통과하면 인코더로 z를 amortize.
8. **Final blind test**와 비교 방법(§11.1~11.2) 실행. 보류 칸은 그 전에 연구자에게 묻는다.

---

## 16. 후속 실험 (단일 GE 결과 이후)

1. **여러 GE**: GE만 바꾸고 G head만 새 GE 인용 레이블로 재학습(인코더·R/K head 그대로). 엔진 간 전이 행렬(A로 학습한 G head를 B에서 평가), 엔진별 전략 선호 표. 모든 GE에 강인한 처방: 엔진별 g를 단순 평균보다 잘 결합 — MGDA(연구자 선호, 주 방법) vs 단순 평균, 부호 일치 마스크, 최악 엔진 기준. 평가는 head 학습에 안 쓴 엔진에서.

---

## 17. 미정·확인 필요

| # | 항목 | 정하는 시점 |
|---|---|---|
| 1 | 5단계 vs 있다/없다(축별) | 판정 파일럿 |
| 2 | 판정할 passage 수(나머지는 인코더) | 사전 점검 비용표 |
| 3 | ② 범위 비교, GradGEO − Q 칸 | 본 실험 직전 연구자에게 재확인 |
| 4 | G head soft label 손실 세부 | head 구현 때 |
| 5 | PCA 차원(0/16/32, 0 = z만) | 검증 split |
| 6 | 판정 근거 논문 Thomas et al.의 학회(SIGIR 2024) | 논문 인용 전 ACM DL로 확인 |
