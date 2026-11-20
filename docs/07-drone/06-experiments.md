# 드론 실험 6 — 실험 기록과 결과

> 지금까지 한 시험을 조건·결과·배운 것 순서로 남겨요. 모두 2026-11-20에 한 번씩 잰 값이에요(반복 측정 아님).

## 목차

- [실험 환경](#실험-환경)
- [E1. 실기 MAVLink 수신](#e1-실기-mavlink-수신)
- [E2. 상태→검색 (검색만)](#e2-상태검색-검색만)
- [E3. 상태 질문에 답하기](#e3-상태-질문에-답하기)
- [E4. 드론 카메라 → 답](#e4-드론-카메라--답)
- [E5. 어디까지 확인했나](#e5-어디까지-확인했나)
- [E6. 자동 테스트](#e6-자동-테스트)
- [가상 드론 시나리오](#가상-드론-시나리오)
- [재현](#재현)
- [한계](#한계)
- [다음 실험](#다음-실험)

## 실험 환경

| 항목 | 내용 |
|---|---|
| 허브 | 맥북, Ollama `gemma4:e4b-mlx`, 예제 문서 4개 (조각 44개) |
| Android | Android 15 에뮬레이터, 디버그 빌드 |
| iOS | iPhone 17 시뮬레이터 (iOS 27.2) |
| 기체 | B-CUBE 픽스호크 2.4.8, ArduCopter V3.6.12, 책상 위·프로펠러 없음, 보정 전 |
| 연결 | 맥 USB → `scripts/mavlink_bridge.py` → UDP → 에뮬레이터(`adb emu redir`)·시뮬레이터 |
| 영상 | 앱의 가상 카메라, 맥에 연결된 실제 카메라(`scripts/video_relay.py`) |

## E1. 실기 MAVLink 수신

- **방법**: 픽스호크 micro-USB를 맥에 꽂고 앱과 같은 규칙의 파서로 35초 동안 읽음.
- **결과**: 앱이 쓰는 메시지 **981개, CRC 오류 0**, MAVLink v1, ArduCopter V3.6.12 · PX4v3 · QUAD, sys 1.
- **받은 PreArm**: Hardware safety switch · 3D Accel calibration needed · Compass not calibrated(두 번) · RC Roll / Pitch / Throttle / Yaw not configured · Throttle below Failsafe · Radio failsafe on.
- **배운 것**
  - ArduPilot은 실패한 점검을 30초마다 **한꺼번에** 보내요 → '최근 묶음' 규칙 ([문서 4 §3](04-status-then-retrieve.md#3-prearm-최근-묶음-규칙--실기에서-배운-것))
  - 가상 드론으로는 안 보이던 버그가 실기 중계에서 드러났어요: Kotlin `DatagramSocket(...).apply { bind(InetSocketAddress(port)) }` 안의 `port`가 소켓의 `getPort()`(-1)로 해석돼 `port out of range:-1` → 고치고 회귀 테스트 추가
  - 테스트 자료로 남긴 캡처(`pixhawk_arducopter3612_usb.bin`)는 위치·해발고도·기압 필드를 0으로 지우고 CRC를 다시 계산했어요. 좌표가 든 메시지는 뺐어요

## E2. 상태→검색 (검색만)

| 질문 | 검색어에 붙은 것 | 1위 |
|---|---|---|
| 왜 시동이 안 걸려? | (없음) | PreArm 소개 문단 |
| 왜 시동이 안 걸려? | `PreArm: Compass not calibrated` | **나침반 보정 절** |
| 왜 시동이 안 걸려? | 실기 묶음 4개 | **조종기 · 가속도계 · 나침반 절 1~3위** |

허브와 폰의 BM25가 같은 결과를 내는지도 따로 확인해요. 예제 문서 44조각, 질문 28개에서 1위가 **28/28 일치**했어요(`LocalRagParityTest`, 기대값은 `hub/tests/make_bm25_parity.py`로 허브 코드에서 만듦).

## E3. 상태 질문에 답하기

| 조건 | 시간 | 결과 |
|---|---|---|
| Android 앱 → 허브 Gemma 4, 실기 상태(UDP 중계) | 1.1초 | 실기 경고 4개를 "조종기, 가속도계, 나침반, 그리고 스로틀"로 요약 |
| iOS 앱 자동 점검 → 허브 Gemma 4, 실기 상태 | — | "조종기·가속도계·나침반 점검이 끝나지 않았다 → 조종기 보정, 3차원 가속도계 보정, 나침반 보정을 먼저" |
| Android 드론 화면, 가상 카메라 + 실기 기록 재생 | 1.7초 | 참고 문서 1~4위: 조종기 · 가속도계 · PreArm 소개 · 나침반 → "조종기 보정, 가속도계 보정, 나침반 보정 등이 필요" |
| 같은 질문, 폰 단독 (LLM 없이) | 1~4ms | 기체 메시지 + 문서의 해결 문장 |

> 첫 iOS 시험에서는 PreArm 묶음이 오기 전에 물어서 GPS 탓을 하는 답이 나왔어요. 그래서 GPS 미수신을 검색 힌트에서 빼고, iOS 자동 점검은 묶음을 한 번 받은 뒤(30초) 묻게 바꿨어요.

## E4. 드론 카메라 → 답

| 영상 | 앱 | 받은 영상 | 캡션 | 검색 | 첫 글자 | 전체 |
|---|---|---|---|---|---|---|
| 가상 (640×360) | Android | 9fps | 0.38초 | 32ms | 1.2초 | **1.5초** |
| 맥의 실제 카메라 (960×1706, `video_relay.py` 10fps) | Android | 9~10fps | 0.81초 | 0.28초 | 1.84초 | **2.3초** |
| 같은 실제 카메라 | iOS | 7fps | 1.75초 | 0.06초 | 3.23초 | **3.9초** |

- 실제 카메라로는 방 안 장면(선반·상자 등)을 한두 문장으로 맞게 설명했어요. 사람이 비칠 수 있어 그 화면은 싣지 않았어요.
- **배운 것**: macOS 카메라 권한은 *명령을 실행한 앱* 기준이에요. 다른 앱 안에 든 터미널은 권한 창을 못 띄우고 멈춰서, 맥 기본 터미널에서 중계를 켰어요. 또 `ffmpeg -listen 1` 중계는 연결 하나만 받고 끊기면 끝나서, 여러 앱이 동시에 붙고 다시 붙을 수 있는 `video_relay.py`를 만들었어요.

## E5. 어디까지 확인했나

| | Android 에뮬레이터 | iOS 시뮬레이터 | 실제 폰 |
|---|---|---|---|
| 가상 드론 | ✅ | — (iOS는 UDP만) | ⏳ |
| 실기 비행컨트롤러 (컴퓨터 UDP 중계) | ✅ | ✅ | ⏳ |
| USB OTG 직결 · 블루투스 SPP | — | — | ⏳ |
| 가상 카메라 | ✅ | ✅ | ⏳ |
| MJPEG (실제 카메라 중계) | ✅ | ✅ | ⏳ |
| USB 영상 수신기 (UVC) | — | — | ⏳ |
| 허브 Gemma 4 | ✅ | ✅ | ⏳ |
| 폰 추출형 답 | ✅ | — | ⏳ |
| 폰 Gemma 4 (LiteRT-LM) | — | — | ⏳ |

## E6. 자동 테스트

| 묶음 | 수 | 내용 |
|---|---|---|
| Android 단위 테스트 | 23개 | MAVLink 교차 검증, 실기 캡처, USB 보드레이트 계산, UDP·TCP 소켓, 가상 시나리오 상태·보고서, 허브와 같은 검색 결과 |
| 허브 드론 테스트 | 7개 | 실시간 상태가 프롬프트에 들어가는지, 힌트를 언제 붙이는지, 힌트로 검색 순위가 바뀌는지 |
| 허브 전체 | 52개 | CI에서 Python 3.10·3.12로 실행 |

## 가상 드론 시나리오

하드웨어 없이 전 과정을 시험하는 100초짜리 시나리오예요(끝나면 처음부터 반복).

| 시간 | 상태 | 기체 메시지 |
|---|---|---|
| 0~12초 | STABILIZE · 시동 OFF · GPS 위치 못 잡음 (위성 0 → 6) | `PreArm: Need 3D Fix` |
| 12~40초 | GPS 3D (위성 10) | `PreArm: Compass not calibrated` (바뀌면 바로, 그 뒤 10초마다) |
| 40~75초 | 시동 → LOITER, 15m 이륙 후 원 비행, 배터리 98% → 28% | — |
| 75~95초 | RTL 귀환 → 착륙 → 시동 꺼짐 | `Battery 1 is low 14.90V used 1850 mAh` |

## 재현

가상 드론 + 가상 카메라 + 드론 화면 (Android):

```bash
adb shell am start -n io.github.aiglassesrok.nunchi/.MainActivity --es nunchi_demo drone
```

실기 픽스호크를 맥에 꽂고 에뮬레이터로 중계 (앱: 드론 화면 → Wi-Fi UDP → 연결):

```bash
adb emu redir add udp:14550:14550
```

```bash
python3 scripts/mavlink_bridge.py --udp 127.0.0.1:14550
```

드론 화면에서 질문까지 자동으로 (디버그 빌드):

```bash
adb shell "am start -n io.github.aiglassesrok.nunchi/.MainActivity --es nunchi_link udp --es nunchi_camera virtual --es nunchi_drone_ask '왜 시동이 안 걸려?'"
```

맥의 실제 카메라를 앱으로 (맥 기본 터미널에서, 앱: 네트워크(MJPEG) → 에뮬레이터 `http://10.0.2.2:8090/stream.mjpg`, iOS 시뮬레이터 `http://127.0.0.1:8090/stream.mjpg`):

```bash
python3 scripts/video_relay.py --device 0
```

iOS 시뮬레이터 자동 점검: Xcode 실행 인자 `-NunchiSelfTest drone` (드론 화면에 저장된 카메라로 분석하고, 30초 뒤 "왜 시동이 안 걸려?"를 물음).

자동 테스트:

```bash
cd clients/android && ./gradlew testDebugUnitTest
```

```bash
cd hub && .venv/bin/python -m pytest -q
```

## 한계

- 모두 **한 번씩** 잰 값이에요. 안경 실험처럼 서로 다른 질문으로 16번씩 재는 p50/p90 측정은 아직이에요.
- 에뮬레이터·시뮬레이터에서 잰 값이라, 실제 폰의 성능·USB·블루투스와 다를 수 있어요.
- 책상 위 정지 상태예요. 비행 중의 진동·전파 간섭·움직이는 영상은 시험하지 않았어요.
- '실기 기록 재생' 화면은 실제로 잡은 MAVLink를 되풀이해 보낸 것이에요(기체가 그 순간 연결돼 있던 것은 아님).
- 지식베이스가 예제 문서 1편(픽스호크 점검 안내)이에요. 실제 기체 매뉴얼·정비 기록으로 넓히면 결과가 달라질 수 있어요.

## 다음 실험

- [ ] 5.8GHz OTG 수신기로 VTX 영상 실수신 → 아날로그 잡음 속 캡션·답 품질
- [ ] 텔레메트리 라디오 쌍, 실제 폰의 USB OTG·블루투스
- [ ] 폰 안 Gemma 4(E2B) 사진 분석 지연
- [ ] 드론 점검 질문 세트를 만들어 정답률과 지연(p50/p90) 반복 측정
- [ ] 비행 로그(DataFlash `.bin`)를 문서로 넣어 "아까 왜 착륙했지?" 같은 질문
- [ ] 허브에 영상 중계 내장 (수신기를 허브에 꽂으면 `/v1/camera`로) → [연구 로드맵](../05-applications/04-research-roadmap.md)

---

[← 드론 카메라 → Gemma 4](05-camera-to-gemma.md) · [드론 실험 문서 처음](01-overview.md) · [다음: 안전·법·전파·개인정보 →](07-safety.md)
