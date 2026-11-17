# Nunchi iOS 앱

안경 마이크·카메라(Meta DAT) → 허브 또는 기기 내 처리 → 안경 스피커.

- 상태: iOS 시뮬레이터 **빌드·실행 검증** (Xcode 27.2 / iOS 27.2 SDK / DAT 1.0.1), 가상 안경으로 사진 질문 자동 점검 통과
- 최소: iOS 17.2 (DAT 요구 사항), 단독 모드 LLM은 iOS 26+ & Apple Intelligence 기기

```bash
open Nunchi.xcodeproj
```

Signing & Capabilities에서 **Team**과 **Bundle Identifier**(`com.<내이름>.nunchi`)를 바꾼 뒤 실행하세요.
가이드: [docs/04-practice/05-ios-app.md](../../docs/04-practice/05-ios-app.md)

| 폴더 | 내용 |
|---|---|
| `Nunchi/Hub` | 허브 API·SSE 클라이언트 |
| `Nunchi/Audio` | 안경 마이크(HFP) 녹음, 한국어 TTS·재생 |
| `Nunchi/Glasses` | DAT 등록·세션·카메라, 가상 안경(MockDeviceKit) |
| `Nunchi/OnDevice` | 단독 모드: kb.json + 2-gram BM25 + Foundation Models |
| `Nunchi/Views` | SwiftUI 화면 |
| `Nunchi/Resources` | 가상 안경 카메라 영상(HEVC 14KB) |
| `tools/` | 가상 안경 영상 다시 만들기 (`make_virtual_feed.sh`) |
| `project.yml` | XcodeGen 설정 (`xcodegen generate` 로 프로젝트 재생성) |

Meta DAT를 쓰면 [Meta Wearables Developer Terms](https://wearables.developer.meta.com/terms)에 동의하는 것입니다.
