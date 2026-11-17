import Foundation

/// 폰 단독 모드용 지식베이스: 허브에서 내보낸 kb.json 을 읽어 '글자 2-gram BM25'로 검색합니다.
/// 허브(Python)의 nunchi/rag/bm25.py 와 같은 규칙이라 검색 결과가 거의 같습니다.
struct KBChunk: Codable, Hashable {
    let id: Int
    let title: String
    let heading: String
    let text: String
    let source: String

    var label: String { [title, heading].filter { !$0.isEmpty }.joined(separator: " > ") }
}

struct KBFile: Codable {
    let format: String
    let version: Int
    let tokenizer: String
    let chunks: [KBChunk]
}

enum KBError: LocalizedError {
    case wrongFormat

    var errorDescription: String? { "nunchi 지식베이스 파일(kb.json)이 아니에요." }
}

final class LocalKnowledgeBase {
    let chunks: [KBChunk]
    private var postings: [String: [(Int, Double)]] = [:]
    private var docLen: [Double] = []
    private var avgdl: Double = 1
    private var idf: [String: Double] = [:]
    private let k1 = 1.5
    private let b = 0.75

    static var savedURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("kb.json")
    }

    init(data: Data) throws {
        let file = try JSONDecoder().decode(KBFile.self, from: data)
        guard file.format == "nunchi-kb" else { throw KBError.wrongFormat }
        chunks = file.chunks
        build()
    }

    static func loadSaved() -> LocalKnowledgeBase? {
        guard let data = try? Data(contentsOf: savedURL) else { return nil }
        return try? LocalKnowledgeBase(data: data)
    }

    static func save(_ data: Data) throws -> LocalKnowledgeBase {
        let kb = try LocalKnowledgeBase(data: data)  // 형식 검사 먼저
        try data.write(to: savedURL, options: .atomic)
        return kb
    }

    private func build() {
        var counts: [String: Int] = [:]  // 토큰별 문서 빈도
        docLen = []
        for (i, chunk) in chunks.enumerated() {
            let tokens = Self.tokenize("\(chunk.label)\n\(chunk.text)")
            docLen.append(Double(tokens.count))
            var tf: [String: Double] = [:]
            for t in tokens { tf[t, default: 0] += 1 }
            for (t, f) in tf {
                postings[t, default: []].append((i, f))
                counts[t, default: 0] += 1
            }
        }
        avgdl = max(1, docLen.reduce(0, +) / Double(max(1, docLen.count)))
        let n = Double(chunks.count)
        for (t, df) in counts {
            idf[t] = log(1 + (n - Double(df) + 0.5) / (Double(df) + 0.5))
        }
    }

    func search(_ query: String, k: Int = 3) -> [KBChunk] {
        var scores = [Double](repeating: 0, count: chunks.count)
        for t in Set(Self.tokenizeQuery(query)) {
            guard let plist = postings[t], let w = idf[t] else { continue }
            for (i, tf) in plist {
                let norm = k1 * (1 - b + b * docLen[i] / avgdl)
                scores[i] += w * tf * (k1 + 1) / (tf + norm)
            }
        }
        return scores.enumerated()
            .filter { $0.element > 0 }
            .sorted { $0.element > $1.element }
            .prefix(k)
            .map { chunks[$0.offset] }
    }

    // MARK: - 토크나이저 (Python 구현과 동일한 규칙)

    private static let runRegex = try! NSRegularExpression(
        pattern: "[\u{AC00}-\u{D7A3}\u{3131}-\u{318E}]+|[\u{4E00}-\u{9FFF}]+|[\u{3040}-\u{30FF}]+|[a-z0-9]+"
    )

    static let queryStop: Set<String> = [
        "알려", "려줘", "려주", "주세", "세요", "해줘", "해주", "줄래", "주라", "뭐야", "뭐지", "뭔가",
        "무엇", "엇인", "인가", "인지", "어떻", "떻게", "어떤", "하나", "나요", "까요", "할까", "는지",
        "있어", "있나", "없어", "니다", "습니", "합니", "입니", "에요", "예요", "어요", "아요", "는데",
        "건가", "거야", "말해", "해봐", "궁금", "금해", "좀", "그럼", "그거", "이거", "저거",
    ]

    static func tokenize(_ text: String) -> [String] {
        let normalized = text.precomposedStringWithCompatibilityMapping.lowercased()
        let ns = normalized as NSString
        var tokens: [String] = []
        for match in runRegex.matches(in: normalized, range: NSRange(location: 0, length: ns.length)) {
            let run = Array(ns.substring(with: match.range))
            guard let first = run.first else { continue }
            if isCJK(first) {
                if run.count == 1 {
                    tokens.append(String(run))
                } else {
                    for i in 0..<(run.count - 1) { tokens.append(String(run[i...i + 1])) }
                }
            } else {
                tokens.append(String(run))
            }
        }
        return tokens
    }

    static func tokenizeQuery(_ text: String) -> [String] {
        let all = tokenize(text)
        let filtered = all.filter { !queryStop.contains($0) }
        return filtered.isEmpty ? all : filtered
    }

    private static func isCJK(_ c: Character) -> Bool {
        guard let v = c.unicodeScalars.first?.value else { return false }
        return (0xAC00...0xD7A3).contains(v) || (0x3131...0x318E).contains(v)
            || (0x4E00...0x9FFF).contains(v) || (0x3040...0x30FF).contains(v)
    }
}
