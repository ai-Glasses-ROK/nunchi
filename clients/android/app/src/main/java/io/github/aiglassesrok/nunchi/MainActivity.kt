package io.github.aiglassesrok.nunchi

import android.Manifest
import android.content.Intent
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions
import androidx.activity.viewModels
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import io.github.aiglassesrok.nunchi.ui.MainScreen
import kotlin.coroutines.resume
import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    // 1) 안드로이드 권한: 블루투스(안경 연결) + 마이크(질문 녹음)
    private val androidPermissions =
        registerForActivityResult(RequestMultiplePermissions()) { result ->
            if (result[Manifest.permission.BLUETOOTH_CONNECT] == true) {
                // DAT SDK는 블루투스 권한을 받은 뒤 초기화해야 합니다.
                Wearables.initialize(this).onFailure { error, _ -> Log.e(TAG, "DAT 초기화 실패: ${error.description}") }
                viewModel.onSdkInitialized()
            }
        }

    // 2) 안경 권한(카메라): Meta AI 앱으로 잠깐 넘어가 받습니다.
    private var permissionContinuation: CancellableContinuation<PermissionStatus>? = null
    private val permissionMutex = Mutex()
    private val wearablesPermission =
        registerForActivityResult(Wearables.RequestPermissionContract()) { result ->
            permissionContinuation?.resume(result.getOrDefault(PermissionStatus.Denied))
            permissionContinuation = null
        }

    private suspend fun requestWearablesPermission(permission: Permission): PermissionStatus =
        permissionMutex.withLock {
            suspendCancellableCoroutine { cont ->
                permissionContinuation = cont
                cont.invokeOnCancellation { permissionContinuation = null }
                wearablesPermission.launch(permission)
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        viewModel.requestWearablesPermission = ::requestWearablesPermission
        setContent { MainScreen(viewModel = viewModel, activity = this) }
        if (savedInstanceState == null) handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** USB 장치를 꽂아서 열렸으면(usb_device_filter) 바로 연결, 데모 요청이면 가상 드론·카메라 켜기 */
    private fun handleIntent(intent: Intent?) {
        if (intent == null) return
        if (intent.action == UsbManager.ACTION_USB_DEVICE_ATTACHED) {
            val device = if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(UsbManager.EXTRA_DEVICE)
            }
            device?.let(viewModel::onUsbAttached)
        }
        if (intent.getStringExtra("nunchi_demo") == "drone") viewModel.startDemo()
        // 디버그 빌드에서만: adb로 질문을 넣어 시험 (한글은 adb 키 입력이 안 돼서)
        if (BuildConfig.DEBUG) {
            intent.getStringExtra("nunchi_link")?.let { viewModel.connectDrone(it); viewModel.requestDroneScreen() } // usb·bt·udp·tcp·virtual
            intent.getStringExtra("nunchi_camera")?.let { viewModel.startCamera(it) } // usb·mjpeg·virtual
            intent.getStringExtra("nunchi_ask")?.let(viewModel::ask)
            // 드론 화면에서 이 질문으로 분석 (카메라 첫 화면을 기다렸다가). 예: --es nunchi_drone_ask "왜 시동이 안 걸려?"
            intent.getStringExtra("nunchi_drone_ask")?.let { viewModel.requestDroneScreen(); viewModel.analyzeWhenFrameReady(it) }
        }
    }

    override fun onStart() {
        super.onStart()
        androidPermissions.launch(
            arrayOf(Manifest.permission.BLUETOOTH, Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.RECORD_AUDIO),
        )
    }

    companion object {
        private const val TAG = "Nunchi"
    }
}
