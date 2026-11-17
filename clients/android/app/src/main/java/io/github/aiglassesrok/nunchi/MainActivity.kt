package io.github.aiglassesrok.nunchi

import android.Manifest
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
