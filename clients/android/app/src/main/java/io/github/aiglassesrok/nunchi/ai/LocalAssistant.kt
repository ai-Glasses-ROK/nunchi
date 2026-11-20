package io.github.aiglassesrok.nunchi.ai

import io.github.aiglassesrok.nunchi.drone.DroneTelemetry
import io.github.aiglassesrok.nunchi.drone.MavNames
import io.github.aiglassesrok.nunchi.rag.KbChunk
import io.github.aiglassesrok.nunchi.rag.LocalKnowledgeBase
import java.util.Locale

/** 폰 RAG 진행 상황 (허브 SSE 이벤트와 같은 흐름: 캡션 → 검색 → 글자 조각 → 끝) */
sealed interface LocalEvent {
    data class Caption(val text: String) : LocalEvent
    data class Retrieval(val sources: List<String>) : LocalEvent
    data class Delta(val text: String) : LocalEvent
}

data class LocalResult(val answer: String, val engine: String, val timings: Map<String, Long>)

/**
 * 폰 단독 RAG = 폰 Gemma 4(있으면) + 2-gram BM25 + 드론 실시간 상태.
 *
 *  사진(드론 카메라·안경)이 있으면  ① Gemma가 사진을 한 줄로 묘사(캡션)  ② 캡션+질문(+드론 상태 키워드)으로 검색
 *  ③ [실시간 기체 상태]·[참고 문서]·[질문]을 같은 대화에 넣어 답 — 허브의 '캡션→검색'을 폰 안에서 그대로.
 *  모델이 없으면 드론 상태 값과 문서 문장으로 '추출형 답변'을 만듭니다 (어떤 폰에서도 오프라인 동작).
 */
class LocalAssistant(private val gemma: OnDeviceGemma, private val kb: () -> LocalKnowledgeBase?) {

    suspend fun answer(
        question: String,
        live: DroneTelemetry?,
        image: ByteArray?,
        fromDroneCamera: Boolean,
        onEvent: (LocalEvent) -> Unit,
    ): LocalResult {
        val t0 = System.currentTimeMillis()
        val timings = LinkedHashMap<String, Long>()
        val liveOn = live != null && live.hasVehicle
        val hint = if (liveOn && DroneTelemetry.isDroneQuestion(question)) live!!.retrievalHint() else ""

        if (gemma.available) {
            return gemma.conversation(systemPrompt(liveOn, image != null, fromDroneCamera), vision = image != null) { turn ->
                var caption = ""
                if (image != null) {
                    val t = System.currentTimeMillis()
                    caption = turn.send(CAPTION_PROMPT, image).replace("\n", " ").take(160)
                    timings["caption_ms"] = System.currentTimeMillis() - t
                    if (caption.isNotBlank()) onEvent(LocalEvent.Caption(caption))
                }
                val t = System.currentTimeMillis()
                val hits = kb()?.search(listOf(question, caption, hint).filter { it.isNotBlank() }.joinToString(" "), 3).orEmpty()
                timings["retrieval_ms"] = System.currentTimeMillis() - t
                onEvent(LocalEvent.Retrieval(labels(hits)))
                val tl = System.currentTimeMillis()
                var first = 0L
                val answer = turn.send(userPrompt(question, hits, if (liveOn) live!!.report() else "")) { d ->
                    if (first == 0L) first = System.currentTimeMillis() - t0
                    onEvent(LocalEvent.Delta(d))
                }
                timings["first_token_ms"] = first
                timings["llm_ms"] = System.currentTimeMillis() - tl
                timings["total_ms"] = System.currentTimeMillis() - t0
                LocalResult(answer, "폰 Gemma · ${gemma.backendName}", timings)
            }
        }

        // 모델 없음 → 추출형
        val t = System.currentTimeMillis()
        val hits = kb()?.search(listOf(question, hint).filter { it.isNotBlank() }.joinToString(" "), 3).orEmpty()
        timings["retrieval_ms"] = System.currentTimeMillis() - t
        onEvent(LocalEvent.Retrieval(labels(hits)))
        val parts = mutableListOf<String>()
        if (image != null) parts += "폰에 Gemma 모델이 없어 사진은 분석하지 못했어요."
        val status = if (liveOn) telemetryAnswer(question, live!!) else null
        status?.let { parts += it }
        // 배터리·위치처럼 값만 묻는 질문은 값으로 끝. 문제·방법을 물으면(또는 값으로 못 답하면) 문서 문장을 덧붙임
        if (status == null || isTroubleQuestion(question)) {
            hits.firstOrNull()?.let { parts += "문서에서 찾은 내용이에요. " + LocalKnowledgeBase.bestSentences(it, "$question $hint") }
        }
        if (parts.isEmpty()) parts += if (kb() == null) "폰 지식베이스가 비어 있어요." else "문서에서 관련 내용을 찾지 못했어요."
        val answer = parts.joinToString(" ")
        onEvent(LocalEvent.Delta(answer))
        timings["total_ms"] = System.currentTimeMillis() - t0
        return LocalResult(answer, "폰 추출형(LLM 없음)", timings)
    }

    private fun labels(hits: List<KbChunk>) = hits.mapIndexed { i, h -> "[${i + 1}] ${h.label}" }

    companion object {
        const val CAPTION_PROMPT =
            "사진에 보이는 글자(숫자·코드·제품명 포함)와 주요 사물을 검색어로 쓸 수 있게 한 줄로 적어라. " +
                "문장으로 설명하지 말고 쉼표로 구분한 명사만 쓴다. 예: HG-2000 발전기 계기판, E2 코드, 빨간 경고등"

        private const val VOICE_SYSTEM = """너는 사용자가 쓰고 있는 스마트 안경 속 음성 비서야. 너의 답은 소리 내어 읽히고 화면에도 나온다.
규칙:
1. 한국어로 1~3문장, 120자 안쪽으로 짧게 말한다. 질문에 대한 답을 첫 문장에 바로 말한다.
2. 마크다운, 목록 기호, 표, 이모지, 번호 인용을 쓰지 않는다.
3. [참고 문서]가 주어지면 그 내용을 우선해서 답하고, 문서에 없는 사실을 지어내지 않는다.
4. 문서에 답이 없으면 "문서에는 없지만"이라고 먼저 말한 뒤 짧게 답한다. 확실하지 않으면 모른다고 말한다."""

        const val LIVE_HINT =
            "[실시간 기체 상태]는 지금 연결된 드론 비행컨트롤러(픽스호크)가 보낸 값이다. 배터리·위치·고도·모드·시동·GPS를 물으면 그 값으로 답하고, " +
                "기체 메시지(PreArm 등)가 있으면 [참고 문서]에서 그 메시지의 해결 방법을 찾아 말한다."
        const val DRONE_CAMERA_HINT = "첨부한 사진은 드론 카메라(FPV)로 지금 보고 있는 장면이다. 사진에서 보이는 것을 근거로 답한다."
        const val GLASSES_HINT = "첨부한 사진은 사용자가 지금 안경 카메라로 보고 있는 장면이다. 사진에서 보이는 것을 근거로 답한다."

        fun systemPrompt(live: Boolean, vision: Boolean, droneCamera: Boolean): String = buildString {
            append(VOICE_SYSTEM)
            if (live) append("\n").append(LIVE_HINT)
            if (vision) append("\n").append(if (droneCamera) DRONE_CAMERA_HINT else GLASSES_HINT)
        }

        fun userPrompt(question: String, hits: List<KbChunk>, liveReport: String): String = buildString {
            if (liveReport.isNotBlank()) append("[실시간 기체 상태]\n").append(liveReport).append("\n\n")
            append("[참고 문서]\n")
            if (hits.isEmpty()) append("(관련 문서를 찾지 못함)") else hits.forEachIndexed { i, h ->
                if (i > 0) append("\n\n")
                append("[${i + 1}] ${h.label}\n${h.text.trim()}")
            }
            append("\n\n[질문]\n").append(question)
        }

        private val TROUBLE = listOf("왜", "안 돼", "안돼", "안 걸", "경고", "에러", "오류", "메시지", "문제", "고장", "prearm", "프리암", "어떻게", "방법", "해결")

        fun isTroubleQuestion(q: String): Boolean = q.lowercase(Locale.ROOT).let { s -> TROUBLE.any { it in s } }

        /** LLM 없이 드론 상태 질문에 값으로 답하기 */
        fun telemetryAnswer(question: String, t: DroneTelemetry, nowMs: Long = System.currentTimeMillis()): String? {
            val q = question.lowercase(Locale.ROOT)
            val f = { v: Double, d: Int -> "%.${d}f".format(Locale.US, v) }
            val out = mutableListOf<String>()
            if (listOf("배터리", "전압", "잔량", "충전").any { it in q }) {
                out += buildString {
                    append("배터리는 ")
                    append(listOfNotNull(t.voltage?.let { "${f(it, 1)}V" }, t.batteryPct?.let { "잔량 $it%" }).joinToString(", ").ifEmpty { "정보가 없어" })
                    append("예요.")
                }
            }
            if (listOf("위치", "어디", "좌표").any { it in q } && t.lat != null && t.lon != null) {
                var s = "지금 위치는 위도 ${f(t.lat, 5)}, 경도 ${f(t.lon, 5)}예요."
                if (t.homeLat != null && t.homeLon != null) s += " 출발점에서 ${f(DroneTelemetry.distanceM(t.lat, t.lon, t.homeLat, t.homeLon), 0)}m 떨어져 있어요."
                out += s
            }
            if (listOf("고도", "높이", "몇 m", "몇m").any { it in q } && t.relAlt != null) out += "이륙 지점 기준 고도는 ${f(t.relAlt, 1)}m예요."
            if (listOf("gps", "위성").any { it in q }) out += "GPS는 ${MavNames.gpsFix(t.gpsFix)}이고 위성 ${maxOf(t.satellites, 0)}개를 잡고 있어요."
            if (listOf("속도", "빠르").any { it in q } && t.groundSpeed != null) out += "지면 속도는 ${f(t.groundSpeed, 1)}m/s예요."
            if (listOf("모드", "상태", "시동", "arm").any { it in q }) {
                out += "지금 모드는 ${MavNames.modeKo(t.mode)}이고 시동은 ${if (t.armed) "걸려 있어요" else "꺼져 있어요"}."
            }
            if (isTroubleQuestion(question)) {
                val prearm = t.currentPrearm(nowMs)
                val recent = t.messages.lastOrNull { it.severity <= 4 && !it.text.startsWith("PreArm") }
                out += when {
                    prearm.size == 1 -> "시동을 막는 점검은 '${prearm[0].text}'예요."
                    prearm.size > 1 -> "시동을 막는 점검이 ${prearm.size}개예요: " + prearm.joinToString(", ") { it.text.removePrefix("PreArm: ") } + "."
                    recent != null -> "최근 기체 경고는 '${recent.text}'예요."
                    else -> "지금 걸린 경고 메시지는 없어요."
                }
                val bad = t.unhealthy.filterNot { it.startsWith("시동 전 점검") && prearm.isNotEmpty() }
                if (bad.isNotEmpty()) out += "이상 센서: ${bad.joinToString(", ")}."
            }
            if (out.isEmpty() && DroneTelemetry.isDroneQuestion(question)) out += "드론 상태: ${t.summary()}."
            return out.takeIf { it.isNotEmpty() }?.joinToString(" ")
        }
    }
}
