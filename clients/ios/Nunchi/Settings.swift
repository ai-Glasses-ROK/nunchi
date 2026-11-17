import Foundation

/// 동작 방식
enum AssistantMode: String, CaseIterable, Identifiable {
    case hub        // 같은 와이파이의 허브(컴퓨터)가 인식·검색·답변
    case onDevice   // 폰 안에서 전부 처리 (네트워크 없음)

    var id: String { rawValue }
    var label: String {
        switch self {
        case .hub: return "허브 모드 (같은 와이파이의 컴퓨터)"
        case .onDevice: return "단독 모드 (폰 안에서 전부)"
        }
    }
}

/// 질문을 녹음할 마이크
enum MicSource: String, CaseIterable, Identifiable {
    case glasses  // 안경 마이크 (블루투스 HFP, 8kHz)
    case phone    // 폰 내장 마이크 (안경 없이 테스트할 때)

    var id: String { rawValue }
    var label: String { self == .glasses ? "안경 마이크" : "폰 마이크" }
}

/// 답을 읽어 줄 목소리
enum VoiceOutput: String, CaseIterable, Identifiable {
    case phone  // 폰의 한국어 음성 합성 (가장 빠름, 오프라인)
    case hub    // 허브가 만든 음성 재생
    case off

    var id: String { rawValue }
    var label: String {
        switch self {
        case .phone: return "폰이 읽기 (권장)"
        case .hub: return "허브 음성"
        case .off: return "읽지 않기"
        }
    }
}

/// UserDefaults 키 (SwiftUI의 @AppStorage 와 같은 키를 씁니다)
enum SettingsKey {
    static let hubURL = "hubURL"
    static let hubToken = "hubToken"
    static let mode = "mode"
    static let mic = "micSource"
    static let voice = "voiceOutput"
}

struct CurrentSettings {
    var hubURL: String
    var hubToken: String
    var mode: AssistantMode
    var mic: MicSource
    var voice: VoiceOutput

    static func load() -> CurrentSettings {
        let d = UserDefaults.standard
        return CurrentSettings(
            hubURL: d.string(forKey: SettingsKey.hubURL) ?? "http://192.168.0.10:8765",
            hubToken: d.string(forKey: SettingsKey.hubToken) ?? "",
            mode: AssistantMode(rawValue: d.string(forKey: SettingsKey.mode) ?? "") ?? .hub,
            mic: MicSource(rawValue: d.string(forKey: SettingsKey.mic) ?? "") ?? .glasses,
            voice: VoiceOutput(rawValue: d.string(forKey: SettingsKey.voice) ?? "") ?? .phone
        )
    }
}
