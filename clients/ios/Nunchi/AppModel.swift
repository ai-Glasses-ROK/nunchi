import SwiftUI
import UIKit

/// 화면과 기능을 잇는 중심 객체: 녹음 → (허브 or 기기 내) 처리 → 화면 표시 + 소리 출력
@MainActor
final class AppModel: ObservableObject {
    enum Role { case user, assistant, notice }

    struct Message: Identifiable {
        let id = UUID()
        let role: Role
        var text: String
        var image: UIImage?
        var sources: [String] = []
        var meta = ""
    }

    @Published var messages: [Message] = [
        Message(role: .notice, text: "마이크 버튼을 누르고 말한 뒤 다시 누르세요. 카메라 버튼은 안경으로 사진을 찍어 함께 물어봅니다."),
    ]
    @Published private(set) var isRecording = false
    @Published private(set) var isBusy = false
    @Published private(set) var hubStatus = "확인 전"
    @Published private(set) var kbStatus = ""

    let glasses = GlassesController()
    private let recorder = VoiceRecorder()
    private let speaker = SpeechOutput()
    private let onDevice = OnDeviceAssistant()
    private var sessionId = String(UUID().uuidString.prefix(12))

    init() {
        glasses.startMonitoring()
        refreshKBStatus()
        #if DEBUG
        // 연구용 자동 점검: 실행 인자 `-NunchiSelfTest photo` 로 켜면 가상 안경 사진 → 허브 비전까지 자동으로 돌려 봅니다.
        if UserDefaults.standard.string(forKey: "NunchiSelfTest") == "photo" {
            Task { await self.runPhotoSelfTest() }
        }
        #endif
    }

    #if DEBUG
    private func runPhotoSelfTest() async {
        debugLog("selftest: start")
        VirtualGlasses.shared.turnOn()
        try? await Task.sleep(nanoseconds: 1_500_000_000)
        await askWithGlassesPhoto(question: "이 계기판에 뜬 코드는 무슨 뜻이고 어떻게 해야 해?")
        debugLog("selftest: done · \(messages.map { "\($0.role): \($0.text)" }.joined(separator: " | "))")
    }
    #endif

    var settings: CurrentSettings { .load() }

    // MARK: - 녹음

    func toggleRecording() async {
        if isRecording {
            await finishRecording()
        } else {
            speaker.stopAll()
            do {
                try recorder.start(source: settings.mic)
                isRecording = true
            } catch {
                notice("녹음을 시작하지 못했어요: \(error.localizedDescription)")
            }
        }
    }

    private func finishRecording() async {
        let seconds = recorder.recordedSeconds
        let route = recorder.routeDescription
        let wav = recorder.stop()
        isRecording = false
        speaker.prepareForPlayback()
        guard seconds >= 0.4 else {
            notice("너무 짧아요. 버튼을 누르고 말한 다음 다시 눌러 주세요.")
            return
        }
        let s = settings
        let userIndex = append(Message(role: .user, text: "🎙️ 인식 중…", meta: route))
        if s.mode == .hub {
            await runHub(userIndex: userIndex) { client, onEvent in
                try await client.voice(wav: wav, sessionId: self.sessionId, speak: s.voice == .hub, onEvent: onEvent)
            }
        } else {
            await runOnDevice(wav: wav, userIndex: userIndex)
        }
    }

    // MARK: - 글자 / 사진 질문

    func ask(_ text: String) async {
        let q = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return }
        speaker.stopAll()
        speaker.prepareForPlayback()
        let userIndex = append(Message(role: .user, text: q))
        let s = settings
        if s.mode == .hub {
            await runHub(userIndex: userIndex) { client, onEvent in
                try await client.ask(q, sessionId: self.sessionId, speak: s.voice == .hub, onEvent: onEvent)
            }
        } else {
            await answerOnDevice(q)
        }
    }

    func askWithGlassesPhoto(question: String) async {
        let s = settings
        guard s.mode == .hub else {
            notice("사진 질문은 허브 모드에서만 돼요 (기기 내 LLM은 아직 글자만 지원).")
            return
        }
        speaker.stopAll()
        isBusy = true
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "지금 보이는 게 뭐야? 짧게 설명해 줘." : question
        do {
            let jpeg = try await glasses.capturePhoto()
            debugLog("photo captured: \(jpeg.count) bytes")
            isBusy = false
            speaker.prepareForPlayback()
            let userIndex = append(Message(role: .user, text: q, image: UIImage(data: jpeg)))
            await runHub(userIndex: userIndex) { client, onEvent in
                try await client.vision(jpeg: jpeg, question: q, sessionId: self.sessionId, speak: s.voice == .hub, onEvent: onEvent)
            }
        } catch {
            isBusy = false
            notice("사진을 찍지 못했어요: \(error.localizedDescription)")
        }
    }

    func resetConversation() {
        sessionId = String(UUID().uuidString.prefix(12))
        speaker.stopAll()
        messages = [Message(role: .notice, text: "대화를 새로 시작했어요.")]
    }

    // MARK: - 허브 모드

    private func runHub(userIndex: Int, _ call: @escaping (HubClient, @escaping @MainActor (HubEvent) -> Void) async throws -> Void) async {
        let s = settings
        isBusy = true
        defer { isBusy = false }
        let answerIndex = append(Message(role: .assistant, text: ""))
        var answer = ""
        do {
            let client = try HubClient(base: s.hubURL, token: s.hubToken)
            try await call(client) { [weak self] event in
                guard let self else { return }
                switch event {
                case .transcript(let text):
                    self.messages[userIndex].text = text.isEmpty ? "🎙️ (알아듣지 못함)" : "🎙️ \(text)"
                case .retrieval(let sources):
                    self.messages[answerIndex].sources = sources.map { "[\($0.n)] \($0.label)" }
                case .delta(let text):
                    answer += text
                    self.messages[answerIndex].text = answer
                case .sentence(let text):
                    if s.voice == .phone { self.speaker.speak(text) }
                case .audio(let wav):
                    if s.voice == .hub { self.speaker.play(wav: wav) }
                case .error(let stage, let message):
                    self.messages[answerIndex].meta += "⚠️ \(stage): \(message)\n"
                case .done(let done):
                    debugLog("answer: \(done.answer)")
                    if !done.ok || answer.isEmpty {
                        self.messages[answerIndex].text = done.answer
                        if s.voice != .off { self.speaker.speak(done.speech) }
                    }
                    if let t = done.timings { self.messages[answerIndex].meta += Self.format(timings: t) }
                }
            }
            hubStatus = "연결됨"
        } catch {
            messages[answerIndex].text = "허브에 연결하지 못했어요: \(error.localizedDescription)"
            hubStatus = "연결 실패"
            if s.voice != .off { speaker.speak("허브에 연결하지 못했어요.") }
        }
    }

    func checkHub() async {
        do {
            let client = try HubClient(base: settings.hubURL, token: settings.hubToken)
            let h = try await client.health()
            hubStatus = "연결됨 · \(h.models["llm"] ?? "?") · 문서 \(h.index.documents)개"
        } catch {
            hubStatus = "연결 실패: \(error.localizedDescription)"
        }
    }

    // MARK: - 단독 모드

    private func runOnDevice(wav: Data, userIndex: Int) async {
        isBusy = true
        defer { isBusy = false }
        do {
            let started = Date()
            let text = try await onDevice.transcribe(wav: wav)
            messages[userIndex].text = text.isEmpty ? "🎙️ (알아듣지 못함)" : "🎙️ \(text)"
            messages[userIndex].meta = "기기 내 인식 \(Int(Date().timeIntervalSince(started) * 1000))ms"
            guard !text.isEmpty else {
                speaker.speak("잘 못 들었어요. 다시 말씀해 주세요.")
                return
            }
            await answerOnDevice(text)
        } catch {
            messages[userIndex].text = "🎙️ 인식 실패"
            notice(error.localizedDescription)
        }
    }

    private func answerOnDevice(_ question: String) async {
        isBusy = true
        defer { isBusy = false }
        let started = Date()
        let result = await onDevice.answer(question)
        var msg = Message(role: .assistant, text: result.text)
        msg.sources = result.sources.enumerated().map { "[\($0.offset + 1)] \($0.element.label)" }
        msg.meta = "기기 내 처리 \(Int(Date().timeIntervalSince(started) * 1000))ms"
        append(msg)
        if settings.voice != .off { speaker.speak(result.text) }
    }

    func downloadKnowledgeBase() async {
        do {
            let client = try HubClient(base: settings.hubURL, token: settings.hubToken)
            let data = try await client.exportKnowledgeBase()
            onDevice.knowledgeBase = try LocalKnowledgeBase.save(data)
            refreshKBStatus()
            notice("허브의 지식베이스를 폰에 저장했어요. 이제 네트워크 없이도 검색할 수 있어요.")
        } catch {
            notice("지식베이스를 받지 못했어요: \(error.localizedDescription)")
        }
    }

    func importKnowledgeBase(from url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            onDevice.knowledgeBase = try LocalKnowledgeBase.save(try Data(contentsOf: url))
            refreshKBStatus()
            notice("kb.json 을 불러왔어요.")
        } catch {
            notice("불러오기 실패: \(error.localizedDescription)")
        }
    }

    private func refreshKBStatus() {
        if let kb = onDevice.knowledgeBase {
            kbStatus = "조각 \(kb.chunks.count)개 저장됨"
        } else {
            kbStatus = "없음"
        }
    }

    var llmStatus: String { OnDeviceAssistant.llmStatus }

    // MARK: - 도우미

    @discardableResult
    private func append(_ m: Message) -> Int {
        messages.append(m)
        return messages.count - 1
    }

    private func notice(_ text: String) {
        append(Message(role: .notice, text: text))
    }

    private static func format(timings: [String: Double]) -> String {
        let names: [(String, String)] = [("stt_ms", "인식"), ("retrieval_ms", "검색"), ("first_token_ms", "첫 글자"), ("total_ms", "전체")]
        return "⏱ " + names.compactMap { key, name in timings[key].map { "\(name) \(Int($0))ms" } }.joined(separator: " · ")
    }
}
