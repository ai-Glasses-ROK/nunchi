import MWDATCore
import SwiftUI

/// 앱 시작점. Meta DAT SDK는 앱이 켜질 때 한 번 `Wearables.configure()` 를 불러야 합니다.
@main
struct NunchiApp: App {
    @StateObject private var model: AppModel

    init() {
        do {
            try Wearables.configure()
            DATStatus.configured = true
        } catch {
            // 시뮬레이터 등 일부 환경에서는 실패할 수 있어요. 실패해도 허브/단독 모드의 음성·글자 기능은 동작합니다.
            DATStatus.configured = false
            DATStatus.configureError = "\(error.description) (code \(error.rawValue))"
            NSLog("[Nunchi] DAT configure 실패: \(error.description) code=\(error.rawValue)")
        }
        _model = StateObject(wrappedValue: AppModel())
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .onOpenURL { url in
                    // Meta AI 앱이 등록/권한 처리 후 돌려보내는 콜백만 SDK에 넘깁니다.
                    guard
                        DATStatus.configured,
                        let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
                        components.queryItems?.contains(where: { $0.name == "metaWearablesAction" }) == true
                    else { return }
                    Task {
                        do {
                            _ = try await Wearables.shared.handleUrl(url)
                        } catch {
                            await MainActor.run { model.glasses.lastError = "Meta AI 콜백 처리 실패: \(error.localizedDescription)" }
                        }
                    }
                }
        }
    }
}

/// SDK 초기화 결과 (초기화 실패 시 Wearables.shared 에 접근하지 않기 위해)
enum DATStatus {
    static var configured = false
    static var configureError: String?
}
