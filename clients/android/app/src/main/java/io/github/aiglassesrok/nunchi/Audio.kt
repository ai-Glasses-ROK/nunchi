package io.github.aiglassesrok.nunchi

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.speech.tts.TextToSpeech
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale
import java.util.UUID
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull

/**
 * 질문 녹음기. 안경 마이크는 블루투스 SCO(=HFP 통화 경로)로 들어옵니다.
 * Meta 공식 샘플과 같은 방식: MODE_IN_COMMUNICATION + setCommunicationDevice(SCO) + VOICE_RECOGNITION 소스.
 */
class VoiceRecorder(context: Context) {
    private val audioManager = context.getSystemService(AudioManager::class.java)
    private var record: AudioRecord? = null
    private var thread: Thread? = null
    private val pcm = ByteArrayOutputStream()
    private var usedSco = false

    @Volatile private var running = false
    var routeDescription = ""
        private set

    val recordedSeconds: Double get() = synchronized(pcm) { pcm.size() / 2.0 / SAMPLE_RATE }

    @SuppressLint("MissingPermission") // RECORD_AUDIO 권한은 MainActivity에서 먼저 받습니다
    suspend fun start(useGlassesMic: Boolean) {
        var sco: AudioDeviceInfo? = null
        if (useGlassesMic) {
            sco = audioManager.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO }
                ?: throw IllegalStateException("안경 마이크(블루투스)를 찾지 못했어요. 안경 연결을 확인하거나 설정에서 '폰 마이크'로 바꿔 보세요.")
            audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
            if (!audioManager.setCommunicationDevice(sco)) throw IllegalStateException("안경 마이크로 경로를 바꾸지 못했어요.")
            // SCO 연결이 실제로 잡힐 때까지 최대 3초 기다림
            withTimeoutOrNull(3_000) {
                while (audioManager.communicationDevice?.type != AudioDeviceInfo.TYPE_BLUETOOTH_SCO) delay(100)
            }
            usedSco = true
            routeDescription = "${sco.productName} (블루투스 SCO)"
        } else {
            usedSco = false
            routeDescription = "폰 마이크"
        }

        val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuf, SAMPLE_RATE),
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            releaseRoute()
            throw IllegalStateException("마이크를 열지 못했어요.")
        }
        sco?.let { rec.setPreferredDevice(it) }
        synchronized(pcm) { pcm.reset() }
        rec.startRecording()
        record = rec
        running = true
        thread = Thread {
            val buf = ByteArray(minBuf)
            while (running) {
                val n = rec.read(buf, 0, buf.size)
                if (n > 0) synchronized(pcm) { pcm.write(buf, 0, n) }
            }
        }.apply { start() }
    }

    /** 녹음을 멈추고 16kHz 모노 WAV를 돌려줍니다. */
    fun stop(): ByteArray {
        running = false
        thread?.join(500)
        thread = null
        record?.let {
            runCatching { it.stop() }
            it.release()
        }
        record = null
        releaseRoute()
        val data = synchronized(pcm) { pcm.toByteArray() }
        return wav(data, SAMPLE_RATE)
    }

    private fun releaseRoute() {
        if (usedSco) {
            runCatching { audioManager.clearCommunicationDevice() }
            audioManager.mode = AudioManager.MODE_NORMAL // 답변은 A2DP(음악 음질)로 나가게
            usedSco = false
        }
    }

    companion object {
        const val SAMPLE_RATE = 16_000

        fun wav(pcm16: ByteArray, sampleRate: Int): ByteArray {
            val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN).apply {
                put("RIFF".toByteArray()); putInt(36 + pcm16.size); put("WAVE".toByteArray())
                put("fmt ".toByteArray()); putInt(16); putShort(1); putShort(1)
                putInt(sampleRate); putInt(sampleRate * 2); putShort(2); putShort(16)
                put("data".toByteArray()); putInt(pcm16.size)
            }
            return header.array() + pcm16
        }
    }
}

/**
 * 답 읽기: 폰의 한국어 TTS(오프라인 음성 데이터 필요) 또는 허브가 만든 WAV 재생.
 * TTS 한국어 데이터: 설정 > 일반 > 언어 > 텍스트 음성 변환 > 엔진 설정 > 음성 데이터 설치 (인터넷 될 때 미리)
 */
class Speaker(private val context: Context) : TextToSpeech.OnInitListener {
    private val tts = TextToSpeech(context, this)
    private var ready = false
    private val pending = ArrayDeque<String>()
    private val wavQueue = ArrayDeque<ByteArray>()
    private var player: MediaPlayer? = null

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts.language = Locale.KOREAN
            tts.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            ready = true
            while (pending.isNotEmpty()) speak(pending.removeFirst())
        }
    }

    fun speak(text: String) {
        val line = text.trim()
        if (line.isEmpty()) return
        if (!ready) {
            pending.addLast(line)
            return
        }
        tts.speak(line, TextToSpeech.QUEUE_ADD, null, UUID.randomUUID().toString())
    }

    fun play(wav: ByteArray) {
        wavQueue.addLast(wav)
        if (player == null) playNext()
    }

    private fun playNext() {
        val data = wavQueue.removeFirstOrNull() ?: run { player = null; return }
        val file = File.createTempFile("answer", ".wav", context.cacheDir).apply { writeBytes(data) }
        player = MediaPlayer().apply {
            setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).build())
            setDataSource(file.absolutePath)
            setOnCompletionListener { mp -> mp.release(); file.delete(); playNext() }
            setOnErrorListener { mp, _, _ -> mp.release(); file.delete(); playNext(); true }
            prepare()
            start()
        }
    }

    fun stopAll() {
        tts.stop()
        pending.clear()
        wavQueue.clear()
        player?.let { runCatching { it.stop() }; it.release() }
        player = null
    }

    fun shutdown() {
        stopAll()
        tts.shutdown()
    }
}
