import Foundation
import Speech
#if canImport(FoundationModels)
import FoundationModels
#endif

enum OnDeviceError: LocalizedError {
    case speechDenied
    case recognizerUnavailable
    case noOnDeviceRecognition

    var errorDescription: String? {
        switch self {
        case .speechDenied: return "음성 인식 권한이 없어요. 설정 > 개인정보 보호 > 음성 인식에서 허용해 주세요."
        case .recognizerUnavailable: return "한국어 음성 인식기를 쓸 수 없어요."
        case .noOnDeviceRecognition: return "이 기기는 한국어 '기기 내' 음성 인식을 지원하지 않아요. 설정 > 일반 > 키보드 > 받아쓰기에서 한국어를 켜고, 인터넷이 될 때 한 번 받아쓰기를 써서 언어 데이터를 내려받아 보세요."
        }
    }
}

/// 폰 단독 모드: 음성 인식 → 검색(2-gram BM25) → 답변 생성을 전부 기기 안에서 처리합니다.
///
/// - 음성 인식: Apple Speech 프레임워크, `requiresOnDeviceRecognition = true` (음성이 기기 밖으로 나가지 않음)
/// - 답변: iOS 26+ & Apple Intelligence 기기(iPhone 15 Pro 이상)는 Foundation Models(기기 내 LLM)
///   그 외 기기는 '검색된 문장 그대로 읽어 주기'(추출형 답변)로 대신합니다. → 어떤 아이폰에서도 오프라인 동작
final class OnDeviceAssistant {
    var knowledgeBase: LocalKnowledgeBase? = LocalKnowledgeBase.loadSaved()

    static let instructions = """
    너는 스마트 안경 속 음성 비서야. 답은 소리로 읽힌다. 한국어로 1~3문장, 120자 안쪽으로 짧게 답한다.
    마크다운과 목록을 쓰지 않는다. [참고 문서]에 있는 내용만 근거로 답하고, 없으면 "문서에는 없지만"이라고 말한 뒤 짧게 답한다.
    질문은 음성 인식 결과라 발음이 비슷한 단어가 틀렸을 수 있으니, 문서의 비슷한 단어로 이해한다.
    """

    /// 기기 내 LLM 사용 가능 여부 (설정 화면 표시용)
    static var llmStatus: String {
        #if canImport(FoundationModels)
        if #available(iOS 26.0, *) {
            switch SystemLanguageModel.default.availability {
            case .available:
                return "사용 가능 (Apple Foundation Models)"
            case .unavailable(let reason):
                return "사용 불가: \(reason)"
            @unknown default:
                return "알 수 없음"
            }
        }
        #endif
        return "iOS 26 이상 + Apple Intelligence 기기에서만 사용 가능 (지금은 추출형 답변으로 대신)"
    }

    static func requestSpeechPermission() async -> Bool {
        await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { status in cont.resume(returning: status == .authorized) }
        }
    }

    func transcribe(wav: Data) async throws -> String {
        guard await Self.requestSpeechPermission() else { throw OnDeviceError.speechDenied }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "ko-KR")), recognizer.isAvailable else {
            throw OnDeviceError.recognizerUnavailable
        }
        guard recognizer.supportsOnDeviceRecognition else { throw OnDeviceError.noOnDeviceRecognition }

        let url = FileManager.default.temporaryDirectory.appendingPathComponent("question-\(UUID().uuidString).wav")
        try wav.write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let request = SFSpeechURLRecognitionRequest(url: url)
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = false

        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<String, Error>) in
            let once = OneShot<String>()
            once.attach(cont)
            recognizer.recognitionTask(with: request) { result, error in
                if let error {
                    once.resume(throwing: error)
                } else if let result, result.isFinal {
                    once.resume(returning: result.bestTranscription.formattedString)
                }
            }
        }
    }

    func answer(_ question: String) async -> (text: String, sources: [KBChunk]) {
        let hits = knowledgeBase?.search(question, k: 3) ?? []
        let context = hits.enumerated()
            .map { "[\($0.offset + 1)] \($0.element.label)\n\($0.element.text)" }
            .joined(separator: "\n\n")

        #if canImport(FoundationModels)
        if #available(iOS 26.0, *), case .available = SystemLanguageModel.default.availability {
            let session = LanguageModelSession(instructions: Self.instructions)
            let prompt = "[참고 문서]\n\(context.isEmpty ? "(관련 문서를 찾지 못함)" : context)\n\n[질문]\n\(question)"
            if let response = try? await session.respond(to: prompt) {
                return (response.content, hits)
            }
        }
        #endif

        // 추출형 답변: 가장 관련 있는 조각의 앞부분을 그대로 읽어 줌 (LLM 없이도 동작)
        if let top = hits.first {
            return ("문서에서 찾은 내용이에요. " + Self.firstSentences(top.text, count: 2), hits)
        }
        if knowledgeBase == nil {
            return ("지식베이스가 없어요. 설정에서 허브의 지식베이스를 받아 오거나 kb.json 파일을 불러와 주세요.", [])
        }
        return ("문서에서 관련 내용을 찾지 못했어요.", [])
    }

    static func firstSentences(_ text: String, count: Int) -> String {
        let cleaned = text.replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "- ", with: "")
            .replacingOccurrences(of: "#", with: "")
        var sentences: [String] = []
        var current = ""
        for ch in cleaned {
            current.append(ch)
            if ".!?。".contains(ch) {
                sentences.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
                if sentences.count == count { break }
            }
        }
        if sentences.isEmpty { return String(cleaned.prefix(120)) }
        return sentences.joined(separator: " ")
    }
}
