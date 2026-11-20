package io.github.aiglassesrok.nunchi.drone

import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 비행컨트롤러 연결 하나를 맡습니다: 읽기 → 파서 → 상태, 1초마다 지상국 하트비트, 데이터 묶음 요청, 끊김 감지.
 * 상태(StateFlow)는 초당 최대 4번만 내보내서 화면이 MAVLink 속도(초당 수십 개)에 휘둘리지 않게 합니다.
 */
class DroneConnection(private val scope: CoroutineScope) {
    private val _state = MutableStateFlow(DroneTelemetry())
    val state: StateFlow<DroneTelemetry> = _state.asStateFlow()

    private var job: Job? = null
    private var link: ByteLink? = null

    val connected: Boolean get() = job?.isActive == true

    fun connect(newLink: ByteLink) {
        disconnect()
        link = newLink
        _state.value = DroneTelemetry(link = newLink.description, linkState = LinkState.CONNECTING)
        job = scope.launch(Dispatchers.IO) { run(newLink) }
    }

    fun disconnect() {
        job?.cancel()
        job = null
        link?.let { runCatching { it.close() } }
        link = null
        _state.update { if (it.linkState == LinkState.DISCONNECTED) it else it.copy(linkState = LinkState.DISCONNECTED, error = "") }
    }

    private suspend fun run(l: ByteLink) {
        val parser = MavlinkParser()
        val writer = MavlinkWriter()
        var t = _state.value
        var lastPublish = 0L
        var published = t
        fun publish(now: Long, force: Boolean = false) {
            if (force || now - lastPublish >= 250 || t.messages.size != published.messages.size || t.events.size != published.events.size ||
                t.messages.lastOrNull() != published.messages.lastOrNull() || t.linkState != published.linkState
            ) {
                _state.value = t
                published = t
                lastPublish = now
            }
        }
        try {
            l.open()
            t = t.copy(linkState = LinkState.WAITING, link = l.description)
            publish(System.currentTimeMillis(), force = true)

            val buf = ByteArray(16 * 1024)
            var lastGcsHeartbeat = 0L
            var targetComp = 1
            var streamsRequestedAt = 0L
            var lastFrameAt = 0L
            while (currentCoroutineContext().isActive) {
                val n = l.read(buf, 200)
                if (n < 0) throw IOException("연결이 끊어졌어요")
                val now = System.currentTimeMillis()
                if (n > 0) {
                    for (f in parser.feed(buf, n)) {
                        lastFrameAt = now
                        val msg = MavMessage.decode(f) ?: continue
                        val before = t.sysId
                        t = t.reduce(msg, f.sysId, now)
                        if (msg is MavMessage.Heartbeat && t.sysId == f.sysId && before == 0) targetComp = f.compId
                    }
                    t = t.copy(frames = parser.framesOk, crcErrors = parser.crcErrors, link = l.description)
                }
                val v2 = parser.lastVersion == 2
                if (now - lastGcsHeartbeat >= 1_000) {
                    runCatching { l.write(writer.heartbeat(v2)) }
                    lastGcsHeartbeat = now
                }
                // 기체를 찾으면(그리고 30초마다 한 번 더) 필요한 메시지를 보내 달라고 요청
                if (t.sysId != 0 && now - streamsRequestedAt > 30_000) {
                    streamsRequestedAt = now
                    runCatching { requestStreams(l, writer, t.sysId, targetComp, t.autopilot, v2) }
                }
                if (t.linkState == LinkState.CONNECTED && now - t.lastHeartbeatMs > HEARTBEAT_TIMEOUT_MS) {
                    t = t.copy(linkState = LinkState.LOST).event(now, "기체 신호 끊김 (${HEARTBEAT_TIMEOUT_MS / 1000}초 동안 하트비트 없음)", severity = 2)
                }
                if (t.linkState == LinkState.WAITING && lastFrameAt == 0L && t.error.isEmpty() && now - streamsRequestedAt > 8_000 && parser.crcErrors > 20) {
                    t = t.copy(error = "MAVLink가 깨져서 들어와요. 보드레이트(57600/115200)를 바꿔 보세요")
                }
                publish(now)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (!currentCoroutineContext().isActive) return // 사용자가 끊어서 생긴 오류는 알리지 않음
            android.util.Log.w("Nunchi", "드론 연결 오류 (${l.description})", e)
            val now = System.currentTimeMillis()
            t = t.copy(linkState = LinkState.DISCONNECTED, error = e.message ?: e.javaClass.simpleName)
                .event(now, "연결 끊김: ${e.message ?: e.javaClass.simpleName}", severity = 3)
            publish(now, force = true)
        } finally {
            runCatching { l.close() }
        }
    }

    private fun requestStreams(l: ByteLink, w: MavlinkWriter, sys: Int, comp: Int, autopilot: Int, v2: Boolean) {
        if (autopilot == MavNames.AUTOPILOT_ARDUPILOT) {
            // ArduPilot: 데이터 묶음 단위로 주기(Hz)를 정함. USB(SERIAL0)는 요청 전에는 거의 아무것도 안 보내는 경우가 많음
            for ((stream, hz) in listOf(
                MavlinkWriter.STREAM_EXTENDED_STATUS to 2,
                MavlinkWriter.STREAM_POSITION to 3,
                MavlinkWriter.STREAM_EXTRA1 to 4,
                MavlinkWriter.STREAM_EXTRA2 to 4,
                MavlinkWriter.STREAM_EXTRA3 to 1,
                MavlinkWriter.STREAM_RC_CHANNELS to 1,
            )) l.write(w.requestDataStream(sys, comp, stream, hz, v2))
        }
        // 홈 위치 한 번 요청 (PX4·ArduPilot 공통 MAV_CMD_REQUEST_MESSAGE, 지원하지 않으면 무시됨)
        l.write(w.commandLong(sys, comp, MavlinkWriter.CMD_REQUEST_MESSAGE, floatArrayOf(MavIds.HOME_POSITION.toFloat()), v2))
    }

    companion object {
        const val HEARTBEAT_TIMEOUT_MS = 5_000L
    }
}
