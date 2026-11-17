package io.github.aiglassesrok.nunchi

import android.app.Activity
import android.app.Application
import android.content.Context
import android.graphics.BitmapFactory
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import java.util.UUID
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class Role { USER, ASSISTANT, NOTICE }

data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val role: Role,
    val text: String,
    val image: ImageBitmap? = null,
    val sources: List<String> = emptyList(),
    val meta: String = "",
)

/** 설정 (SharedPreferences) */
data class HubSettings(
    val hubUrl: String = "http://192.168.0.10:8765",
    val token: String = "",
    val useGlassesMic: Boolean = true,
    val voice: String = "phone", // phone | hub | off
)

data class UiState(
    val messages: List<ChatMessage> = listOf(
        ChatMessage(role = Role.NOTICE, text = "마이크 버튼을 누르고 말한 뒤 다시 누르세요. 카메라 버튼은 안경으로 사진을 찍어 함께 물어봅니다."),
    ),
    val recording: Boolean = false,
    val busy: Boolean = false,
    val hubStatus: String = "확인 전",
    val glassesStatus: String = "확인 중",
    val deviceCount: Int = 0,
    val virtualGlasses: Boolean = false,
    val settings: HubSettings = HubSettings(),
)

class MainViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = app.getSharedPreferences("nunchi", Context.MODE_PRIVATE)
    private val recorder = VoiceRecorder(app)
    private val speaker = Speaker(app)
    private val glasses = Glasses(app)
    private var sessionId = UUID.randomUUID().toString().take(12)
    private var sdkReady = false

    private val _ui = MutableStateFlow(UiState(settings = loadSettings()))
    val ui: StateFlow<UiState> = _ui.asStateFlow()

    /** MainActivity가 Meta AI 앱 권한 요청을 대신 띄워 줍니다. */
    var requestWearablesPermission: (suspend (Permission) -> PermissionStatus)? = null

    /** 블루투스 권한을 받은 뒤 MainActivity가 DAT SDK를 초기화하고 부릅니다. */
    fun onSdkInitialized() {
        if (sdkReady) return
        sdkReady = true
        viewModelScope.launch {
            Wearables.registrationState.collect { state -> _ui.update { it.copy(glassesStatus = glasses.registrationText(state)) } }
        }
        viewModelScope.launch {
            Wearables.devices.collect { devices -> _ui.update { it.copy(deviceCount = devices.size) } }
        }
        checkHub()
    }

    // ------------------------------------------------------------ 설정
    private fun loadSettings() = HubSettings(
        hubUrl = prefs.getString("hub_url", null) ?: HubSettings().hubUrl,
        token = prefs.getString("hub_token", "") ?: "",
        useGlassesMic = prefs.getBoolean("glasses_mic", true),
        voice = prefs.getString("voice", "phone") ?: "phone",
    )

    fun saveSettings(s: HubSettings) {
        prefs.edit()
            .putString("hub_url", s.hubUrl)
            .putString("hub_token", s.token)
            .putBoolean("glasses_mic", s.useGlassesMic)
            .putString("voice", s.voice)
            .apply()
        _ui.update { it.copy(settings = s) }
        checkHub()
    }

    private fun client() = HubClient(_ui.value.settings.hubUrl, _ui.value.settings.token)

    fun checkHub() {
        viewModelScope.launch {
            val status = runCatching { "연결됨 · " + client().health() }.getOrElse { "연결 실패: ${it.message}" }
            _ui.update { it.copy(hubStatus = status) }
        }
    }

    // ------------------------------------------------------------ 녹음 → 음성 질문
    fun toggleRecording() {
        viewModelScope.launch {
            if (_ui.value.recording) {
                finishRecording()
            } else {
                speaker.stopAll()
                runCatching { recorder.start(_ui.value.settings.useGlassesMic) }
                    .onSuccess { _ui.update { it.copy(recording = true) } }
                    .onFailure { notice("녹음을 시작하지 못했어요: ${it.message}") }
            }
        }
    }

    private suspend fun finishRecording() {
        val seconds = recorder.recordedSeconds
        val route = recorder.routeDescription
        val wav = recorder.stop()
        _ui.update { it.copy(recording = false) }
        if (seconds < 0.4) {
            notice("너무 짧아요. 버튼을 누르고 말한 다음 다시 눌러 주세요.")
            return
        }
        val userId = append(ChatMessage(role = Role.USER, text = "🎙️ 인식 중…", meta = route))
        runHub(userId) { c, speak, onEvent -> c.voice(wav, sessionId, speak, onEvent) }
    }

    // ------------------------------------------------------------ 글자 / 사진 질문
    fun ask(text: String) {
        val q = text.trim()
        if (q.isEmpty()) return
        speaker.stopAll()
        val userId = append(ChatMessage(role = Role.USER, text = q))
        viewModelScope.launch { runHub(userId) { c, speak, onEvent -> c.ask(q, sessionId, speak, onEvent) } }
    }

    fun askWithGlassesPhoto(question: String) {
        val q = question.trim().ifEmpty { "지금 보이는 게 뭐야? 짧게 설명해 줘." }
        val request = requestWearablesPermission ?: return notice("앱이 아직 준비되지 않았어요.")
        viewModelScope.launch {
            speaker.stopAll()
            _ui.update { it.copy(busy = true) }
            val jpeg = runCatching { glasses.capturePhoto(request) }.getOrElse {
                _ui.update { s -> s.copy(busy = false) }
                notice("사진을 찍지 못했어요: ${it.message}")
                return@launch
            }
            _ui.update { it.copy(busy = false) }
            val image = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size)?.asImageBitmap()
            val userId = append(ChatMessage(role = Role.USER, text = q, image = image))
            runHub(userId) { c, speak, onEvent -> c.vision(jpeg, q, sessionId, speak, onEvent) }
        }
    }

    fun resetConversation() {
        sessionId = UUID.randomUUID().toString().take(12)
        speaker.stopAll()
        _ui.update { it.copy(messages = listOf(ChatMessage(role = Role.NOTICE, text = "대화를 새로 시작했어요."))) }
    }

    // ------------------------------------------------------------ 안경
    fun registerGlasses(activity: Activity) = glasses.register(activity)

    fun unregisterGlasses(activity: Activity) = glasses.unregister(activity)

    fun setVirtualGlasses(on: Boolean) {
        viewModelScope.launch {
            if (on) {
                runCatching { glasses.enableVirtualGlasses() }
                    .onSuccess { msg -> _ui.update { it.copy(virtualGlasses = true) }; notice("$msg. 마이크는 설정에서 '폰 마이크'로 바꿔 쓰세요.") }
                    .onFailure { notice("가상 안경을 켜지 못했어요: ${it.message}") }
            } else {
                glasses.disableVirtualGlasses()
                _ui.update { it.copy(virtualGlasses = false) }
            }
        }
    }

    // ------------------------------------------------------------ 허브 응답 처리 (SSE)
    private suspend fun runHub(
        userId: String,
        call: suspend (HubClient, Boolean, suspend (HubEvent) -> Unit) -> Unit,
    ) {
        val s = _ui.value.settings
        _ui.update { it.copy(busy = true) }
        val answerId = append(ChatMessage(role = Role.ASSISTANT, text = ""))
        val answer = StringBuilder()
        try {
            call(client(), s.voice == "hub") { ev ->
                when (ev) {
                    is HubEvent.Transcript -> edit(userId) { it.copy(text = if (ev.text.isBlank()) "🎙️ (알아듣지 못함)" else "🎙️ ${ev.text}") }
                    is HubEvent.Retrieval -> edit(answerId) { it.copy(sources = ev.sources) }
                    is HubEvent.Delta -> {
                        answer.append(ev.text)
                        edit(answerId) { it.copy(text = answer.toString()) }
                    }
                    is HubEvent.Sentence -> if (s.voice == "phone") speaker.speak(ev.text)
                    is HubEvent.Audio -> if (s.voice == "hub") speaker.play(ev.wav)
                    is HubEvent.Error -> edit(answerId) { it.copy(meta = it.meta + "⚠️ ${ev.stage}: ${ev.message}\n") }
                    is HubEvent.Done -> {
                        if (!ev.ok || answer.isEmpty()) {
                            edit(answerId) { it.copy(text = ev.answer) }
                            if (s.voice != "off") speaker.speak(ev.speech)
                        }
                        edit(answerId) { it.copy(meta = it.meta + formatTimings(ev.timings)) }
                    }
                }
            }
            _ui.update { it.copy(hubStatus = if (it.hubStatus.startsWith("연결됨")) it.hubStatus else "연결됨") }
        } catch (e: Exception) {
            edit(answerId) { it.copy(text = "허브에 연결하지 못했어요: ${e.message}") }
            _ui.update { it.copy(hubStatus = "연결 실패") }
            if (s.voice != "off") speaker.speak("허브에 연결하지 못했어요.")
        } finally {
            _ui.update { it.copy(busy = false) }
        }
    }

    private fun formatTimings(t: Map<String, Double>): String {
        val names = listOf("stt_ms" to "인식", "retrieval_ms" to "검색", "first_token_ms" to "첫 글자", "total_ms" to "전체")
        val parts = names.mapNotNull { (k, n) -> t[k]?.let { "$n ${it.toInt()}ms" } }
        return if (parts.isEmpty()) "" else "⏱ " + parts.joinToString(" · ")
    }

    // ------------------------------------------------------------ 도우미
    private fun append(m: ChatMessage): String {
        _ui.update { it.copy(messages = it.messages + m) }
        return m.id
    }

    private fun edit(id: String, change: (ChatMessage) -> ChatMessage) {
        _ui.update { s -> s.copy(messages = s.messages.map { if (it.id == id) change(it) else it }) }
    }

    private fun notice(text: String) {
        append(ChatMessage(role = Role.NOTICE, text = text))
    }

    override fun onCleared() {
        speaker.shutdown()
        super.onCleared()
    }
}
