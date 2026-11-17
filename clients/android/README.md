# Nunchi Android 앱

안경 마이크(블루투스 SCO)·카메라(Meta DAT) → 허브 → 폰 TTS로 안경 스피커.

- 상태: Meta 공식 샘플(CameraAccess, DAT 1.0.1)의 API·빌드 버전을 따라 작성. **컴파일·실기기 미검증** — 빌드 오류는 이슈로 알려 주세요.
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
