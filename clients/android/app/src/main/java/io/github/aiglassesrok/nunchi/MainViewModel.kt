package io.github.aiglassesrok.nunchi

import android.annotation.SuppressLint
import android.app.Activity
import android.app.Application
import android.bluetooth.BluetoothManager
import android.content.Context
import android.graphics.BitmapFactory
import android.hardware.usb.UsbDevice
import android.net.Uri
import android.provider.OpenableColumns
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import io.github.aiglassesrok.nunchi.ai.LocalAssistant
import io.github.aiglassesrok.nunchi.ai.LocalEvent
import io.github.aiglassesrok.nunchi.ai.OnDeviceGemma
import io.github.aiglassesrok.nunchi.camera.CameraFrame
import io.github.aiglassesrok.nunchi.camera.DroneCamera
import io.github.aiglassesrok.nunchi.camera.MjpegSource
import io.github.aiglassesrok.nunchi.camera.UvcSource
import io.github.aiglassesrok.nunchi.camera.VirtualCameraSource
import io.github.aiglassesrok.nunchi.drone.BluetoothSppLink
import io.github.aiglassesrok.nunchi.drone.DroneConnection
import io.github.aiglassesrok.nunchi.drone.DroneTelemetry
import io.github.aiglassesrok.nunchi.drone.TcpLink
import io.github.aiglassesrok.nunchi.drone.UdpLink
import io.github.aiglassesrok.nunchi.drone.UsbSerial
import io.github.aiglassesrok.nunchi.drone.UsbSerialLink
import io.github.aiglassesrok.nunchi.drone.VirtualDroneLink
import io.github.aiglassesrok.nunchi.rag.KbChunk
import io.github.aiglassesrok.nunchi.rag.LocalKnowledgeBase
import io.github.aiglassesrok.nunchi.rag.MarkdownChunker
import java.io.File
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

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
    val engine: String = "hub", // hub = 허브(컴퓨터)에서 RAG·Gemma 4 | phone = 폰 안에서 (폰 Gemma 모델 + 2-gram BM25)
    val droneLink: String = "usb", // usb | bt | udp | tcp | virtual
    val droneBaud: Int = 57600,
    val droneUdpPort: Int = 14550,
    val droneTcp: String = "10.0.2.2:5760",
    val droneBtAddress: String = "",
    val cameraSource: String = "usb", // usb | mjpeg | virtual
    val cameraUrl: String = "http://192.168.4.1:81/stream",
)

/** 드론 카메라 화면 위에 보여 줄 Gemma 4 결과 */
data class DroneAnalysis(
    val text: String = "",
    val caption: String = "",
    val sources: List<String> = emptyList(),
    val engine: String = "",
    val meta: String = "",
    val busy: Boolean = false,
    val error: String = "",
)

data class UiState(
    val messages: List<ChatMessage> = listOf(
        ChatMessage(role = Role.NOTICE, text = "마이크 버튼을 누르고 말한 뒤 다시 누르세요. 카메라 버튼은 안경으로 사진을 찍어 함께 물어봅니다. 비행기 버튼은 드론(픽스호크·카메라) 화면이에요."),
    ),
    val recording: Boolean = false,
    val busy: Boolean = false,
    val hubStatus: String = "확인 전",
    val glassesStatus: String = "확인 중",
    val deviceCount: Int = 0,
    val virtualGlasses: Boolean = false,
    val settings: HubSettings = HubSettings(),
    val analysis: DroneAnalysis = DroneAnalysis(),
    val autoAnalyze: Boolean = false,
    val kbInfo: String = "불러오는 중",
    val gemmaStatus: String = "",
    val openDrone: Int = 0, // 늘어날 때마다 드론 화면을 엶
)

class MainViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = app.getSharedPreferences("nunchi", Context.MODE_PRIVATE)
    private val recorder = VoiceRecorder(app)
    private val speaker = Speaker(app)
    private val glasses = Glasses(app)
    private val speech = OnDeviceSpeech(app)
    private val gemma = OnDeviceGemma(app)
    private var kb: LocalKnowledgeBase? = null
    private val assistant = LocalAssistant(gemma) { kb }
    private var sessionId = UUID.randomUUID().toString().take(12)
    private var sdkReady = false
    private var listening = false
    private var autoJob: Job? = null

    private val _ui = MutableStateFlow(UiState(settings = loadSettings()))
    val ui: StateFlow<UiState> = _ui.asStateFlow()

    /** 드론 비행컨트롤러(MAVLink) */
    private val droneLink = DroneConnection(viewModelScope)
    val drone: StateFlow<DroneTelemetry> = droneLink.state

    /** 드론 카메라 (프레임은 초당 10장 이상이라 UiState와 따로 흘림) */
    private val camera = DroneCamera(viewModelScope)
    val cameraFrame: StateFlow<CameraFrame?> = camera.frame
    val cameraStatus: StateFlow<String> = camera.status

    /** MainActivity가 Meta AI 앱 권한 요청을 대신 띄워 줍니다. */
    var requestWearablesPermission: (suspend (Permission) -> PermissionStatus)? = null

    init {
        reloadKnowledge()
    }

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
        engine = prefs.getString("engine", "hub") ?: "hub",
        droneLink = prefs.getString("drone_link", "usb") ?: "usb",
        droneBaud = prefs.getInt("drone_baud", 57600),
        droneUdpPort = prefs.getInt("drone_udp_port", 14550),
        droneTcp = prefs.getString("drone_tcp", null) ?: HubSettings().droneTcp,
        droneBtAddress = prefs.getString("drone_bt", "") ?: "",
        cameraSource = prefs.getString("camera_source", "usb") ?: "usb",
        cameraUrl = prefs.getString("camera_url", null) ?: HubSettings().cameraUrl,
    )

    fun saveSettings(s: HubSettings) {
        prefs.edit()
            .putString("hub_url", s.hubUrl)
            .putString("hub_token", s.token)
            .putBoolean("glasses_mic", s.useGlassesMic)
            .putString("voice", s.voice)
            .putString("engine", s.engine)
            .putString("drone_link", s.droneLink)
            .putInt("drone_baud", s.droneBaud)
            .putInt("drone_udp_port", s.droneUdpPort)
            .putString("drone_tcp", s.droneTcp)
            .putString("drone_bt", s.droneBtAddress)
            .putString("camera_source", s.cameraSource)
            .putString("camera_url", s.cameraUrl)
            .apply()
        _ui.update { it.copy(settings = s) }
        checkHub()
    }

    private fun client() = HubClient(_ui.value.settings.hubUrl, _ui.value.settings.token)

    private val phoneMode: Boolean get() = _ui.value.settings.engine == "phone"

    fun checkHub() {
        viewModelScope.launch {
            val status = runCatching { "연결됨 · " + client().health() }.getOrElse { "연결 실패: ${it.message}" }
            _ui.update { it.copy(hubStatus = status, gemmaStatus = gemma.status()) }
        }
    }

    /** 드론이 연결돼 있으면 허브에 함께 보낼 실시간 상태 */
    private fun live(question: String): HubClient.Live {
        val t = drone.value
        if (!t.hasVehicle) return HubClient.Live()
        // 상태 키워드는 드론을 묻는 질문일 때만 검색어에 (장면 설명엔 사진 캡션이 검색어 역할)
        val hint = if (DroneTelemetry.isDroneQuestion(question)) t.retrievalHint() else ""
        return HubClient.Live(context = t.report(), hint = hint)
    }

    // ------------------------------------------------------------ 녹음 → 음성 질문
    fun toggleRecording() {
        viewModelScope.launch {
            if (phoneMode) return@launch togglePhoneListening()
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

    private suspend fun togglePhoneListening() {
        if (!listening) {
            if (!speech.available) return notice("이 폰은 한국어 '기기 내' 음성 인식을 쓸 수 없어요. 글자로 묻거나 허브 모드를 쓰세요. (설정 > 음성 인식에서 한국어 오프라인 데이터 설치)")
            speaker.stopAll()
            speech.start()
            listening = true
            _ui.update { it.copy(recording = true) }
            return
        }
        listening = false
        _ui.update { it.copy(recording = false) }
        speech.stop()
        val text = speech.result().trim()
        if (text.length < 2) return notice("잘 못 들었어요. 다시 한 번 말씀해 주세요.")
        ask(text)
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
        // 음성은 받아쓰기 전이라 질문을 모름 → 상태 키워드는 늘 보내고, 허브가 받아쓴 질문을 보고 검색어에 붙일지 정함
        val t = drone.value
        val voiceLive = if (t.hasVehicle) HubClient.Live(t.report(), t.retrievalHint()) else HubClient.Live()
        runHub(userId) { c, speak, onEvent -> c.voice(wav, sessionId, speak, voiceLive, onEvent) }
    }

    // ------------------------------------------------------------ 글자 / 사진 질문
    fun ask(text: String) {
        val q = text.trim()
        if (q.isEmpty()) return
        speaker.stopAll()
        val userId = append(ChatMessage(role = Role.USER, text = q))
        viewModelScope.launch {
            if (phoneMode) runLocal(q, null) else runHub(userId) { c, speak, onEvent -> c.ask(q, sessionId, speak, live(q), onEvent) }
        }
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
            if (phoneMode) runLocal(q, jpeg) else runHub(userId) { c, speak, onEvent -> c.vision(jpeg, q, sessionId, speak, live(q), "glasses", onEvent) }
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

    // ------------------------------------------------------------ 드론 비행컨트롤러 (MAVLink)
    fun usbSerialDevices(): List<String> = UsbSerial.devices(getApplication()).map { UsbSerial.describe(it) }

    @SuppressLint("MissingPermission") // BLUETOOTH_CONNECT는 MainActivity가 먼저 받음
    fun bondedBluetooth(): List<Pair<String, String>> = runCatching {
        getApplication<Application>().getSystemService(BluetoothManager::class.java)?.adapter?.bondedDevices
            ?.map { (it.name ?: it.address) to it.address }?.sortedBy { it.first }.orEmpty()
    }.getOrDefault(emptyList())

    fun connectDrone(kind: String = _ui.value.settings.droneLink) {
        val s = _ui.value.settings
        val app = getApplication<Application>()
        viewModelScope.launch {
            val link = when (kind) {
                "virtual" -> VirtualDroneLink()
                "udp" -> UdpLink(s.droneUdpPort)
                "tcp" -> {
                    val host = s.droneTcp.substringBeforeLast(':')
                    val port = s.droneTcp.substringAfterLast(':').toIntOrNull() ?: 5760
                    TcpLink(host, port)
                }
                "bt" -> {
                    val (name, addr) = bondedBluetooth().firstOrNull { it.second == s.droneBtAddress }
                        ?: return@launch notice("블루투스 모듈을 고르세요. (먼저 안드로이드 설정에서 HC-05/06을 페어링, PIN 1234/0000)")
                    BluetoothSppLink(app, addr, name)
                }
                else -> {
                    val device = UsbSerial.devices(app).firstOrNull()
                        ?: return@launch notice("USB 시리얼 장치가 없어요. 텔레메트리 라디오(지상용)나 픽스호크를 OTG로 꽂아 주세요.")
                    if (!UsbSerial.requestPermission(app, device)) return@launch notice("USB 권한이 거절됐어요.")
                    UsbSerialLink(app, device, s.droneBaud)
                }
            }
            if (kind != s.droneLink) saveSettings(s.copy(droneLink = kind))
            droneLink.connect(link)
        }
    }

    fun disconnectDrone() = droneLink.disconnect()

    fun requestDroneScreen() = _ui.update { it.copy(openDrone = it.openDrone + 1) }

    /** 실물 없이 한 번에 시험: 가상 드론 + 가상 카메라 + 드론 화면 (adb: am start … --es nunchi_demo drone) */
    fun startDemo() {
        connectDrone("virtual")
        startCamera("virtual")
        requestDroneScreen()
    }

    /** usb_device_filter로 앱이 열렸을 때: 시리얼이면 텔레메트리, 영상이면 카메라를 바로 연결 */
    fun onUsbAttached(device: UsbDevice) {
        val isVideo = (0 until device.interfaceCount).any { device.getInterface(it).interfaceClass == android.hardware.usb.UsbConstants.USB_CLASS_VIDEO }
        when {
            isVideo -> startCamera("usb")
            UsbSerial.detect(device) != null && !droneLink.connected -> connectDrone("usb")
            else -> return
        }
        requestDroneScreen()
    }

    // ------------------------------------------------------------ 드론 카메라 → Gemma 4 → 화면
    fun startCamera(kind: String = _ui.value.settings.cameraSource) {
        val s = _ui.value.settings
        if (kind != s.cameraSource) saveSettings(s.copy(cameraSource = kind))
        val app = getApplication<Application>()
        if (kind == "usb" && !UvcSource.hasDevice(app)) {
            notice("USB 영상 수신기가 없어요. 5.8GHz FPV 수신기(OTG/UVC형)를 폰에 꽂아 주세요.")
        }
        camera.start(
            when (kind) {
                "virtual" -> VirtualCameraSource(app)
                "mjpeg" -> MjpegSource(s.cameraUrl)
                else -> UvcSource(app)
            },
        )
    }

    fun stopCamera() {
        setAutoAnalyze(false)
        camera.stop()
    }

    /** 지금 드론 카메라 화면을 Gemma 4로 분석해서 화면에 띄움 (폰 모델이 있으면 폰, 없으면 허브) */
    fun analyzeDroneFrame(question: String, speak: Boolean = true) {
        if (_ui.value.analysis.busy) return
        val q = question.trim().ifEmpty { DEFAULT_DRONE_QUESTION }
        val jpeg = camera.latestJpeg() ?: return run { _ui.update { it.copy(analysis = DroneAnalysis(error = "카메라 화면이 아직 없어요. 카메라를 켜 주세요.")) } }
        viewModelScope.launch { runDroneAnalysis(q, jpeg, speak) }
    }

    private suspend fun runDroneAnalysis(q: String, jpeg: ByteArray, speak: Boolean) {
        val usePhone = phoneMode && gemma.available
        _ui.update { it.copy(analysis = DroneAnalysis(busy = true, engine = if (usePhone) "폰 Gemma 4" else "허브 Gemma 4")) }
        fun edit(change: (DroneAnalysis) -> DroneAnalysis) = _ui.update { it.copy(analysis = change(it.analysis)) }
        val t0 = System.currentTimeMillis()
        try {
            if (usePhone) {
                val r = assistant.answer(q, drone.value, jpeg, fromDroneCamera = true) { ev ->
                    when (ev) {
                        is LocalEvent.Caption -> edit { it.copy(caption = ev.text) }
                        is LocalEvent.Retrieval -> edit { it.copy(sources = ev.sources) }
                        is LocalEvent.Delta -> edit { it.copy(text = it.text + ev.text) }
                    }
                }
                edit { it.copy(text = r.answer, engine = r.engine, meta = formatTimings(r.timings.mapValues { v -> v.value.toDouble() })) }
                if (speak && _ui.value.settings.voice != "off") speaker.speak(r.answer)
            } else {
                val answer = StringBuilder()
                client().vision(jpeg, q, sessionId, speak = false, live = live(q), source = "drone") { ev ->
                    when (ev) {
                        is HubEvent.Caption -> edit { it.copy(caption = ev.text) }
                        is HubEvent.Retrieval -> edit { it.copy(sources = ev.sources) }
                        is HubEvent.Delta -> { answer.append(ev.text); edit { it.copy(text = answer.toString()) } }
                        is HubEvent.Error -> edit { it.copy(error = "${ev.stage}: ${ev.message}") }
                        is HubEvent.Done -> {
                            if (!ev.ok || answer.isEmpty()) edit { it.copy(text = ev.answer) }
                            edit { it.copy(meta = formatTimings(ev.timings)) }
                            if (speak && _ui.value.settings.voice != "off") speaker.speak(ev.speech)
                        }
                        else -> Unit
                    }
                }
            }
        } catch (e: Exception) {
            val hint = if (!usePhone && phoneMode) " (폰에 Gemma 모델이 없어 허브로 보냈어요)" else ""
            edit { it.copy(error = "분석 실패: ${e.message}$hint") }
        } finally {
            edit { it.copy(busy = false, meta = it.meta.ifEmpty { "⏱ 전체 ${System.currentTimeMillis() - t0}ms" }) }
        }
    }

    /** 몇 초마다 자동 분석 (앞의 분석이 끝난 뒤 interval초 쉬고 다음) */
    fun setAutoAnalyze(on: Boolean, intervalSec: Int = 6, question: String = DEFAULT_DRONE_QUESTION) {
        autoJob?.cancel()
        autoJob = null
        _ui.update { it.copy(autoAnalyze = on) }
        if (!on) return
        autoJob = viewModelScope.launch {
            while (isActive) {
                val jpeg = camera.latestJpeg()
                if (jpeg != null && !_ui.value.analysis.busy) runDroneAnalysis(question, jpeg, speak = false)
                delay(intervalSec * 1000L)
            }
        }
    }

    // ------------------------------------------------------------ 폰 단독 RAG
    private suspend fun runLocal(question: String, image: ByteArray?) {
        _ui.update { it.copy(busy = true) }
        val answerId = append(ChatMessage(role = Role.ASSISTANT, text = ""))
        val sb = StringBuilder()
        try {
            val r = assistant.answer(question, drone.value, image, fromDroneCamera = false) { ev ->
                when (ev) {
                    is LocalEvent.Caption -> edit(answerId) { it.copy(meta = it.meta + "📷 ${ev.text}\n") }
                    is LocalEvent.Retrieval -> edit(answerId) { it.copy(sources = ev.sources) }
                    is LocalEvent.Delta -> { sb.append(ev.text); edit(answerId) { it.copy(text = sb.toString()) } }
                }
            }
            val droneMark = if (drone.value.hasVehicle) " · 드론 상태 반영" else ""
            edit(answerId) { it.copy(text = r.answer, meta = it.meta + "📱 ${r.engine}$droneMark\n" + formatTimings(r.timings.mapValues { v -> v.value.toDouble() })) }
            if (_ui.value.settings.voice != "off") speaker.speak(r.answer)
        } catch (e: Exception) {
            edit(answerId) { it.copy(text = "폰에서 답을 만들지 못했어요: ${e.message}") }
        } finally {
            _ui.update { it.copy(busy = false, gemmaStatus = gemma.status()) }
        }
    }

    /** 앱에 든 예제 문서(assets/kb) + 내가 넣은 문서(files/kb: .md·.txt·허브에서 받은 kb.json) */
    fun reloadKnowledge() {
        val app = getApplication<Application>()
        viewModelScope.launch {
            val loaded = withContext(Dispatchers.IO) {
                runCatching {
                    val md = mutableListOf<Pair<String, String>>()
                    app.assets.list("kb")?.filter { it.endsWith(".md") }?.forEach { name ->
                        md += name to app.assets.open("kb/$name").bufferedReader().use { it.readText() }
                    }
                    val extra = mutableListOf<KbChunk>()
                    userKbDir().listFiles()?.sortedBy { it.name }?.forEach { f ->
                        when {
                            f.name.endsWith(".json") -> extra += parseKbJson(f.readText())
                            f.name.endsWith(".md") || f.name.endsWith(".txt") -> md += f.name to f.readText()
                        }
                    }
                    LocalKnowledgeBase(LocalKnowledgeBase.fromMarkdown(md).chunks + extra)
                }
            }
            loaded.onSuccess { k ->
                kb = k
                _ui.update { it.copy(kbInfo = "문서 ${k.documentCount}개 · 조각 ${k.chunks.size}개", gemmaStatus = gemma.status()) }
            }.onFailure { e -> _ui.update { it.copy(kbInfo = "불러오기 실패: ${e.message}") } }
        }
    }

    private fun userKbDir() = File(getApplication<Application>().filesDir, "kb").apply { mkdirs() }

    private fun parseKbJson(text: String): List<KbChunk> {
        val o = JSONObject(text)
        require(o.optString("format") == "nunchi-kb") { "nunchi 지식베이스(kb.json)가 아니에요" }
        val arr = o.getJSONArray("chunks")
        return (0 until arr.length()).map { i ->
            val c = arr.getJSONObject(i)
            KbChunk(c.optString("title"), c.optString("heading"), c.optString("text"), c.optString("source"))
        }
    }

    fun downloadHubKnowledge() {
        viewModelScope.launch {
            runCatching {
                val json = client().exportKnowledge()
                withContext(Dispatchers.IO) { File(userKbDir(), "hub-kb.json").writeText(json) }
            }.onSuccess { notice("허브의 지식베이스를 폰에 저장했어요."); reloadKnowledge() }
                .onFailure { notice("지식베이스를 받지 못했어요: ${it.message}") }
        }
    }

    /** 파일 가져오기: .litertlm → 모델 폴더, .md·.txt·.json → 폰 지식베이스 */
    fun importFile(uri: Uri) {
        val app = getApplication<Application>()
        viewModelScope.launch {
            val name = app.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0) else null
            } ?: "import.bin"
            val target = when {
                name.endsWith(".litertlm") -> File(gemma.modelDir, name)
                name.endsWith(".md") || name.endsWith(".txt") || name.endsWith(".json") -> File(userKbDir(), name)
                else -> return@launch notice("가져올 수 있는 파일: Gemma 모델(.litertlm), 문서(.md·.txt), 허브 지식베이스(kb.json)")
            }
            notice("가져오는 중: $name …")
            runCatching {
                withContext(Dispatchers.IO) {
                    app.contentResolver.openInputStream(uri)?.use { input -> target.outputStream().use { input.copyTo(it, 1 shl 20) } }
                        ?: error("파일을 열지 못했어요")
                    if (target.name.endsWith(".json")) parseKbJson(target.readText())
                    if (target.name.endsWith(".md") || target.name.endsWith(".txt")) MarkdownChunker.normalize(target.readText())
                }
            }.onSuccess {
                notice("가져왔어요: $name")
                if (target.parentFile == gemma.modelDir) { gemma.close(); _ui.update { it.copy(gemmaStatus = gemma.status()) } } else reloadKnowledge()
            }.onFailure { target.delete(); notice("가져오지 못했어요: ${it.message}") }
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
                    is HubEvent.Caption -> edit(answerId) { it.copy(meta = it.meta + "📷 ${ev.text}\n") }
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
                        val droneMark = if (drone.value.hasVehicle) "🛩 드론 상태 반영\n" else ""
                        edit(answerId) { it.copy(meta = it.meta + droneMark + formatTimings(ev.timings)) }
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
        val names = listOf("stt_ms" to "인식", "caption_ms" to "캡션", "retrieval_ms" to "검색", "first_token_ms" to "첫 글자", "total_ms" to "전체")
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
        autoJob?.cancel()
        camera.stop()
        droneLink.disconnect()
        speech.cancel()
        gemma.close()
        speaker.shutdown()
        super.onCleared()
    }

    companion object {
        const val DEFAULT_DRONE_QUESTION = "지금 보이는 것을 한두 문장으로 설명해 줘." // 드론 카메라라는 건 프롬프트가 알려 줌
    }
}
