# Nunchi Android 앱

안경 마이크(블루투스 SCO)·카메라(Meta DAT) → 허브 → 폰 TTS로 안경 스피커.

- 상태: **빌드·실행 검증** — JDK 21로 `assembleDebug`·`lintDebug`(오류 0) 통과, Android 15 에뮬레이터에서 가상 안경 사진 → 허브 비전 답변까지 확인. 실물 안경·실기기는 미검증.
- 최소: Android 12 (API 31)

Android Studio → Open → 이 폴더 → Run. 가이드: [docs/04-practice/06-android-app.md](../../docs/04-practice/06-android-app.md)

| 파일 | 역할 |
|---|---|
| `MainActivity.kt` | 권한 → `Wearables.initialize()`, Meta AI 권한 요청 계약 |
| `MainViewModel.kt` | 상태·대화·SSE 처리 |
| `HubClient.kt` | HttpURLConnection + org.json (외부 라이브러리 없음) |
| `Audio.kt` | SCO 녹음 → 16kHz WAV, TextToSpeech(한국어), WAV 재생 |
| `Glasses.kt` | 세션 → 카메라 → 사진, 가상 안경(MockDeviceKit) |
| `ui/MainScreen.kt` | Compose 화면 |

`gradlew`·`gradle-wrapper.jar`는 Gradle Wrapper(Apache 2.0)이고, Meta DAT는 Maven Central에서 받습니다(토큰 불필요).
