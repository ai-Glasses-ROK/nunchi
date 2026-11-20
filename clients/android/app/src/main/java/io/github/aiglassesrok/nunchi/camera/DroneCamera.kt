package io.github.aiglassesrok.nunchi.camera

import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.hardware.usb.UsbDevice
import android.media.MediaMetadataRetriever
import android.os.Handler
import android.os.Looper
import com.herohan.uvcapp.CameraHelper
import com.herohan.uvcapp.ICameraHelper
import com.serenegiant.usb.UVCCamera
import com.serenegiant.utils.UVCUtils
import io.github.aiglassesrok.nunchi.R
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlin.coroutines.resume
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine

/** 드론 카메라 한 장. jpeg는 원본이 JPEG일 때(MJPEG·UVC) 다시 압축하지 않고 그대로 Gemma에 보냅니다. */
class CameraFrame(val bitmap: Bitmap, val jpeg: ByteArray?, val timeMs: Long)

/** 프레임을 만들어 내는 곳 (USB 수신기·네트워크·가상). run은 취소될 때까지 돌며 onFrame을 부릅니다. */
interface FrameSource {
    val description: String
    suspend fun run(onFrame: (CameraFrame) -> Unit)
}

/**
 * 드론 카메라 관리: 소스 하나를 돌리고 최신 프레임·상태를 화면에 내보냅니다.
 *
 * 이 키트(B-CUBE 아날로그 FPV 카메라 → 5.8GHz VTX)의 영상을 폰으로 받는 방법:
 *  - **USB 수신기**: 5.8GHz FPV 수신기 중 'OTG/UVC' 제품(폰에 꽂으면 USB 카메라로 보이는 것)을 폰 USB-C에 연결
 *  - **네트워크(MJPEG)**: ESP32-CAM, IP 카메라, 노트북의 영상 중계 등 `http://…` MJPEG 주소
 *  - **가상**: 앱에 든 합성 항공 영상 (실물 없이 Gemma 4 경로 시험)
 */
class DroneCamera(private val scope: CoroutineScope) {
    private val _frame = MutableStateFlow<CameraFrame?>(null)
    val frame: StateFlow<CameraFrame?> = _frame.asStateFlow()
    private val _status = MutableStateFlow("꺼짐")
    val status: StateFlow<String> = _status.asStateFlow()
    private var job: Job? = null

    val running: Boolean get() = job?.isActive == true

    fun start(source: FrameSource) {
        stop()
        _status.value = "${source.description} · 연결 중…"
        job = scope.launch(Dispatchers.IO) {
            var count = 0
            var windowStart = System.currentTimeMillis()
            try {
                source.run { f ->
                    _frame.value = f
                    count++
                    val now = System.currentTimeMillis()
                    if (now - windowStart >= 1_000) {
                        _status.value = "${source.description} · ${f.bitmap.width}×${f.bitmap.height} · ${count * 1000 / (now - windowStart)}fps"
                        count = 0
                        windowStart = now
                    }
                }
                _status.value = "${source.description} · 끝남"
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (currentCoroutineContext().isActive) _status.value = "카메라 오류: ${e.message ?: e.javaClass.simpleName}"
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        _status.value = "꺼짐"
    }

    /** Gemma에 보낼 JPEG (긴 변 maxSide 이하). 원본 JPEG가 작으면 그대로 */
    fun latestJpeg(maxSide: Int = 768): ByteArray? {
        val f = _frame.value ?: return null
        val b = f.bitmap
        if (f.jpeg != null && maxOf(b.width, b.height) <= maxSide) return f.jpeg
        val scale = maxSide.toFloat() / maxOf(b.width, b.height)
        val scaled = if (scale < 1f) Bitmap.createScaledBitmap(b, (b.width * scale).toInt(), (b.height * scale).toInt(), true) else b
        return ByteArrayOutputStream().use { out ->
            scaled.compress(Bitmap.CompressFormat.JPEG, 85, out)
            out.toByteArray()
        }
    }
}

// ------------------------------------------------------------------ 가상 카메라

/** 앱에 든 합성 항공 영상(착륙장 H·도로·차)을 초당 10장으로 반복 재생 */
class VirtualCameraSource(private val context: Context, private val rawRes: Int = R.raw.virtual_drone_feed) : FrameSource {
    override val description = "가상 드론 카메라"

    override suspend fun run(onFrame: (CameraFrame) -> Unit) {
        val file = File(context.cacheDir, "virtual_drone_feed.mp4")
        if (!file.exists()) context.resources.openRawResource(rawRes).use { input -> file.outputStream().use { input.copyTo(it) } }
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(file.path)
            val durationMs = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 8_000L
            // 미리 한 바퀴 뽑아 두고 돌림 (MediaMetadataRetriever는 매번 뽑으면 느림)
            val frames = (0 until durationMs step 100).mapNotNull { ms ->
                retriever.getFrameAtTime(ms * 1000, MediaMetadataRetriever.OPTION_CLOSEST)
            }
            if (frames.isEmpty()) throw IOException("가상 영상을 읽지 못했어요")
            var i = 0
            while (currentCoroutineContext().isActive) {
                onFrame(CameraFrame(frames[i % frames.size], null, System.currentTimeMillis()))
                i++
                delay(100)
            }
        } finally {
            retriever.release()
        }
    }
}

// ------------------------------------------------------------------ 네트워크 MJPEG

/**
 * `multipart/x-mixed-replace` MJPEG 스트림(ESP32-CAM `:81/stream`, IP Webcam `/video`, mjpg-streamer 등)이나
 * JPEG 한 장 주소(ESP32-CAM `/capture`)를 읽습니다. 외부 라이브러리 없이 JPEG 시작(FFD8)·끝(FFD9) 표시로 자릅니다.
 */
class MjpegSource(private val url: String) : FrameSource {
    override val description = "네트워크 카메라"

    override suspend fun run(onFrame: (CameraFrame) -> Unit) {
        val target = url.trim().let { if (it.startsWith("http://") || it.startsWith("https://")) it else "http://$it" }
        while (currentCoroutineContext().isActive) {
            val conn = URL(target).openConnection() as HttpURLConnection
            conn.connectTimeout = 5_000
            conn.readTimeout = 10_000
            try {
                val code = conn.responseCode
                if (code !in 200..299) throw IOException("HTTP $code")
                val type = conn.contentType.orEmpty()
                if (type.startsWith("image/")) {
                    // 사진 한 장 주소: 초당 5번 다시 받기
                    val bytes = conn.inputStream.use { it.readBytes() }
                    decode(bytes)?.let { onFrame(CameraFrame(it, bytes, System.currentTimeMillis())) }
                    delay(200)
                } else {
                    readStream(BufferedInputStream(conn.inputStream, 64 * 1024), onFrame)
                }
            } finally {
                conn.disconnect()
            }
        }
    }

    private suspend fun readStream(input: InputStream, onFrame: (CameraFrame) -> Unit) {
        val buf = ByteArrayOutputStream(128 * 1024)
        var inJpeg = false
        var prev = -1
        var lastEmit = 0L
        while (currentCoroutineContext().isActive) {
            val b = input.read()
            if (b < 0) return
            if (!inJpeg) {
                if (prev == 0xFF && b == 0xD8) {
                    inJpeg = true
                    buf.reset()
                    buf.write(0xFF)
                    buf.write(0xD8)
                }
            } else {
                buf.write(b)
                if (prev == 0xFF && b == 0xD9) {
                    inJpeg = false
                    val now = System.currentTimeMillis()
                    if (now - lastEmit >= 66) { // 화면은 초당 15장이면 충분 (나머지는 건너뜀)
                        val bytes = buf.toByteArray()
                        decode(bytes)?.let { onFrame(CameraFrame(it, bytes, now)) }
                        lastEmit = now
                    }
                }
                if (buf.size() > 8 * 1024 * 1024) inJpeg = false // 깨진 스트림 방어
            }
            prev = b
        }
    }

    private fun decode(bytes: ByteArray): Bitmap? = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
}

// ------------------------------------------------------------------ USB 수신기 (UVC)

/**
 * 5.8GHz FPV 수신기(OTG/UVC형)나 아날로그→USB 캡처 장치를 USB 카메라로 읽습니다 (UVCAndroid = libuvc + libusb).
 * 안드로이드 기본 카메라 API는 대부분의 폰에서 외부 USB 카메라를 지원하지 않아서 라이브러리를 씁니다.
 */
class UvcSource(private val app: Application) : FrameSource {
    override val description = "USB 영상 수신기"

    override suspend fun run(onFrame: (CameraFrame) -> Unit) {
        UVCUtils.init(app)
        val main = Handler(Looper.getMainLooper())
        var created: CameraHelper? = null
        try {
        suspendCancellableCoroutine { cont ->
            lateinit var helper: CameraHelper
            val frameCallback = com.serenegiant.usb.IFrameCallback { buffer ->
                // NV21 → JPEG(네이티브로 빠름) → 화면용 비트맵. JPEG는 그대로 Gemma에 보냄
                val size = helper.previewSize ?: return@IFrameCallback
                val nv21 = ByteArray(buffer.remaining()).also { buffer.get(it) }
                val jpeg = ByteArrayOutputStream().use { out ->
                    YuvImage(nv21, ImageFormat.NV21, size.width, size.height, null).compressToJpeg(Rect(0, 0, size.width, size.height), 80, out)
                    out.toByteArray()
                }
                BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size)?.let { onFrame(CameraFrame(it, jpeg, System.currentTimeMillis())) }
            }
            val callback = object : ICameraHelper.StateCallback {
                override fun onAttach(device: UsbDevice) = helper.selectDevice(device)
                override fun onDeviceOpen(device: UsbDevice, isFirstOpen: Boolean) = helper.openCamera()
                override fun onCameraOpen(device: UsbDevice) {
                    helper.setFrameCallback(frameCallback, UVCCamera.PIXEL_FORMAT_NV21)
                    helper.startPreview()
                }
                override fun onCameraClose(device: UsbDevice) = Unit
                override fun onDeviceClose(device: UsbDevice) = Unit
                override fun onDetach(device: UsbDevice) {
                    if (cont.isActive) cont.resume(Unit) // 뽑힘 → 끝
                }
                override fun onCancel(device: UsbDevice) {
                    if (cont.isActive) cont.resume(Unit) // USB 권한 거절
                }
            }
            main.post {
                helper = CameraHelper()
                created = helper
                helper.setStateCallback(callback)
                helper.deviceList?.firstOrNull()?.let { helper.selectDevice(it) }
            }
        }
        } finally {
            main.post { runCatching { created?.release() } } // 끝나든(뽑힘) 취소되든 USB 카메라를 놓음
        }
    }

    companion object {
        /** UVC(영상) 인터페이스가 있는 USB 장치가 꽂혀 있나 */
        fun hasDevice(context: Context): Boolean =
            context.getSystemService(android.hardware.usb.UsbManager::class.java)?.deviceList?.values?.any { d ->
                (0 until d.interfaceCount).any { d.getInterface(it).interfaceClass == android.hardware.usb.UsbConstants.USB_CLASS_VIDEO }
            } == true
    }
}
