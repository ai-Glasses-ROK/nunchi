import Foundation

// 허브(nunchi serve)와 통신합니다. 응답은 SSE(text/event-stream)로 조금씩 받습니다.

struct HubSource: Decodable, Hashable {
    let n: Int
    let label: String
    let text: String
}

struct HubDone: Decodable {
    let ok: Bool
    let answer: String
    let speech: String
    let sources: [HubSource]?
    let timings: [String: Double]?
}

struct HubHealth: Decodable {
    struct Index: Decodable {
        let documents: Int
        let chunks: Int
    }

    let ok: Bool
    let models: [String: String]
    let stt: String
    let tts: String
    let index: Index
}

enum HubEvent {
    case transcript(String)
    case caption(String)
    case retrieval([HubSource])
    case delta(String)
    case sentence(String)
    case audio(Data)
    case error(stage: String, message: String)
    case done(HubDone)
}

enum HubError: LocalizedError {
    case badURL(String)
    case http(Int, String)

    var errorDescription: String? {
        switch self {
        case .badURL(let s): return "허브 주소가 올바르지 않아요: \(s)"
        case .http(401, _): return "토큰이 맞지 않아요 (설정에서 허브 토큰 확인)"
        case .http(let code, let body): return "허브 오류 \(code): \(body.prefix(200))"
        }
    }
}

final class HubClient {
    let baseURL: URL
    private let token: String
    private let session: URLSession

    init(base: String, token: String) throws {
        var trimmed = base.trimmingCharacters(in: .whitespacesAndNewlines)
        while trimmed.hasSuffix("/") { trimmed.removeLast() }
        if !trimmed.hasPrefix("http://") && !trimmed.hasPrefix("https://") { trimmed = "http://" + trimmed }
        guard let url = URL(string: trimmed), url.host != nil else { throw HubError.badURL(base) }
        baseURL = url
        self.token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 120
        config.waitsForConnectivity = false
        session = URLSession(configuration: config)
    }

    // MARK: - 요청들

    func health() async throws -> HubHealth {
        var req = request("v1/health")
        req.timeoutInterval = 5
        let (data, resp) = try await session.data(for: req)
        try check(resp, data)
        return try JSONDecoder().decode(HubHealth.self, from: data)
    }

    /// 드론 비행컨트롤러의 실시간 상태. context는 프롬프트의 [실시간 기체 상태] 칸에, hint(PreArm 메시지 등)는 검색어에 붙습니다.
    struct Live {
        var context = ""
        var hint = ""
    }

    func ask(_ question: String, sessionId: String, speak: Bool, live: Live = Live(), onEvent: @escaping @MainActor (HubEvent) -> Void) async throws {
        var req = request("v1/ask", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = ["question": question, "session_id": sessionId, "stream": true, "speak": speak]
        if !live.context.isEmpty { body["live_context"] = String(live.context.prefix(4000)) }
        if !live.hint.isEmpty { body["live_hint"] = String(live.hint.prefix(500)) }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        try await stream(req, onEvent: onEvent)
    }

    func voice(wav: Data, sessionId: String, speak: Bool, onEvent: @escaping @MainActor (HubEvent) -> Void) async throws {
        var form = MultipartForm()
        form.addFile(name: "file", filename: "voice.wav", mime: "audio/wav", data: wav)
        form.addField("session_id", sessionId)
        form.addField("stream", "true")
        form.addField("speak", speak ? "true" : "false")
        try await stream(form.request(base: request("v1/voice", method: "POST")), onEvent: onEvent)
    }

    func vision(jpeg: Data, question: String, sessionId: String, speak: Bool, source: String = "glasses", live: Live = Live(),
                onEvent: @escaping @MainActor (HubEvent) -> Void) async throws {
        var form = MultipartForm()
        form.addFile(name: "image", filename: "photo.jpg", mime: "image/jpeg", data: jpeg)
        form.addField("question", question)
        form.addField("session_id", sessionId)
        form.addField("stream", "true")
        form.addField("speak", speak ? "true" : "false")
        form.addField("image_source", source) // glasses(안경 카메라) | drone(드론 카메라)
        if !live.context.isEmpty { form.addField("live_context", String(live.context.prefix(4000))) }
        if !live.hint.isEmpty { form.addField("live_hint", String(live.hint.prefix(500))) }
        try await stream(form.request(base: request("v1/vision", method: "POST")), onEvent: onEvent)
    }

    /// 단독 모드용 지식베이스(JSON) 내려받기
    func exportKnowledgeBase() async throws -> Data {
        let req = request("v1/kb/export")
        let (data, resp) = try await session.data(for: req)
        try check(resp, data)
        return data
    }

    // MARK: - 내부

    private func request(_ path: String, method: String = "GET") -> URLRequest {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = method
        if !token.isEmpty { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        return req
    }

    private func check(_ resp: URLResponse, _ data: Data) throws {
        guard let http = resp as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            throw HubError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
    }

    /// SSE 읽기. 서버는 항상 `event:` 줄 다음에 `data:` 한 줄을 보내므로 data 줄마다 이벤트를 처리합니다.
    private func stream(_ req: URLRequest, onEvent: @escaping @MainActor (HubEvent) -> Void) async throws {
        let (bytes, resp) = try await session.bytes(for: req)
        if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            var body = ""
            for try await line in bytes.lines { body += line }
            throw HubError.http(http.statusCode, body)
        }
        var eventType = "message"
        for try await line in bytes.lines {
            if line.hasPrefix("event: ") {
                eventType = String(line.dropFirst(7))
            } else if line.hasPrefix("data: ") {
                if let event = Self.parse(type: eventType, json: Data(line.dropFirst(6).utf8)) {
                    await onEvent(event)
                }
            }
        }
    }

    private static func parse(type: String, json: Data) -> HubEvent? {
        guard let obj = (try? JSONSerialization.jsonObject(with: json)) as? [String: Any] else { return nil }
        switch type {
        case "transcript":
            return .transcript(obj["text"] as? String ?? "")
        case "caption":
            return .caption(obj["text"] as? String ?? "")
        case "retrieval":
            let raw = (try? JSONSerialization.data(withJSONObject: obj["sources"] ?? [])) ?? Data("[]".utf8)
            return .retrieval((try? JSONDecoder().decode([HubSource].self, from: raw)) ?? [])
        case "delta":
            return .delta(obj["text"] as? String ?? "")
        case "sentence":
            return .sentence(obj["text"] as? String ?? "")
        case "audio":
            guard let b64 = obj["wav_b64"] as? String, let data = Data(base64Encoded: b64) else { return nil }
            return .audio(data)
        case "error":
            return .error(stage: obj["stage"] as? String ?? "?", message: obj["message"] as? String ?? "")
        case "done":
            return (try? JSONDecoder().decode(HubDone.self, from: json)).map { .done($0) }
        default:
            return nil
        }
    }
}

/// multipart/form-data 만들기 (파일 업로드용)
struct MultipartForm {
    private let boundary = "Nunchi-\(UUID().uuidString)"
    private var body = Data()

    mutating func addField(_ name: String, _ value: String) {
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8))
    }

    mutating func addFile(name: String, filename: String, mime: String, data: Data) {
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\nContent-Type: \(mime)\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n".utf8))
    }

    func request(base: URLRequest) -> URLRequest {
        var req = base
        var payload = body
        payload.append(Data("--\(boundary)--\r\n".utf8))
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = payload
        return req
    }
}
