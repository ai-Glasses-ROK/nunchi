# 참고 자료

> 2026년 11월 기준으로 확인한 자료입니다. 버전이 빠르게 바뀌니 최신 문서를 함께 보세요.

## 목차

- [Meta Wearables Device Access Toolkit](#meta-wearables-device-access-toolkit)
- [로컬 AI](#로컬-ai)
- [수능 · 평가](#수능--평가)
- [재난문자](#재난문자)
- [기법](#기법)

## Meta Wearables Device Access Toolkit
- [개발 문서 — 빌드 개요](https://wearables.developer.meta.com/docs/develop/dat/build-overview/) — 모듈 구성, HFP 8kHz 마이크, 세션은 기기당 하나
- [iOS 통합 가이드](https://wearables.developer.meta.com/docs/develop/dat/build-integration-ios/) — 로컬 네트워크 권한, Wi-Fi 스트리밍
- [버전 의존성](https://wearables.developer.meta.com/docs/develop/dat/version-dependencies/) — 지원 기기, Meta AI 앱·펌웨어 버전
- [알려진 이슈](https://wearables.developer.meta.com/docs/knownissues)
- [iOS SDK (GitHub)](https://github.com/facebook/meta-wearables-dat-ios) · [Android SDK (GitHub)](https://github.com/facebook/meta-wearables-dat-android) — v1.0.1, 공식 샘플(CameraAccess, BirdSpotter)
- [Road to VR — DAT 소개 기사](https://roadtovr.com/meta-ray-ban-smart-glasses-third-party-app-sdk-device-access-toolkit/)

## 로컬 AI
- [Ollama](https://ollama.com) — [OpenAI 호환 API(음성 인식·합성 포함)](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx), [임베딩](https://github.com/ollama/ollama/blob/main/docs/capabilities/embeddings.mdx)
- [Gemma 4 (Ollama 라이브러리)](https://ollama.com/library/gemma4) · [LFM3-Audio](https://ollama.com/library/lfm3-audio) · [bge-m3](https://ollama.com/library/bge-m3)
- [Apple Foundation Models (뉴스룸)](https://www.apple.com/kr/newsroom/2025/09/apples-foundation-models-framework-unlocks-new-intelligent-app-experiences/)
- [Avenkin (구 OpenGlasses)](https://github.com/straff2002/OpenGlasses) — iPhone용 안경 AI 앱, 기기 내 모델 지원 (BSL 1.1)

## 수능 · 평가
- [한국교육과정평가원 수능 기출문제](https://www.suneung.re.kr/boardCnts/list.do?boardID=1500234&m=0403&s=suneung) — 2026학년도 문제지·정답표 (직접 받기)
- Pyeon et al., *Evaluating Large Language Models on the 2026 Korean CSAT Mathematics Exam: Measuring Mathematical Ability in a Zero-Data-Leakage Setting* (2025) — [arXiv:2511.18649](https://arxiv.org/abs/2511.18649). 24개 모델, 46문항(공통+선택 3과목), 100점 환산. 표 6(한국어 지시문): GPT-5 Codex 100(텍스트·이미지), GPT-5 텍스트 95.7·이미지 97.8, gpt-oss-20B 텍스트 95.7(사람이 옮긴 문제), Gemini 2.5 Pro 텍스트 91.3·이미지 93.5, GPT-5-nano 텍스트 89.1·이미지 67.4. 이 레포의 클라우드 비교 기준.

## 재난문자
- 위급재난문자는 수신 거부 불가, 긴급재난문자·안전안내문자는 설정 가능, 비행기 모드에서는 수신 불가 — 국내 IT 매체 정리(예: [yellowit.co.kr](https://yellowit.co.kr), [jab-guyver.co.kr](https://jab-guyver.co.kr/1684)). 메뉴 이름은 OS 버전마다 다를 수 있어요.

## 기법
- Robertson & Zaragoza, *The Probabilistic Relevance Framework: BM25 and Beyond* (2009)
- Cormack et al., *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods* (SIGIR 2009)
- Lewis et al., *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks* (NeurIPS 2020)
