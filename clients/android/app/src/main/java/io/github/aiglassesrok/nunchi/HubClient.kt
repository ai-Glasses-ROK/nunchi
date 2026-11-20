package io.github.aiglassesrok.nunchi

import java.io.BufferedReader
import java.io.ByteArrayOutputStream
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/** 허브(nunchi serve)가 SSE로 보내는 이벤트 */
sealed interface HubEvent {
    data class Transcript(val text: String) : HubEvent
    data class Caption(val text: String) : HubEvent
    data class Retrieval(val sources: List<String>) : HubEvent
    data class Delta(val text: String) : HubEvent
    data class Sentence(val text: String) : HubEvent
    class Audio(val wav: ByteArray) : HubEvent
    data class Error(val stage: String, val message: String) : HubEvent
    data class Done(val ok: Boolean, val answer: String, val speech: String, val timings: Map<String, Double>) : HubEvent
}

class HubException(message: String) : Exception(message)

/**
 * 외부 라이브러리 없이 HttpURLConnection + org.json 으로 허브와 통신합니다.
 * 모든 네트워크 작업은 IO 스레드에서, 이벤트 콜백은 메인 스레드에서 호출됩니다.
 */
class HubClient(baseUrl: String, private val token: String) {
    private val base: String = baseUrl.trim().trimEnd('/').let {
        if (it.startsWith("http://") || it.startsWith("https://")) it else "http://$it"
    }

    suspend fun health(): String = withContext(Dispatchers.IO) {
        val conn = open("v1/health", "GET", timeoutMs = 5_000)
        val body = readBody(conn)
        val json = JSONObject(body)
        val models = json.optJSONObject("models")
        val index = json.optJSONObject("index")
        "${models?.optString("llm") ?: "?"} · 문서 ${index?.optInt("documents") ?: 0}개"
    }

    /**
     * live = 드론 비행컨트롤러의 실시간 상태. context는 프롬프트의 [실시간 기체 상태] 칸에,
     * hint(PreArm 메시지 등)는 검색어에 붙습니다. 옛 허브는 모르는 필드를 무시하므로 함께 보내도 안전합니다.
     */
    data class Live(val context: String = "", val hint: String = "")

    suspend fun ask(question: String, sessionId: String, speak: Boolean, live: Live = Live(), onEvent: suspend (HubEvent) -> Unit) {
        val body = JSONObject()
            .put("question", question)
            .put("session_id", sessionId)
            .put("stream", true)
            .put("speak", speak)
        if (live.context.isNotBlank()) body.put("live_context", live.context.take(4000))
        if (live.hint.isNotBlank()) body.put("live_hint", live.hint.take(500))
        stream("v1/ask", "application/json", body.toString().toByteArray(), onEvent)
    }

    suspend fun voice(wav: ByteArray, sessionId: String, speak: Boolean, live: Live = Live(), onEvent: suspend (HubEvent) -> Unit) {
        val form = Multipart()
        form.file("file", "voice.wav", "audio/wav", wav)
        form.field("session_id", sessionId)
        form.field("stream", "true")
        form.field("speak", speak.toString())
        form.live(live)
        stream("v1/voice", form.contentType, form.bytes(), onEvent)
    }

    suspend fun vision(
        jpeg: ByteArray,
        question: String,
        sessionId: String,
        speak: Boolean,
        live: Live = Live(),
        source: String = "glasses",
        onEvent: suspend (HubEvent) -> Unit,
    ) {
        val form = Multipart()
        form.file("image", "photo.jpg", "image/jpeg", jpeg)
        form.field("question", question)
        form.field("session_id", sessionId)
        form.field("stream", "true")
        form.field("speak", speak.toString())
        form.field("image_source", source) // glasses | drone (허브가 '안경 카메라' / '드론 카메라' 안내문을 고름)
        form.live(live)
        stream("v1/vision", form.contentType, form.bytes(), onEvent)
    }

    /** 폰 단독 모드용 지식베이스 받기 (허브의 /v1/kb/export, nunchi-kb 형식 JSON 문자열) */
    suspend fun exportKnowledge(): String = withContext(Dispatchers.IO) {
        val text = readBody(open("v1/kb/export", "GET", timeoutMs = 30_000))
        require(JSONObject(text).optString("format") == "nunchi-kb") { "nunchi 지식베이스 형식이 아니에요" }
        text
    }

    // ---------------------------------------------------------------- 내부

    private fun open(path: String, method: String, timeoutMs: Int = 120_000): HttpURLConnection {
        val conn = URL("$base/$path").openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 5_000
        conn.readTimeout = timeoutMs
        if (token.isNotBlank()) conn.setRequestProperty("Authorization", "Bearer ${token.trim()}")
        return conn
    }

    private fun readBody(conn: HttpURLConnection): String {
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
        if (code !in 200..299) {
            throw HubException(if (code == 401) "토큰이 맞지 않아요 (설정에서 허브 토큰 확인)" else "허브 오류 $code: ${text.take(200)}")
        }
        return text
    }

    /** SSE 읽기: `event:` 줄 다음 `data:` 줄이 오면 이벤트 하나 */
    private suspend fun stream(path: String, contentType: String, body: ByteArray, onEvent: suspend (HubEvent) -> Unit) {
        withContext(Dispatchers.IO) {
            val conn = open(path, "POST")
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", contentType)
            conn.setFixedLengthStreamingMode(body.size)
            conn.outputStream.use { it.write(body) }
            val code = conn.responseCode
            if (code !in 200..299) readBody(conn) // 예외 발생
            BufferedReader(InputStreamReader(conn.inputStream, Charsets.UTF_8)).use { reader ->
                var type = "message"
                while (true) {
                    val line = reader.readLine() ?: break
                    when {
                        line.startsWith("event: ") -> type = line.removePrefix("event: ")
                        line.startsWith("data: ") -> parse(type, line.removePrefix("data: "))?.let { ev ->
                            withContext(Dispatchers.Main) { onEvent(ev) }
                        }
                    }
                }
            }
            conn.disconnect()
        }
    }

    private fun parse(type: String, data: String): HubEvent? {
        val o = runCatching { JSONObject(data) }.getOrNull() ?: return null
        return when (type) {
            "transcript" -> HubEvent.Transcript(o.optString("text"))
            "caption" -> HubEvent.Caption(o.optString("text"))
            "retrieval" -> {
                val arr = o.optJSONArray("sources") ?: JSONArray()
                HubEvent.Retrieval((0 until arr.length()).map { i ->
                    val s = arr.getJSONObject(i)
                    "[${s.optInt("n")}] ${s.optString("label")}"
                })
            }
            "delta" -> HubEvent.Delta(o.optString("text"))
            "sentence" -> HubEvent.Sentence(o.optString("text"))
            "audio" -> HubEvent.Audio(android.util.Base64.decode(o.optString("wav_b64"), android.util.Base64.DEFAULT))
            "error" -> HubEvent.Error(o.optString("stage"), o.optString("message"))
            "done" -> {
                val t = o.optJSONObject("timings")
                val timings = buildMap { t?.keys()?.forEach { k -> put(k, t.optDouble(k)) } }
                HubEvent.Done(o.optBoolean("ok"), o.optString("answer"), o.optString("speech"), timings)
            }
            else -> null
        }
    }

    /** multipart/form-data 만들기 */
    private class Multipart {
        private val boundary = "Nunchi-${UUID.randomUUID()}"
        private val out = ByteArrayOutputStream()
        val contentType get() = "multipart/form-data; boundary=$boundary"

        fun field(name: String, value: String) {
            out.write("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n".toByteArray())
        }

        fun file(name: String, filename: String, mime: String, data: ByteArray) {
            out.write("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"; filename=\"$filename\"\r\nContent-Type: $mime\r\n\r\n".toByteArray())
            out.write(data)
            out.write("\r\n".toByteArray())
        }

        fun live(live: Live) {
            if (live.context.isNotBlank()) field("live_context", live.context.take(4000))
            if (live.hint.isNotBlank()) field("live_hint", live.hint.take(500))
        }

        fun bytes(): ByteArray {
            out.write("--$boundary--\r\n".toByteArray())
            return out.toByteArray()
        }
    }
}
