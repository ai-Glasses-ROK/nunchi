# Nunchi Android 앱

안경 마이크(블루투스 SCO)·카메라(Meta DAT) → 허브 → 폰 TTS로 안경 스피커. 드론(픽스호크 MAVLink + 드론 카메라 → Gemma 4 → 화면)과 폰 단독 RAG도 → [드론 실험](../../docs/07-drone/01-overview.md) · [실습 8](../../docs/04-practice/08-drone.md)

## 목차

- [상태](#상태)
- [열고 실행](#열고-실행)
- [코드 구조](#코드-구조)
- [라이선스](#라이선스)

## 상태

- 상태: **빌드·실행 검증** — JDK 21로 `assembleDebug`·`lintDebug`(오류 0)·`testDebugUnitTest`(23개) 통과, Android 15 에뮬레이터에서 가상 안경 사진 → 허브 비전 답변, 드론 카메라 → Gemma 4 → 화면, 실기 픽스호크 상태(맥 UDP 중계)까지 확인. 실물 안경·실기기(폰)는 미검증.
- 최소: Android 12 (API 31)

## 열고 실행

Android Studio → Open → 이 폴더 → Run. 가이드: [docs/04-practice/06-android-app.md](../../docs/04-practice/06-android-app.md)

## 코드 구조

| 파일 | 역할 |
|---|---|
| `MainActivity.kt` | 권한 → `Wearables.initialize()`, Meta AI 권한 요청 계약 |
| `MainViewModel.kt` | 상태·대화·SSE 처리 |
| `HubClient.kt` | HttpURLConnection + org.json (외부 라이브러리 없음) |
| `Audio.kt` | SCO 녹음 → 16kHz WAV, TextToSpeech(한국어), WAV 재생 |
| `Glasses.kt` | 세션 → 카메라 → 사진, 가상 안경(MockDeviceKit) |
| `ui/MainScreen.kt` · `ui/DroneScreen.kt` | Compose 화면 · 드론 화면 |
| `drone/` | MAVLink 파서, USB OTG 시리얼(CDC·FTDI·CP210x·CH340), UDP·TCP·블루투스, 가상 드론, 실시간 상태 |
| `camera/DroneCamera.kt` | 드론 카메라: UVC 수신기·MJPEG·가상 |
| `ai/` · `rag/` | 폰 Gemma 4(LiteRT-LM)·폰 RAG, 2-gram BM25(허브와 같은 규칙) |

## 라이선스

`gradlew`·`gradle-wrapper.jar`는 Gradle Wrapper(Apache 2.0)이고, Meta DAT는 Maven Central에서 받습니다(토큰 불필요).
