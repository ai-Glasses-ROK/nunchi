# 실습 6 — Android 앱 (Nunchi)

> 상태: 코드는 Meta 공식 샘플(meta-wearables-dat-android 1.0.1, CameraAccess)의 API·빌드 설정을 그대로 따라 작성했지만, **이 레포 작성 시점에는 컴파일·실기기 검증을 하지 않았어요**(빌드 환경에 Android SDK가 없음). 처음 빌드할 때 오류가 나면 이슈로 알려 주세요.

## 1. 준비물

- Android Studio (최신) — JDK 17 포함
- Android 12(API 31) 이상 폰 (안경 마이크 경로 지정 API 때문에)
- (실사용) Meta AI 앱 + 안경, 개발자 모드 켜기

## 2. 열고 실행

1. Android Studio → **Open** → `clients/android`
2. Gradle 동기화 (Meta DAT는 Maven Central에 있어 토큰 없이 받아짐)
3. 폰 연결(USB 디버깅) → ▶︎ Run

배포용 앱 ID가 생기면 `clients/android/local.properties` 에:

```properties
mwdat_application_id=<Developer Center 앱 ID>
mwdat_client_token=<클라이언트 토큰>
```

## 3. 사용

1. 첫 실행: 블루투스·마이크 권한 허용 → DAT 초기화
2. ⚙︎ → 허브 주소·토큰 → 저장 (상단에 "허브: 연결됨")
3. ⚙︎ → **Meta AI와 연결** (처음 한 번, 인터넷 필요)
4. 🎙️ 버튼 → 말하기 → 다시 누르기 / 📷 → 안경 사진으로 묻기
5. 실물 안경이 없으면 ⚙︎ → **가상 안경** 켜기 + '안경 마이크 사용' 끄기

## 4. 코드 구조

| 파일 | 역할 |
|---|---|
| `MainActivity.kt` | 권한 요청 → `Wearables.initialize()`, Meta AI 권한 계약(RequestPermissionContract) |
| `MainViewModel.kt` | 상태·대화·허브 응답 처리 |
| `HubClient.kt` | HttpURLConnection + org.json 으로 SSE·multipart (외부 라이브러리 없음) |
| `Audio.kt` | 안경 마이크 녹음(블루투스 SCO) · 한국어 TTS · WAV 재생 |
| `Glasses.kt` | 세션 → 카메라 → 사진(JPEG) · 가상 안경(MockDeviceKit) |
| `ui/MainScreen.kt` | Compose 화면 |

### 안경 마이크 녹음 방식 (공식 샘플과 동일)

```kotlin
audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
audioManager.setCommunicationDevice(scoDevice)          // 안경(블루투스 SCO)
val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 16000, CHANNEL_IN_MONO, ENCODING_PCM_16BIT, …)
rec.setPreferredDevice(scoDevice)
// 녹음 끝나면 clearCommunicationDevice() + MODE_NORMAL → 답은 A2DP(음악 음질)로
```

### DatResult 다루기 팁
`onSuccess { }` 콜백 **안에서 suspend 함수를 부르지 마세요**. `fold(onSuccess = { it }, onFailure = { e, _ -> throw … })` 로 값을 꺼낸 뒤 바깥에서 기다리는 편이 안전합니다(이 레포의 `Glasses.kt` 방식).

## 5. 알려진 한계

- **단독 모드 없음**: 안드로이드에서 폰 안 LLM은 llama.cpp / MediaPipe·LiteRT 같은 엔진을 직접 붙여야 해요 → [폰 단독 모드](07-standalone-phone.md), [연구 로드맵](../05-applications/04-research-roadmap.md)
- 평문 HTTP 허용(`network_security_config.xml`): 로컬 허브 접속용. 허브를 `--https` 로 띄우면 지워도 됩니다.
- 사진마다 세션을 새로 만들고 닫아요(가장 단순하고 안정적). 연속 촬영이 많다면 세션을 유지하고 `removeCamera()` 를 쓰는 방식으로 바꿀 수 있어요.
