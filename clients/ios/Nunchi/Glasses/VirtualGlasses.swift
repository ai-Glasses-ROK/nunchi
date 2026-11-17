import Foundation
import UIKit
#if DEBUG
import MWDATMockDevice
#endif

/// 실물 안경 없이 개발·연구할 때 쓰는 '가상 안경' (Meta DAT의 MockDeviceKit).
///
/// 켜면 SDK 안에 가상 레이밴 메타가 생기고, 앱은 실물과 똑같은 코드(등록 → 세션 → 카메라 → 사진)로 동작합니다.
/// - 기본: 앱에 든 '발전기 계기판(E2)' 영상·사진이 안경 카메라로 들어갑니다 (시뮬레이터에서도 동작).
/// - 선택: 실제 아이폰에서 '폰 카메라 사용'을 켜면 폰 뒷면 카메라가 안경 카메라 역할을 합니다.
/// - 마이크는 가상화되지 않으므로, 가상 안경 모드에서는 '폰 마이크'로 녹음하세요.
///
/// MockDeviceKit은 테스트 전용이라 Debug 빌드에서만 켤 수 있습니다.
@MainActor
final class VirtualGlasses: ObservableObject {
    static let shared = VirtualGlasses()

    @Published private(set) var isOn = false
    @Published private(set) var status = "꺼짐"
    /// 실제 아이폰에서 폰 카메라를 안경 카메라로 쓸지 (다음에 켤 때 적용)
    @Published var usePhoneCamera = false

    static var isSupported: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    #if DEBUG
    private var device: MockGlasses?
    #endif

    func turnOn() {
        #if DEBUG
        guard !isOn else { return }
        guard DATStatus.configured else {
            status = "SDK 초기화 실패로 사용할 수 없어요"
            return
        }
        do {
            let kit = MockDeviceKit.shared
            kit.enable()  // '등록됨 + 권한 허용' 상태로 시작
            let glasses = try kit.pairGlasses(model: .rayBanMeta)
            glasses.powerOn()
            glasses.unfold()  // 다리를 펴야 기기 목록에 나타납니다
            glasses.don()     // 착용 상태
            let camera = glasses.services.camera
            if usePhoneCamera {
                // 실제 아이폰: 폰 뒷면 카메라를 안경 카메라처럼 (사진도 이 영상에서 찍힘 — 실기기 검증 필요)
                camera.setCameraFeed(cameraFacing: .back)
            } else {
                // 기본: 앱에 들어 있는 '발전기 계기판(E2)' 영상과 사진 → 시뮬레이터에서도 동작
                if let feed = Bundle.main.url(forResource: "virtual-glasses-feed", withExtension: "mp4") {
                    camera.setCameraFeed(fileURL: feed)
                }
                if let photo = Self.makeSamplePhoto() {
                    camera.setCapturedImage(fileURL: photo)
                }
            }
            device = glasses
            isOn = true
            status = "가상 레이밴 메타 연결됨"
            debugLog("virtual glasses on")
        } catch {
            status = "켜기 실패: \(error.localizedDescription)"
        }
        #endif
    }

    func turnOff() {
        #if DEBUG
        guard isOn else { return }
        MockDeviceKit.shared.disable()
        device = nil
        isOn = false
        status = "꺼짐"
        #endif
    }

    /// 가상 안경 카메라가 '찍을' 예시 사진: 가상의 발전기(HG-2000) 계기판에 E2 코드가 떠 있는 장면.
    /// 비전 모델이 'E2'를 읽고, 허브의 매뉴얼(RAG)에서 '엔진 과열' 대처법을 찾아 답하는지 시험할 수 있어요.
    static func makeSamplePhoto() -> URL? {
        let size = CGSize(width: 960, height: 720)
        let image = UIGraphicsImageRenderer(size: size).image { ctx in
            UIColor(white: 0.85, alpha: 1).setFill()
            ctx.fill(CGRect(origin: .zero, size: size))
            // 계기판 몸체
            let body = UIBezierPath(roundedRect: CGRect(x: 80, y: 90, width: 800, height: 540), cornerRadius: 36)
            UIColor(red: 0.95, green: 0.55, blue: 0.10, alpha: 1).setFill()
            body.fill()
            // 액정
            let screen = CGRect(x: 230, y: 200, width: 500, height: 230)
            UIColor(red: 0.05, green: 0.12, blue: 0.08, alpha: 1).setFill()
            UIBezierPath(roundedRect: screen, cornerRadius: 12).fill()
            let center = NSMutableParagraphStyle()
            center.alignment = .center
            ("E2" as NSString).draw(
                in: screen.insetBy(dx: 0, dy: 20),
                withAttributes: [
                    .font: UIFont.monospacedSystemFont(ofSize: 150, weight: .bold),
                    .foregroundColor: UIColor(red: 1, green: 0.25, blue: 0.2, alpha: 1),
                    .paragraphStyle: center,
                ]
            )
            ("HG-2000" as NSString).draw(
                in: CGRect(x: 80, y: 120, width: 800, height: 60),
                withAttributes: [.font: UIFont.boldSystemFont(ofSize: 44), .foregroundColor: UIColor.black, .paragraphStyle: center]
            )
            // 경고등
            UIColor.red.setFill()
            UIBezierPath(ovalIn: CGRect(x: 300, y: 480, width: 70, height: 70)).fill()
            UIColor.yellow.setFill()
            UIBezierPath(ovalIn: CGRect(x: 590, y: 480, width: 70, height: 70)).fill()
        }
        guard let data = image.jpegData(compressionQuality: 0.9) else { return nil }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("virtual-glasses-photo.jpg")
        do {
            try data.write(to: url, options: .atomic)
            return url
        } catch {
            return nil
        }
    }
}

/// Debug 빌드에서만 콘솔에 남기는 로그 (연구용 자동 점검에 사용)
func debugLog(_ message: String) {
    #if DEBUG
    NSLog("[Nunchi] %@", message)
    #endif
}
