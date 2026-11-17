import Foundation
import MWDATCamera
import MWDATCore

enum GlassesError: LocalizedError {
    case sdkNotReady(String)
    case noDevice
    case cameraDenied
    case cameraUnavailable
    case captureNotStarted
    case timeout(String)
    case sessionStopped
    case stream(String)

    var errorDescription: String? {
        switch self {
        case .sdkNotReady(let why): return "DAT SDK가 준비되지 않았어요: \(why)"
        case .noDevice: return "연결된 안경이 없어요. 안경을 켜고 다리를 편 뒤(착용), Meta AI 앱에서 연결 상태를 확인하세요."
        case .cameraDenied: return "안경 카메라 권한이 없어요. Meta AI 앱에서 이 앱의 카메라를 '항상 허용'으로 바꿔 주세요."
        case .cameraUnavailable: return "카메라를 붙이지 못했어요. 세션이 시작됐는지 확인하세요."
        case .captureNotStarted: return "사진 촬영을 시작하지 못했어요. 잠시 후 다시 시도하세요."
        case .timeout(let what): return "\(what) 시간이 초과됐어요. 안경을 쓰고(또는 다리를 펴고) 다시 시도하세요."
        case .sessionStopped: return "안경 세션이 끝났어요."
        case .stream(let msg): return "카메라 오류: \(msg)"
        }
    }
}

/// Meta Wearables DAT SDK 래퍼: 등록 → 세션 → 카메라 → 사진 한 장.
///
/// 배터리·발열을 아끼려고 카메라는 '사진 찍을 때만' 켜고, 찍고 나면 바로 끕니다.
/// (계속 스트리밍하면 안경이 뜨거워져 스스로 멈출 수 있어요)
@MainActor
final class GlassesController: ObservableObject {
    @Published private(set) var registrationText = "확인 중"
    @Published private(set) var isRegistered = false
    @Published private(set) var deviceCount = 0
    @Published private(set) var sessionText = "없음"
    @Published var lastError: String?

    private var session: DeviceSession?
    private var sessionStarted = false
    private let sessionTokens = ListenerTokenBag()
    private let streamTokens = ListenerTokenBag()
    private var monitorTasks: [Task<Void, Never>] = []

    func startMonitoring() {
        guard DATStatus.configured, monitorTasks.isEmpty else {
            if !DATStatus.configured { registrationText = "SDK 초기화 실패: \(DATStatus.configureError ?? "-")" }
            return
        }
        let wearables = Wearables.shared
        monitorTasks.append(Task { [weak self] in
            for await state in wearables.registrationStateStream() {
                self?.apply(state)
            }
        })
        monitorTasks.append(Task { [weak self] in
            for await ids in wearables.devicesStream() {
                self?.deviceCount = ids.count
            }
        })
    }

    private func apply(_ state: RegistrationState) {
        switch state {
        case .registered:
            registrationText = "연결됨"
            isRegistered = true
        case .registering:
            registrationText = "Meta AI 앱에서 승인 중…"
            isRegistered = false
        case .available:
            registrationText = "연결 전 (아래 버튼으로 연결)"
            isRegistered = false
        case .unavailable:
            registrationText = "사용 불가 (Meta AI 앱·개발자 모드 확인)"
            isRegistered = false
        @unknown default:
            registrationText = "알 수 없음"
            isRegistered = false
        }
    }

    /// Meta AI 앱으로 넘어가 이 앱을 승인합니다. (처음 한 번, 인터넷 필요)
    func register() async {
        guard DATStatus.configured else { return }
        do {
            try await Wearables.shared.startRegistration()
        } catch {
            lastError = "등록 실패: \(error.localizedDescription)"
        }
    }

    func unregister() async {
        guard DATStatus.configured else { return }
        do {
            try await Wearables.shared.startUnregistration()
        } catch {
            lastError = "연결 해제 실패: \(error.localizedDescription)"
        }
    }

    // MARK: - 세션

    private func ensureSession() async throws -> DeviceSession {
        guard DATStatus.configured else { throw GlassesError.sdkNotReady(DATStatus.configureError ?? "-") }
        if let session, sessionStarted { return session }

        let wearables = Wearables.shared
        let selector = AutoDeviceSelector(wearables: wearables)
        // 안경이 켜지고(다리 펴짐) 폰과 연결돼 '사용 가능한 기기'가 될 때까지 기다립니다.
        if selector.activeDevice == nil {
            sessionText = "안경 찾는 중"
            try await withTimeout(seconds: 10, what: "안경 찾기") {
                for await deviceId in selector.activeDeviceStream() where deviceId != nil { return }
                throw GlassesError.noDevice
            }
        }
        let newSession = try wearables.createSession(deviceSelector: selector)
        session = newSession
        sessionStarted = false
        sessionText = "시작 중"
        // start() 전에 구독해야 첫 상태 변화를 놓치지 않습니다.
        newSession.statePublisher.listen { [weak self] state in
            Task { @MainActor in self?.handleSessionState(state) }
        }.store(in: sessionTokens)
        newSession.errorPublisher.listen { [weak self] error in
            Task { @MainActor in self?.lastError = "세션 오류: \(error.localizedDescription)" }
        }.store(in: sessionTokens)
        // 상태 스트림도 start() 전에 만들어야 합니다. 세션이 빨리 시작되면(가상 안경·예열된 기기)
        // start() 뒤에 만든 스트림은 이미 지나간 .started를 다시 알려 주지 않아 15초를 그냥 기다립니다.
        let states = newSession.stateStream()
        try newSession.start()

        try await withTimeout(seconds: 15, what: "안경 연결") {
            if newSession.state == .started { return }
            for await state in states {
                if state == .started { return }
                if state == .stopped { throw GlassesError.sessionStopped }
            }
            throw GlassesError.sessionStopped
        }
        sessionStarted = true
        return newSession
    }

    private func handleSessionState(_ state: DeviceSessionState) {
        switch state {
        case .started: sessionText = "연결됨"; sessionStarted = true
        case .paused: sessionText = "일시정지 (안경을 다시 쓰면 재개)"
        case .stopped:
            sessionText = "종료됨"
            sessionStarted = false
            session = nil
            sessionTokens.clear()
        case .starting: sessionText = "시작 중"
        case .stopping: sessionText = "종료 중"
        case .idle: sessionText = "대기"
        @unknown default: sessionText = "알 수 없음"
        }
    }

    func endSession() {
        session?.stop()
    }

    // MARK: - 사진 한 장

    /// 카메라 권한 확인 → 세션 → 카메라 스트림 시작 → 사진 촬영(JPEG) → 카메라 정지
    func capturePhoto() async throws -> Data {
        guard DATStatus.configured else { throw GlassesError.sdkNotReady(DATStatus.configureError ?? "-") }
        let wearables = Wearables.shared
        if try await wearables.checkPermissionStatus(.camera) != .granted {
            // Meta AI 앱으로 잠깐 넘어가 권한을 받습니다 ('항상 허용' 권장: 오프라인에서도 다시 묻지 않음)
            guard try await wearables.requestPermission(.camera) == .granted else { throw GlassesError.cameraDenied }
        }
        let activeSession = try await ensureSession()

        // 해상도·프레임을 낮게 잡을수록 블루투스 압축이 줄어 한 장의 화질은 오히려 좋아집니다.
        let config = StreamConfiguration(videoCodec: .raw, resolution: .medium, frameRate: 15)
        guard let camera = try activeSession.addCamera(config: config) else { throw GlassesError.cameraUnavailable }
        let stream = camera.stream
        defer {
            streamTokens.clear()
            camera.stop()  // 스트림까지 함께 정리됨
        }

        // 1) 스트리밍 상태가 될 때까지 기다리기
        try await withTimeout(seconds: 12, what: "카메라 시작") {
            try await waitFor { (once: OneShot<Void>) in
                stream.statePublisher.listen { state in
                    if state == .streaming { once.resume(returning: ()) }
                }.store(in: self.streamTokens)
                stream.errorPublisher.listen { error in
                    once.resume(throwing: GlassesError.stream(error.localizedDescription))
                }.store(in: self.streamTokens)
                stream.start()
            }
        }

        // 2) 사진 찍기
        return try await withTimeout(seconds: 10, what: "사진 전송") {
            try await waitFor { (once: OneShot<Data>) in
                stream.photoDataPublisher.listen { photo in
                    once.resume(returning: photo.data)
                }.store(in: self.streamTokens)
                if !stream.capturePhoto(format: .jpeg) {
                    once.resume(throwing: GlassesError.captureNotStarted)
                }
            }
        }
    }
}

/// continuation을 정확히 한 번만 resume 하도록 보장 (여러 콜백·취소가 경쟁할 때)
final class OneShot<T>: @unchecked Sendable {
    private var cont: CheckedContinuation<T, Error>?
    private var finished = false
    private var earlyError: Error?
    private let lock = NSLock()

    func attach(_ c: CheckedContinuation<T, Error>) {
        lock.lock()
        if finished {
            let error = earlyError ?? CancellationError()
            lock.unlock()
            c.resume(throwing: error)
            return
        }
        cont = c
        lock.unlock()
    }

    func resume(returning value: T) {
        lock.lock()
        guard !finished else { lock.unlock(); return }
        finished = true
        let c = cont
        cont = nil
        lock.unlock()
        c?.resume(returning: value)
    }

    func resume(throwing error: Error) {
        lock.lock()
        guard !finished else { lock.unlock(); return }
        finished = true
        earlyError = error
        let c = cont
        cont = nil
        lock.unlock()
        c?.resume(throwing: error)
    }
}

/// 콜백 기반 API를 async로 바꾸는 도우미. 작업이 취소되면(시간 초과 등) 기다림도 함께 풀립니다.
@MainActor
func waitFor<T>(_ setup: @escaping @MainActor (OneShot<T>) -> Void) async throws -> T {
    let once = OneShot<T>()
    return try await withTaskCancellationHandler {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<T, Error>) in
            once.attach(cont)
            setup(once)
        }
    } onCancel: {
        once.resume(throwing: CancellationError())
    }
}

/// 제한 시간 안에 끝나지 않으면 GlassesError.timeout (본문 작업은 취소됨)
func withTimeout<T>(seconds: Double, what: String, _ body: @escaping @MainActor () async throws -> T) async throws -> T {
    try await withThrowingTaskGroup(of: T.self) { group in
        group.addTask { try await body() }
        group.addTask {
            try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            throw GlassesError.timeout(what)
        }
        defer { group.cancelAll() }
        guard let result = try await group.next() else { throw GlassesError.timeout(what) }
        return result
    }
}
