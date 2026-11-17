package io.github.aiglassesrok.nunchi

import android.app.Activity
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import com.meta.wearable.dat.camera.addCamera
import com.meta.wearable.dat.camera.types.PhotoData
import com.meta.wearable.dat.camera.types.StreamConfiguration
import com.meta.wearable.dat.camera.types.StreamState
import com.meta.wearable.dat.camera.types.VideoQuality
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import com.meta.wearable.dat.core.types.RegistrationState
import com.meta.wearable.dat.mockdevice.MockDeviceKit
import com.meta.wearable.dat.mockdevice.api.GlassesModel
import java.io.ByteArrayOutputStream
import java.io.File
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeout

/**
 * Meta Wearables DAT 래퍼: 등록 상태 → 세션 → 카메라 → 사진 한 장(JPEG).
 * 배터리·발열을 아끼려고 사진을 찍을 때만 세션과 카메라를 켜고, 찍고 나면 끕니다.
 */
class Glasses(private val context: Context) {

    fun registrationText(state: RegistrationState): String = when (state) {
        RegistrationState.REGISTERED -> "연결됨"
        RegistrationState.REGISTERING -> "Meta AI 앱에서 승인 중…"
        RegistrationState.AVAILABLE -> "연결 전"
        RegistrationState.UNREGISTERING -> "연결 해제 중"
        else -> "사용 불가 (Meta AI 앱·개발자 모드 확인)"
    }

    fun register(activity: Activity) = Wearables.startRegistration(activity)

    fun unregister(activity: Activity) = Wearables.startUnregistration(activity)

    /**
     * 사진 한 장 찍기.
     * @param requestPermission Meta AI 앱으로 넘어가 카메라 권한을 받는 함수 (MainActivity가 제공)
     */
    suspend fun capturePhoto(requestPermission: suspend (Permission) -> PermissionStatus): ByteArray {
        // DatResult의 콜백 안에서는 suspend 함수를 부르지 않도록, 먼저 값만 꺼내고 바깥에서 기다립니다.

        // 1) 카메라 권한 (처음 한 번 Meta AI 앱에서 '항상 허용' 권장)
        val status = Wearables.checkPermissionStatus(Permission.CAMERA).fold(
            onSuccess = { it },
            onFailure = { error, _ -> throw IllegalStateException("권한 확인 실패: ${error.description}") },
        )
        if (status != PermissionStatus.Granted && requestPermission(Permission.CAMERA) != PermissionStatus.Granted) {
            throw IllegalStateException("안경 카메라 권한이 없어요. Meta AI 앱에서 이 앱의 카메라를 허용해 주세요.")
        }

        // 2) 안경이 '연결된 기기'로 나타날 때까지 대기
        withTimeout(10_000) { Wearables.devices.first { it.isNotEmpty() } }

        // 3) 세션 시작
        val session: DeviceSession = Wearables.createSession(AutoDeviceSelector()).fold(
            onSuccess = { it },
            onFailure = { error, _ -> throw IllegalStateException("세션을 만들지 못했어요: ${error.description}") },
        )
        try {
            session.start()
            withTimeout(15_000) { session.state.first { it == DeviceSessionState.STARTED } }

            // 4) 카메라 붙이고 스트리밍 시작 (낮은 프레임이 사진 한 장 화질엔 오히려 유리)
            val camera = session.addCamera(StreamConfiguration(videoQuality = VideoQuality.MEDIUM, frameRate = 15)).fold(
                onSuccess = { it },
                onFailure = { error, _ -> throw IllegalStateException("카메라를 붙이지 못했어요: ${error.description}") },
            )
            try {
                camera.stream.start().onFailure { error, _ -> throw IllegalStateException("스트림 시작 실패: ${error.description}") }
                withTimeout(12_000) { camera.stream.state.first { it == StreamState.STREAMING } }

                // 5) 사진 촬영
                val photo = camera.stream.capturePhoto().fold(
                    onSuccess = { it },
                    onFailure = { error, _ -> throw IllegalStateException("사진 촬영 실패: ${error.description}") },
                )
                return toJpeg(photo)
            } finally {
                camera.stop()
            }
        } finally {
            session.stop() // 세션 종료 → 다음 촬영 때 새로 만듦 (가장 단순하고 안정적인 방식)
        }
    }

    private fun toJpeg(photo: PhotoData): ByteArray {
        val bitmap: Bitmap = when (photo) {
            is PhotoData.Bitmap -> photo.bitmap
            is PhotoData.HEIC -> {
                val buf = photo.data
                val bytes = ByteArray(buf.remaining()).also { buf.duplicate().get(it) }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    ?: throw IllegalStateException("HEIC 사진을 열지 못했어요")
            }
            else -> throw IllegalStateException("알 수 없는 사진 형식이에요")
        }
        return ByteArrayOutputStream().use { out ->
            bitmap.compress(Bitmap.CompressFormat.JPEG, 85, out)
            out.toByteArray()
        }
    }

    // ------------------------------------------------------------ 가상 안경 (MockDeviceKit)

    /**
     * 실물 안경 없이 테스트: SDK 안에 가상 레이밴 메타를 만들고 착용 상태로 둡니다.
     * 카메라 영상·사진은 앱에 든 '발전기 계기판(E2)' 예시가 들어갑니다.
     */
    suspend fun enableVirtualGlasses(): String {
        val kit = MockDeviceKit.getInstance(context)
        kit.enable()
        val device = kit.pairGlasses(GlassesModel.RAYBAN_META).getOrThrow()
        device.powerOn()
        device.unfold()
        device.don()
        device.services.camera.setCameraFeed(rawToUri(R.raw.virtual_glasses_feed, "virtual_feed.mp4"))
        device.services.camera.setCapturedImage(rawToUri(R.raw.virtual_glasses_photo, "virtual_photo.jpg"))
        return "가상 레이밴 메타 연결됨"
    }

    fun disableVirtualGlasses() {
        MockDeviceKit.getInstance(context).disable()
    }

    private fun rawToUri(resId: Int, name: String): Uri {
        val file = File(context.cacheDir, name)
        if (!file.exists()) context.resources.openRawResource(resId).use { input -> file.outputStream().use { input.copyTo(it) } }
        return Uri.fromFile(file)
    }
}
