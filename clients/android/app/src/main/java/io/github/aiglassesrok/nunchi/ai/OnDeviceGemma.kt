package io.github.aiglassesrok.nunchi.ai

import android.content.Context
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.SamplerConfig
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * 폰 안의 Gemma 4 (Google AI Edge LiteRT-LM). 인터넷 없이 폰 GPU/CPU로 돌아갑니다.
 *
 * 모델 파일(.litertlm, 예: Gemma 4 E2B — 사진 입력 지원)을 아래 폴더에 넣으면 자동으로 씁니다.
 *   /sdcard/Android/data/io.github.aiglassesrok.nunchi/files/models/
 *   (adb push 또는 앱 설정의 '모델 파일 가져오기')
 * 파일이 없으면 [available]이 false → 허브의 Gemma 4나 추출형 답변으로 대신합니다.
 *
 * 처음 부를 때 모델을 메모리에 올리느라 수 초~수십 초 걸리고, 그다음부터는 바로 답합니다.
 */
class OnDeviceGemma(private val context: Context) {
    private var engine: Engine? = null
    private var enginePath: String? = null
    private var engineVision = false
    private val lock = Mutex()

    @Volatile var backendName: String = ""
        private set

    val modelDir: File
        get() = File(context.getExternalFilesDir(null) ?: context.filesDir, "models").apply { mkdirs() }

    fun modelFile(): File? =
        modelDir.listFiles()?.filter { it.isFile && it.name.endsWith(".litertlm") && it.length() > 1_000_000 }?.maxByOrNull { it.lastModified() }

    val available: Boolean get() = modelFile() != null

    fun status(): String {
        val f = modelFile() ?: return "모델 없음 (models 폴더에 .litertlm 넣기)"
        return "${f.name} · ${f.length() / 1_000_000}MB" + if (engine != null) " · 불러옴($backendName)" else ""
    }

    /** 대화 하나 안에서 여러 번 주고받기 (사진은 첫 턴에 한 번만 넣으면 다음 턴도 그 사진을 기억) */
    suspend fun <T> conversation(system: String, vision: Boolean, maxTokens: Int = 320, block: suspend (Turn) -> T): T =
        withContext(Dispatchers.Default) {
            lock.withLock {
                val e = engine(vision)
                val conv = e.createConversation(
                    ConversationConfig(
                        systemInstruction = Contents.of(system),
                        samplerConfig = SamplerConfig(topK = 40, topP = 0.95, temperature = 0.3),
                        maxOutputToken = maxTokens,
                    ),
                )
                conv.use { block(Turn(it)) }
            }
        }

    class Turn internal constructor(private val conv: Conversation) {
        suspend fun send(text: String, image: ByteArray? = null, onDelta: (String) -> Unit = {}): String {
            val contents = if (image != null) Contents.of(Content.ImageBytes(image), Content.Text(text)) else Contents.of(text)
            val sb = StringBuilder()
            conv.sendMessageAsync(contents).collect { msg ->
                val piece = msg.contents.contents.filterIsInstance<Content.Text>().joinToString("") { it.text }
                // 보통 조각(delta)으로 오지만, 누적 문자열로 오는 경우도 견딤
                val delta = if (sb.isNotEmpty() && piece.length > sb.length && piece.startsWith(sb)) piece.substring(sb.length) else piece
                if (delta.isNotEmpty()) {
                    sb.append(delta)
                    onDelta(delta)
                }
            }
            return sb.toString().trim()
        }
    }

    private fun engine(vision: Boolean): Engine {
        val model = modelFile() ?: throw IllegalStateException("폰에 Gemma 모델 파일(.litertlm)이 없어요")
        engine?.let { if (enginePath == model.path && (engineVision || !vision)) return it }
        close()
        var last: Throwable? = null
        // GPU가 빠르지만 기기·에뮬레이터에 따라 안 될 수 있어 CPU로 한 번 더
        for (gpu in listOf(true, false)) {
            try {
                val backend = if (gpu) Backend.GPU() else Backend.CPU()
                val config = if (vision) {
                    EngineConfig(
                        modelPath = model.path, backend = backend, visionBackend = if (gpu) Backend.GPU() else Backend.CPU(),
                        maxNumTokens = 4096, maxNumImages = 1, cacheDir = context.cacheDir.path,
                    )
                } else {
                    EngineConfig(modelPath = model.path, backend = backend, maxNumTokens = 4096, cacheDir = context.cacheDir.path)
                }
                val e = Engine(config)
                e.initialize()
                engine = e
                enginePath = model.path
                engineVision = vision
                backendName = if (gpu) "GPU" else "CPU"
                return e
            } catch (t: Throwable) { // 네이티브 라이브러리 오류(UnsatisfiedLinkError)까지
                last = t
            }
        }
        throw IllegalStateException("모델을 불러오지 못했어요: ${last?.message ?: last?.javaClass?.simpleName}")
    }

    fun close() {
        runCatching { engine?.close() }
        engine = null
        enginePath = null
    }
}
