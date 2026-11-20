# Nunchi 문서 지도

> 처음이라면 위에서부터 차례로, 이미 개발을 한다면 필요한 곳으로 바로 가세요.
> 모든 문서는 "인터넷 없는 환경에서 내 안경으로 내 문서를 묻고 답하기"라는 한 가지 목표를 향합니다.

## 어디서부터 읽을까요?

| 나는… | 이 순서로 |
|---|---|
| 개발을 전혀 몰라요 | [큰 그림](01-basics/01-big-picture.md) → [용어 사전](01-basics/02-glossary.md) → [터미널 첫걸음](01-basics/07-terminal-101.md) → [허브 설치](03-setup/01-hub.md) → [첫 RAG 실습](04-practice/01-first-rag.md) |
| 일단 돌려 보고 싶어요 | [허브 설치](03-setup/01-hub.md) → [첫 RAG 실습](04-practice/01-first-rag.md) → [음성](04-practice/02-voice.md) → [사진](04-practice/03-vision.md) |
| 안경으로 실사용하고 싶어요 | [공기계 준비](03-setup/03-spare-phone.md) → [재난문자 없이 쓰기](03-setup/04-airplane-mode.md) → [오프라인 와이파이](03-setup/05-offline-network.md) → [iOS 앱](04-practice/05-ios-app.md) 또는 [Android 앱](04-practice/06-android-app.md) |
| 드론(픽스호크)을 붙이고 싶어요 | [드론 연결](04-practice/08-drone.md) → [드론 영상: 카메라+송신기](04-practice/09-fpv-video.md) → [Android 앱](04-practice/06-android-app.md) → [폰 단독 모드](04-practice/07-standalone-phone.md) |
| 연구 결과가 궁금해요 | [평가와 실험 결과](05-applications/03-evaluation.md) → [구조와 설계 결정](02-concepts/01-architecture.md) → [연구 로드맵](05-applications/04-research-roadmap.md) |

## 전체 목록

### 1. 기초 — 개발 몰라도 읽을 수 있어요
1. [한 장으로 보는 큰 그림](01-basics/01-big-picture.md) — 안경·폰·허브가 각각 무슨 일을 하나
2. [용어 사전](01-basics/02-glossary.md) — LLM, RAG, 임베딩, HFP, 양자화… 70여 개
3. [LLM은 어떻게 말을 할까](01-basics/03-how-llm-works.md) — 토큰, 파라미터, 양자화, 환각
4. [RAG는 어떻게 내 문서를 아나](01-basics/04-how-rag-works.md) — 조각내기, 검색, 프롬프트
5. [소리는 어떻게 글이 되고 글은 소리가 되나](01-basics/05-how-voice-works.md) — 샘플레이트와 8kHz 문제
6. [안경은 폰과 어떻게 이야기하나](01-basics/06-how-glasses-connect.md) — 블루투스 프로파일과 Meta DAT
7. [터미널 첫걸음](01-basics/07-terminal-101.md) — 명령어 복사·붙여넣기부터

### 2. 개념 — 왜 이렇게 만들었나
1. [구조와 설계 결정](02-concepts/01-architecture.md) — 세 가지 동작 모드, 지연시간 예산
2. [오프라인과 재난문자](02-concepts/02-offline-and-alerts.md) — 셀 방송(CBS)의 원리와 안전 원칙
3. [모델 고르기](02-concepts/03-model-selection.md) — 메모리 계산과 추천 조합
4. [한국어 RAG 노하우](02-concepts/04-korean-rag.md) — 2-gram BM25, 질문 꼬리말, 사진 캡션 검색
5. [음성 UX 원칙](02-concepts/05-voice-ux.md) — 귀로 듣는 답은 달라야 한다

### 3. 설치 — 준비하기
1. [허브 설치 (맥/윈도우/리눅스)](03-setup/01-hub.md)
2. [인터넷이 될 때 미리 챙길 것](03-setup/02-offline-bundle.md) — 오프라인 꾸러미 체크리스트
3. [공기계 준비](03-setup/03-spare-phone.md)
4. [재난문자 방해 없이 쓰기](03-setup/04-airplane-mode.md)
5. [인터넷 없는 와이파이 만들기](03-setup/05-offline-network.md)

### 4. 실전 — 코드와 사용법
1. [첫 RAG: 문서 넣고 묻기](04-practice/01-first-rag.md)
2. [음성으로 묻기](04-practice/02-voice.md) — 파일, 맥 마이크, 웹
3. [사진으로 묻기](04-practice/03-vision.md) — 캡션→검색
4. [허브 API 레퍼런스](04-practice/04-hub-api.md)
5. [iOS 앱](04-practice/05-ios-app.md) — 가상 안경 포함
6. [Android 앱](04-practice/06-android-app.md)
7. [폰 단독 모드](04-practice/07-standalone-phone.md) — Android도 폰 안 RAG(2-gram BM25 + 폰 Gemma 4)
8. [드론(픽스호크) 연결](04-practice/08-drone.md) — 드론 카메라 → Gemma 4 → 화면, 실시간 상태로 RAG
9. [드론 영상 연결](04-practice/09-fpv-video.md) — 소형 FPV 카메라 + 5.8GHz 송신기(VTX), 받는 쪽 수신기, 영상 중계

### 5. 응용 — 더 잘 쓰기
1. [활용 시나리오](05-applications/01-use-cases.md)
2. [튜닝: 빠르게, 정확하게, 오래](05-applications/02-tuning.md)
3. [평가와 실험 결과](05-applications/03-evaluation.md) — 수능·지연시간·8kHz 실측
4. [연구 로드맵](05-applications/04-research-roadmap.md)

### 6. 참고
1. [문제 해결](06-reference/01-troubleshooting.md)
2. [자주 묻는 질문](06-reference/02-faq.md)
3. [안전·개인정보·법](06-reference/03-safety-privacy-legal.md)
4. [참고 자료](06-reference/04-sources.md)
