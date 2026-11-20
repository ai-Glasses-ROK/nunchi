package io.github.aiglassesrok.nunchi.rag

import java.text.Normalizer
import java.util.Locale
import kotlin.math.ln

/**
 * 폰 단독 모드의 검색(RAG의 R) — 허브 `nunchi/rag/bm25.py`·`chunker.py`와 같은 규칙의 Kotlin 이식.
 * 한국어는 형태소 분석기 없이 '글자 2-gram'으로 자르고 BM25로 순위를 매깁니다(iOS LocalKnowledgeBase.swift와도 같음).
 * 그래서 허브에서 찾던 조각을 폰에서도 거의 그대로 찾습니다 (테스트: 같은 질문 → 같은 1위).
 */
data class KbChunk(val title: String, val heading: String, val text: String, val source: String) {
    val label: String get() = listOf(title, heading).filter { it.isNotEmpty() }.joinToString(" > ")
    val searchText: String get() = if (label.isEmpty()) text else "$label\n$text"
}

object KoreanTokenizer {
    private val RUN = Regex("[\\uAC00-\\uD7A3\\u3131-\\u318E]+|[\\u4E00-\\u9FFF]+|[\\u3040-\\u30FF]+|[a-z0-9]+")

    /** 질문에만 자주 나오고 내용과는 상관없는 2-gram (허브 QUERY_STOP과 같음) */
    val QUERY_STOP = setOf(
        "알려", "려줘", "려주", "주세", "세요", "해줘", "해주", "줄래", "주라", "뭐야", "뭐지", "뭔가",
        "무엇", "엇인", "인가", "인지", "어떻", "떻게", "어떤", "하나", "나요", "까요", "할까", "는지",
        "있어", "있나", "없어", "니다", "습니", "합니", "입니", "에요", "예요", "어요", "아요", "는데",
        "건가", "거야", "말해", "해봐", "궁금", "금해", "좀", "그럼", "그거", "이거", "저거",
    )

    fun tokenize(text: String): List<String> {
        val norm = Normalizer.normalize(text, Normalizer.Form.NFKC).lowercase(Locale.ROOT)
        val out = ArrayList<String>()
        for (m in RUN.findAll(norm)) {
            val run = m.value
            if (isCjk(run[0])) {
                if (run.length == 1) out += run else for (i in 0 until run.length - 1) out += run.substring(i, i + 2)
            } else {
                out += run
            }
        }
        return out
    }

    fun tokenizeQuery(text: String): List<String> {
        val all = tokenize(text)
        val kept = all.filter { it !in QUERY_STOP }
        return kept.ifEmpty { all }
    }

    private fun isCjk(c: Char): Boolean {
        val o = c.code
        return o in 0xAC00..0xD7A3 || o in 0x3131..0x318E || o in 0x4E00..0x9FFF || o in 0x3040..0x30FF
    }
}

/** Okapi BM25 (k1=1.5, b=0.75) — 허브와 같은 값 */
class Bm25(docs: List<String>, private val k1: Double = 1.5, private val b: Double = 0.75) {
    private val n = docs.size
    private val docLen = DoubleArray(n)
    private val postings = HashMap<String, MutableList<Pair<Int, Int>>>()
    private val idf = HashMap<String, Double>()
    private val avgdl: Double

    init {
        docs.forEachIndexed { i, d ->
            val counts = KoreanTokenizer.tokenize(d).groupingBy { it }.eachCount()
            docLen[i] = counts.values.sum().toDouble()
            counts.forEach { (tok, tf) -> postings.getOrPut(tok) { ArrayList() } += i to tf }
        }
        avgdl = if (n > 0) docLen.average() else 0.0
        postings.forEach { (tok, p) -> idf[tok] = ln(1 + (n - p.size + 0.5) / (p.size + 0.5)) }
    }

    fun scores(query: String): DoubleArray {
        val s = DoubleArray(n)
        if (n == 0) return s
        for (tok in KoreanTokenizer.tokenizeQuery(query).toSet()) {
            val plist = postings[tok] ?: continue
            val w = idf.getValue(tok)
            for ((i, tf) in plist) {
                val norm = k1 * (1 - b + b * docLen[i] / maxOf(avgdl, 1e-6))
                s[i] += w * tf * (k1 + 1) / (tf + norm)
            }
        }
        return s
    }

    fun search(query: String, k: Int): List<Pair<Int, Double>> =
        scores(query).withIndex().filter { it.value > 0 }.sortedByDescending { it.value }.take(k).map { it.index to it.value }
}

/** 마크다운을 '제목 경로 + 600자 안팎' 조각으로 (허브 chunker.py와 같은 원칙) */
object MarkdownChunker {
    private val HEADING = Regex("^(#{1,6})\\s+(.+?)\\s*#*\\s*$")
    private val SENT_END = Regex("(?<=[.!?…。！？])[\"'”’)\\]]*\\s+|\\n+")
    private val TITLE = Regex("^#\\s+(.+)$", RegexOption.MULTILINE)

    fun title(text: String, fileName: String): String =
        TITLE.find(text)?.groupValues?.get(1)?.trim()
            ?: fileName.substringBeforeLast('.').replace('_', ' ').replace('-', ' ').trim()

    fun splitSentences(text: String): List<String> = SENT_END.split(text).map { it.trim() }.filter { it.isNotEmpty() }

    fun normalize(text: String): String {
        val t = Normalizer.normalize(text, Normalizer.Form.NFC).replace("\r\n", "\n").replace('\r', '\n')
            .replace(Regex("[ \\t\\u00A0\\u3000]+"), " ")
        return t.lines().joinToString("\n") { it.trim() }.replace(Regex("\\n{3,}"), "\n\n").trim()
    }

    private fun blocks(text: String): List<Pair<String, String>> {
        val path = ArrayList<Pair<Int, String>>()
        val out = ArrayList<Pair<String, String>>()
        val para = ArrayList<String>()
        var inCode = false
        fun flush() {
            if (para.isNotEmpty()) {
                val body = para.joinToString("\n").trim()
                if (body.isNotEmpty()) out += path.joinToString(" > ") { it.second } to body
                para.clear()
            }
        }
        for (line in text.split("\n")) {
            if (line.trim().startsWith("```")) {
                inCode = !inCode
                para += line
                continue
            }
            val m = if (inCode) null else HEADING.matchEntire(line)
            if (m != null) {
                flush()
                val level = m.groupValues[1].length
                while (path.isNotEmpty() && path.last().first >= level) path.removeAt(path.lastIndex)
                path += level to m.groupValues[2].trim()
                continue
            }
            if (line.isBlank() && !inCode) {
                flush()
                continue
            }
            para += line
        }
        flush()
        return out
    }

    fun chunk(text: String, title: String, source: String, chunkChars: Int = 600, overlap: Int = 120): List<KbChunk> {
        val units = ArrayList<Pair<String, String>>()
        for ((h, block) in blocks(text)) {
            var heading = h
            if (title.isNotEmpty() && (heading == title || heading.startsWith("$title > "))) heading = heading.removePrefix(title).trimStart(' ', '>')
            if (block.length <= chunkChars) units += heading to block else splitBlock(block, chunkChars).forEach { units += heading to it }
        }
        val chunks = ArrayList<KbChunk>()
        var curHead: String? = null
        val cur = ArrayList<String>()
        var curLen = 0
        fun emit() {
            val body = cur.joinToString("\n\n").trim()
            if (body.isNotEmpty()) chunks += KbChunk(title, curHead ?: "", body, source)
            cur.clear()
            curLen = 0
        }
        for ((heading, unit) in units) {
            if (cur.isNotEmpty() && (heading != curHead || curLen + unit.length + 2 > chunkChars)) {
                val prev = cur.joinToString("\n\n")
                val same = heading == curHead
                emit()
                if (same && overlap > 0) tail(prev, overlap).takeIf { it.isNotEmpty() }?.let { cur += it; curLen = it.length }
            }
            curHead = heading
            cur += unit
            curLen += unit.length + 2
        }
        emit()
        return chunks
    }

    private fun splitBlock(block: String, limit: Int): List<String> {
        val pieces = ArrayList<String>()
        var buf = ""
        for (s0 in splitSentences(block).ifEmpty { listOf(block) }) {
            var s = s0
            while (s.length > limit) {
                pieces += s.substring(0, limit)
                s = s.substring(limit)
            }
            if (buf.isNotEmpty() && buf.length + s.length + 1 > limit) {
                pieces += buf
                buf = s
            } else {
                buf = "$buf $s".trim()
            }
        }
        if (buf.isNotEmpty()) pieces += buf
        return pieces
    }

    private fun tail(text: String, overlap: Int): String {
        val sents = splitSentences(text)
        val tail = ArrayList<String>()
        var total = 0
        for (s in sents.reversed()) {
            if (total + s.length > overlap) break
            tail.add(0, s)
            total += s.length + 1
        }
        if (tail.isEmpty() && sents.isNotEmpty()) return sents.last().takeLast(overlap)
        return tail.joinToString(" ")
    }
}

/** 폰 안의 지식베이스: 조각 목록 + BM25 색인 */
class LocalKnowledgeBase(val chunks: List<KbChunk>) {
    private val bm25 = Bm25(chunks.map { it.searchText })

    val documentCount: Int get() = chunks.map { it.source }.distinct().size

    fun search(query: String, k: Int = 3): List<KbChunk> =
        if (query.isBlank()) emptyList() else bm25.search(query, k).map { chunks[it.first] }

    companion object {
        fun fromMarkdown(files: List<Pair<String, String>>): LocalKnowledgeBase =
            LocalKnowledgeBase(files.flatMap { (name, raw) ->
                val text = MarkdownChunker.normalize(raw)
                val title = MarkdownChunker.title(text, name)
                MarkdownChunker.chunk(text, title, name)
            })

        /** 질문과 가장 많이 겹치는 문장 n개 (LLM이 없을 때의 추출형 답변) */
        fun bestSentences(chunk: KbChunk, query: String, n: Int = 2): String {
            val q = KoreanTokenizer.tokenizeQuery(query).toSet()
            val sents = MarkdownChunker.splitSentences(chunk.text.replace(Regex("(?m)^\\s*[-*|]\\s*"), ""))
                .map { it.replace("**", "").replace("|", " ").trim() }.filter { it.length > 3 }
            if (sents.isEmpty()) return chunk.text.take(160)
            val ranked = sents.withIndex().sortedByDescending { (i, s) -> KoreanTokenizer.tokenize(s).count { it in q } * 10 - i }
            return ranked.take(n).sortedBy { it.index }.joinToString(" ") { it.value }
        }
    }
}
