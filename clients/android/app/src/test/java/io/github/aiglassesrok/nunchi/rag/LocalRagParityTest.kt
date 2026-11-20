package io.github.aiglassesrok.nunchi.rag

import java.io.File
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 폰 검색기(Kotlin)가 허브 검색기(Python bm25.py·chunker.py)와 같은 결과를 내는지.
 * 기대값 bm25_parity.json은 허브 코드로 만든 것: 같은 예제 문서 → 같은 조각 수, 같은 토큰, 같은 상위 3개.
 */
class LocalRagParityTest {
    private val examples = File("../../../examples")
    private val fixture = JSONObject(javaClass.classLoader!!.getResource("bm25_parity.json")!!.readText())

    private fun kb(): LocalKnowledgeBase {
        val files = (File(examples, "knowledge").listFiles()!!.toList() + File(examples, "drone").listFiles()!!.toList())
            .filter { it.name.endsWith(".md") }.sortedBy { it.name }
        assertTrue("예제 문서를 찾지 못함: ${examples.absolutePath}", files.size >= 4)
        return LocalKnowledgeBase.fromMarkdown(files.map { it.name to it.readText() })
    }

    @Test
    fun tokenizerMatchesPython() {
        val expected = fixture.getJSONArray("tokens").let { a -> (0 until a.length()).map { a.getString(it) } }
        assertEquals(expected, KoreanTokenizer.tokenize("회의실 예약은 2시간 ①번 ＡＢＣ PreArm:Compass"))
    }

    @Test
    fun chunkerMatchesPython() {
        assertEquals(fixture.getInt("chunk_count"), kb().chunks.size)
    }

    @Test
    fun searchRanksLikeHub() {
        val kb = kb()
        val cases = fixture.getJSONArray("cases")
        var top1 = 0
        val diffs = mutableListOf<String>()
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val q = c.getString("q")
            val expected = c.getJSONArray("top").let { a -> (0 until a.length()).map { a.getString(it) } }
            val got = kb.search(q, 3).map { it.label }
            if (got.firstOrNull() == expected.firstOrNull()) top1++ else diffs += "$q\n  허브 ${expected.firstOrNull()}\n  폰   ${got.firstOrNull()}"
            if (got != expected) println("상위3 차이: $q\n  허브 $expected\n  폰   $got")
        }
        assertEquals("1위가 다른 질문:\n" + diffs.joinToString("\n"), cases.length(), top1)
    }

    @Test
    fun droneMessageFindsItsFix() {
        val top = kb().search("왜 시동이 안 걸려? PreArm: Compass not calibrated", 1).single()
        assertTrue(top.label, "Compass not calibrated" in top.label)
        val sentence = LocalKnowledgeBase.bestSentences(top, "나침반 보정 어떻게 해")
        assertTrue(sentence, "보정" in sentence)
    }
}
