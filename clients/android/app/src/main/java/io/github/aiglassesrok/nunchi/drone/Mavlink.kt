package io.github.aiglassesrok.nunchi.drone

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 최소 MAVLink v1/v2 구현 — 픽스호크(ArduPilot·PX4) 비행컨트롤러의 상태를 읽는 데 필요한 메시지만 다룹니다.
 *
 * 왜 직접 구현했나: 필요한 건 메시지 10여 개의 '읽기'뿐이라 수백 개 메시지를 담은 라이브러리 대신
 * 200줄 남짓으로 끝냅니다 (HubClient가 외부 라이브러리 없이 HTTP를 다루는 것과 같은 방침).
 * 정확성은 테스트에서 공식 XML로 만든 별도 구현(dronefleet)과 바이트 단위로 교차 검증합니다.
 *
 * 프레임 구조
 *   v1: FE len seq sys comp msgid(1)          payload  crc(2)
 *   v2: FD len incompat compat seq sys comp msgid(3) payload crc(2) [서명 13]
 *   crc = X.25(헤더의 len부터 payload 끝까지 + 메시지별 CRC_EXTRA 1바이트)
 */
object MavCrc {
    fun accumulate(byte: Int, crc: Int): Int {
        var tmp = (byte xor crc) and 0xFF
        tmp = (tmp xor (tmp shl 4)) and 0xFF
        return ((crc ushr 8) xor (tmp shl 8) xor (tmp shl 3) xor (tmp ushr 4)) and 0xFFFF
    }

    fun compute(bytes: ByteArray, from: Int, until: Int, extra: Int): Int {
        var crc = 0xFFFF
        for (i in from until until) crc = accumulate(bytes[i].toInt() and 0xFF, crc)
        return accumulate(extra, crc)
    }
}

/** 메시지 번호 → (CRC_EXTRA, 기본 필드 길이). 표에 없는 메시지는 CRC를 확인할 수 없어 건너뜁니다. */
object MavIds {
    const val HEARTBEAT = 0
    const val SYS_STATUS = 1
    const val SYSTEM_TIME = 2
    const val GPS_RAW_INT = 24
    const val SCALED_PRESSURE = 29
    const val ATTITUDE = 30
    const val GLOBAL_POSITION_INT = 33
    const val MISSION_CURRENT = 42
    const val RC_CHANNELS = 65
    const val REQUEST_DATA_STREAM = 66
    const val VFR_HUD = 74
    const val COMMAND_LONG = 76
    const val COMMAND_ACK = 77
    const val POWER_STATUS = 125
    const val BATTERY_STATUS = 147
    const val EKF_STATUS_REPORT = 193
    const val HOME_POSITION = 242
    const val STATUSTEXT = 253

    /** common.xml / ardupilotmega.xml 기준. 테스트(crcExtraMatchesFieldDefinitions)에서 필드 정의로 다시 계산해 확인합니다. */
    val SPECS: Map<Int, Pair<Int, Int>> = mapOf(
        HEARTBEAT to (50 to 9),
        SYS_STATUS to (124 to 31),
        SYSTEM_TIME to (137 to 12),
        GPS_RAW_INT to (24 to 30),
        SCALED_PRESSURE to (115 to 14),
        ATTITUDE to (39 to 28),
        GLOBAL_POSITION_INT to (104 to 28),
        MISSION_CURRENT to (28 to 2),
        RC_CHANNELS to (118 to 42),
        REQUEST_DATA_STREAM to (148 to 6),
        VFR_HUD to (20 to 20),
        COMMAND_LONG to (152 to 33),
        COMMAND_ACK to (143 to 3),
        POWER_STATUS to (203 to 6),
        BATTERY_STATUS to (154 to 36),
        EKF_STATUS_REPORT to (71 to 22),
        HOME_POSITION to (104 to 52),
        STATUSTEXT to (83 to 51),
    )
}

class MavFrame(
    val version: Int,
    val seq: Int,
    val sysId: Int,
    val compId: Int,
    val msgId: Int,
    val payload: ByteArray,
)

/**
 * 바이트가 조각조각 들어와도(USB·블루투스·UDP) 프레임을 복원하는 스트림 파서.
 * CRC가 틀리면 그 시작 바이트 하나만 버리고 다시 찾습니다(무선 링크의 깨진 바이트에 강함).
 */
class MavlinkParser {
    private var buf = ByteArray(4096)
    private var len = 0

    var framesOk = 0L
        private set
    var crcErrors = 0L
        private set
    var unknownFrames = 0L
        private set
    /** 기체가 마지막으로 보낸 프레임의 버전 (보낼 때 같은 버전을 씀) */
    var lastVersion = 0
        private set

    fun feed(data: ByteArray, count: Int = data.size): List<MavFrame> {
        if (count <= 0) return emptyList()
        if (len + count > buf.size) buf = buf.copyOf(maxOf(buf.size * 2, len + count))
        System.arraycopy(data, 0, buf, len, count)
        len += count

        val out = ArrayList<MavFrame>()
        var i = 0
        while (true) {
            while (i < len && !isStx(buf[i])) i++
            if (i >= len) break
            val v2 = buf[i] == STX_V2
            val headerLen = if (v2) 10 else 6
            if (len - i < headerLen) break
            val payloadLen = u8(i + 1)
            val incompat = if (v2) u8(i + 2) else 0
            if (incompat and 0x01.inv() != 0) { // 모르는 호환 불가 플래그 → 진짜 프레임이 아닐 가능성이 큼
                i++
                continue
            }
            val frameLen = headerLen + payloadLen + 2 + if (incompat and 0x01 != 0) 13 else 0
            if (len - i < frameLen) break // 나머지가 올 때까지 기다림

            val msgId = if (v2) u8(i + 7) or (u8(i + 8) shl 8) or (u8(i + 9) shl 16) else u8(i + 5)
            val spec = MavIds.SPECS[msgId]
            if (spec == null) {
                // CRC를 확인할 수 없는 메시지: 다음 바이트가 새 프레임의 시작이면 프레임으로 인정하고 통째로 건너뜀
                if (len - i == frameLen || isStx(buf[i + frameLen])) {
                    unknownFrames++
                    i += frameLen
                } else {
                    i++
                }
                continue
            }
            val crcAt = i + headerLen + payloadLen
            val crc = MavCrc.compute(buf, i + 1, crcAt, spec.first)
            if (crc != (u8(crcAt) or (u8(crcAt + 1) shl 8))) {
                crcErrors++
                i++
                continue
            }
            out += MavFrame(
                version = if (v2) 2 else 1,
                seq = u8(i + if (v2) 4 else 2),
                sysId = u8(i + if (v2) 5 else 3),
                compId = u8(i + if (v2) 6 else 4),
                msgId = msgId,
                payload = buf.copyOfRange(i + headerLen, crcAt),
            )
            lastVersion = if (v2) 2 else 1
            framesOk++
            i += frameLen
        }
        System.arraycopy(buf, i, buf, 0, len - i)
        len -= i
        if (len > MAX_PENDING) len = 0 // 프레임이 전혀 안 맞는 잡음이 쌓이면 비움
        return out
    }

    private fun u8(at: Int) = buf[at].toInt() and 0xFF

    companion object {
        const val STX_V1: Byte = 0xFE.toByte()
        const val STX_V2: Byte = 0xFD.toByte()
        private const val MAX_PENDING = 64 * 1024
        fun isStx(b: Byte) = b == STX_V1 || b == STX_V2
    }
}

/** 해석한 메시지 (필요한 필드만) */
sealed interface MavMessage {
    data class Heartbeat(
        val type: Int,
        val autopilot: Int,
        val baseMode: Int,
        val customMode: Long,
        val systemStatus: Int,
    ) : MavMessage {
        val armed: Boolean get() = baseMode and 0x80 != 0
    }

    data class SysStatus(
        val sensorsPresent: Long,
        val sensorsEnabled: Long,
        val sensorsHealth: Long,
        val voltageMv: Int,
        val currentCa: Int,
        val batteryRemaining: Int,
        val dropRateComm: Int,
    ) : MavMessage

    data class GpsRaw(
        val fixType: Int,
        val lat: Int,
        val lon: Int,
        val altMm: Int,
        val eph: Int,
        val velCms: Int,
        val satellites: Int,
    ) : MavMessage

    data class Attitude(val roll: Float, val pitch: Float, val yaw: Float) : MavMessage

    data class GlobalPosition(
        val lat: Int,
        val lon: Int,
        val altMm: Int,
        val relAltMm: Int,
        val vxCms: Int,
        val vyCms: Int,
        val vzCms: Int,
        val hdgCdeg: Int,
    ) : MavMessage

    data class VfrHud(
        val airspeed: Float,
        val groundspeed: Float,
        val heading: Int,
        val throttle: Int,
        val alt: Float,
        val climb: Float,
    ) : MavMessage

    data class BatteryStatus(
        val id: Int,
        val cellsMv: List<Int>,
        val currentCa: Int,
        val consumedMah: Int,
        val remaining: Int,
    ) : MavMessage

    data class StatusText(val severity: Int, val text: String) : MavMessage

    data class HomePosition(val lat: Int, val lon: Int, val altMm: Int) : MavMessage

    data class RcChannels(val chanCount: Int, val rssi: Int, val throttle: Int) : MavMessage

    data class CommandAck(val command: Int, val result: Int) : MavMessage

    companion object {
        /** 프레임 → 메시지. v2는 끝의 0 바이트를 잘라 보내므로 기본 길이만큼 0으로 채워서 읽습니다. */
        fun decode(f: MavFrame): MavMessage? {
            val baseLen = MavIds.SPECS[f.msgId]?.second ?: return null
            val p = ByteBuffer.wrap(if (f.payload.size >= baseLen) f.payload else f.payload.copyOf(baseLen)).order(ByteOrder.LITTLE_ENDIAN)
            fun u8(at: Int) = p.get(at).toInt() and 0xFF
            fun i8(at: Int) = p.get(at).toInt()
            fun u16(at: Int) = p.getShort(at).toInt() and 0xFFFF
            fun i16(at: Int) = p.getShort(at).toInt()
            fun u32(at: Int) = p.getInt(at).toLong() and 0xFFFFFFFFL
            return when (f.msgId) {
                MavIds.HEARTBEAT -> Heartbeat(type = u8(4), autopilot = u8(5), baseMode = u8(6), customMode = u32(0), systemStatus = u8(7))
                MavIds.SYS_STATUS -> SysStatus(
                    sensorsPresent = u32(0), sensorsEnabled = u32(4), sensorsHealth = u32(8),
                    voltageMv = u16(14), currentCa = i16(16), batteryRemaining = i8(30), dropRateComm = u16(18),
                )
                MavIds.GPS_RAW_INT -> GpsRaw(
                    fixType = u8(28), lat = p.getInt(8), lon = p.getInt(12), altMm = p.getInt(16),
                    eph = u16(20), velCms = u16(24), satellites = u8(29),
                )
                MavIds.ATTITUDE -> Attitude(roll = p.getFloat(4), pitch = p.getFloat(8), yaw = p.getFloat(12))
                MavIds.GLOBAL_POSITION_INT -> GlobalPosition(
                    lat = p.getInt(4), lon = p.getInt(8), altMm = p.getInt(12), relAltMm = p.getInt(16),
                    vxCms = i16(20), vyCms = i16(22), vzCms = i16(24), hdgCdeg = u16(26),
                )
                MavIds.VFR_HUD -> VfrHud(
                    airspeed = p.getFloat(0), groundspeed = p.getFloat(4), alt = p.getFloat(8), climb = p.getFloat(12),
                    heading = i16(16), throttle = u16(18),
                )
                MavIds.BATTERY_STATUS -> BatteryStatus(
                    id = u8(32),
                    cellsMv = (0 until 10).map { u16(10 + it * 2) }.filter { it != 0xFFFF && it != 0 },
                    currentCa = i16(30), consumedMah = p.getInt(0), remaining = i8(35),
                )
                MavIds.STATUSTEXT -> {
                    val raw = ByteArray(50).also { p.position(1); p.get(it) }
                    val end = raw.indexOf(0).let { if (it < 0) raw.size else it }
                    StatusText(severity = u8(0), text = String(raw, 0, end, Charsets.UTF_8).trim())
                }
                MavIds.HOME_POSITION -> HomePosition(lat = p.getInt(0), lon = p.getInt(4), altMm = p.getInt(8))
                MavIds.RC_CHANNELS -> RcChannels(chanCount = u8(40), rssi = u8(41), throttle = u16(8))
                MavIds.COMMAND_ACK -> CommandAck(command = u16(0), result = u8(2))
                else -> null // CRC 확인용으로만 아는 메시지 (SYSTEM_TIME 등)
            }
        }
    }
}

/**
 * 폰(지상국, GCS) → 기체로 보내는 메시지. 기체가 v1로 말하면 v1, v2면 v2로 맞춰 보냅니다.
 * 지상국 번호는 미션플래너·QGC와 같은 관례(sys 255, comp 190)를 씁니다.
 */
class MavlinkWriter(private val sysId: Int = 255, private val compId: Int = 190) {
    private var seq = 0

    /** truncate=true 면 v2 규칙대로 payload 끝의 0 바이트를 잘라 보냅니다(실제 ArduPilot·PX4가 이렇게 보냄). */
    fun frame(msgId: Int, fullPayload: ByteArray, v2: Boolean, truncate: Boolean = false): ByteArray {
        val extra = MavIds.SPECS.getValue(msgId).first
        var size = fullPayload.size
        if (v2 && truncate) while (size > 1 && fullPayload[size - 1] == 0.toByte()) size--
        val payload = if (size == fullPayload.size) fullPayload else fullPayload.copyOf(size)
        val headerLen = if (v2) 10 else 6
        val out = ByteArray(headerLen + payload.size + 2)
        out[0] = if (v2) MavlinkParser.STX_V2 else MavlinkParser.STX_V1
        out[1] = payload.size.toByte()
        if (v2) {
            out[2] = 0 // incompat flags (서명 없음)
            out[3] = 0 // compat flags
            out[4] = seq.toByte(); out[5] = sysId.toByte(); out[6] = compId.toByte()
            out[7] = msgId.toByte(); out[8] = (msgId shr 8).toByte(); out[9] = (msgId shr 16).toByte()
        } else {
            out[2] = seq.toByte(); out[3] = sysId.toByte(); out[4] = compId.toByte(); out[5] = msgId.toByte()
        }
        seq = (seq + 1) and 0xFF
        System.arraycopy(payload, 0, out, headerLen, payload.size)
        val crc = MavCrc.compute(out, 1, headerLen + payload.size, extra)
        out[headerLen + payload.size] = crc.toByte()
        out[headerLen + payload.size + 1] = (crc shr 8).toByte()
        return out
    }

    /** 지상국 하트비트 (1초마다). type=6 GCS, autopilot=8 INVALID, system_status=4 ACTIVE */
    fun heartbeat(v2: Boolean): ByteArray {
        val p = le(9).putInt(0, 0)
        p.put(4, 6).put(5, 8).put(6, 0).put(7, 4).put(8, 3)
        return frame(MavIds.HEARTBEAT, p.array(), v2)
    }

    /** ArduPilot에 데이터 묶음(stream)을 초당 rateHz번 보내 달라고 요청 */
    fun requestDataStream(targetSys: Int, targetComp: Int, streamId: Int, rateHz: Int, v2: Boolean): ByteArray {
        val p = le(6).putShort(0, rateHz.toShort())
        p.put(2, targetSys.toByte()).put(3, targetComp.toByte()).put(4, streamId.toByte()).put(5, 1)
        return frame(MavIds.REQUEST_DATA_STREAM, p.array(), v2)
    }

    fun commandLong(targetSys: Int, targetComp: Int, command: Int, params: FloatArray, v2: Boolean): ByteArray {
        val p = le(33)
        for (i in 0 until 7) p.putFloat(i * 4, params.getOrElse(i) { 0f })
        p.putShort(28, command.toShort()).put(30, targetSys.toByte()).put(31, targetComp.toByte()).put(32, 0)
        return frame(MavIds.COMMAND_LONG, p.array(), v2)
    }

    private fun le(n: Int) = ByteBuffer.allocate(n).order(ByteOrder.LITTLE_ENDIAN)

    companion object {
        // MAV_DATA_STREAM (ArduPilot은 아직 이 방식으로 주기를 정합니다)
        const val STREAM_EXTENDED_STATUS = 2 // SYS_STATUS, GPS_RAW_INT, …
        const val STREAM_RC_CHANNELS = 3
        const val STREAM_POSITION = 6 // GLOBAL_POSITION_INT
        const val STREAM_EXTRA1 = 10 // ATTITUDE
        const val STREAM_EXTRA2 = 11 // VFR_HUD
        const val STREAM_EXTRA3 = 12 // BATTERY_STATUS, SYSTEM_TIME, …

        const val CMD_SET_MESSAGE_INTERVAL = 511
        const val CMD_REQUEST_MESSAGE = 512
    }
}

/** 사람이 읽는 이름들 (한국어 설명 포함) */
object MavNames {
    const val AUTOPILOT_ARDUPILOT = 3
    const val AUTOPILOT_PX4 = 12

    private val COPTER_TYPES = setOf(2, 3, 4, 13, 14, 15, 29, 35)
    private val PLANE_TYPES = setOf(1, 16, 19, 20, 21, 22, 23, 24, 25)
    private val ROVER_TYPES = setOf(10, 11)

    fun vehicle(type: Int): String = when (type) {
        1 -> "고정익"
        2 -> "쿼드콥터"
        3 -> "동축 콥터"
        4 -> "헬리콥터"
        10 -> "로버"
        11 -> "보트"
        12 -> "수중 드론"
        13 -> "헥사콥터"
        14 -> "옥토콥터"
        15 -> "트라이콥터"
        in 19..25 -> "VTOL"
        else -> "기체(type $type)"
    }

    fun autopilot(a: Int): String = when (a) {
        AUTOPILOT_ARDUPILOT -> "ArduPilot"
        AUTOPILOT_PX4 -> "PX4"
        else -> "autopilot $a"
    }

    private val COPTER = mapOf(
        0 to "STABILIZE", 1 to "ACRO", 2 to "ALT_HOLD", 3 to "AUTO", 4 to "GUIDED", 5 to "LOITER", 6 to "RTL",
        7 to "CIRCLE", 9 to "LAND", 11 to "DRIFT", 13 to "SPORT", 14 to "FLIP", 15 to "AUTOTUNE", 16 to "POSHOLD",
        17 to "BRAKE", 18 to "THROW", 19 to "AVOID_ADSB", 20 to "GUIDED_NOGPS", 21 to "SMART_RTL", 22 to "FLOWHOLD",
        23 to "FOLLOW", 24 to "ZIGZAG", 25 to "SYSTEMID", 26 to "AUTOROTATE", 27 to "AUTO_RTL", 28 to "TURTLE",
    )
    private val PLANE = mapOf(
        0 to "MANUAL", 1 to "CIRCLE", 2 to "STABILIZE", 3 to "TRAINING", 4 to "ACRO", 5 to "FBWA", 6 to "FBWB",
        7 to "CRUISE", 8 to "AUTOTUNE", 10 to "AUTO", 11 to "RTL", 12 to "LOITER", 13 to "TAKEOFF", 14 to "AVOID_ADSB",
        15 to "GUIDED", 17 to "QSTABILIZE", 18 to "QHOVER", 19 to "QLOITER", 20 to "QLAND", 21 to "QRTL",
        22 to "QAUTOTUNE", 23 to "QACRO", 24 to "THERMAL", 25 to "LOITER_ALT_QLAND",
    )
    private val ROVER = mapOf(
        0 to "MANUAL", 1 to "ACRO", 3 to "STEERING", 4 to "HOLD", 5 to "LOITER", 6 to "FOLLOW", 7 to "SIMPLE",
        8 to "DOCK", 9 to "CIRCLE", 10 to "AUTO", 11 to "RTL", 12 to "SMART_RTL", 15 to "GUIDED",
    )
    private val PX4_MAIN = mapOf(1 to "MANUAL", 2 to "ALTCTL", 3 to "POSCTL", 4 to "AUTO", 5 to "ACRO", 6 to "OFFBOARD", 7 to "STABILIZED", 8 to "RATTITUDE")
    private val PX4_AUTO = mapOf(1 to "AUTO_READY", 2 to "TAKEOFF", 3 to "LOITER", 4 to "MISSION", 5 to "RTL", 6 to "LAND", 8 to "FOLLOW_TARGET", 9 to "PRECLAND")

    /** 비행 모드 이름. ArduPilot은 기체 종류마다 번호 뜻이 다르고, PX4는 custom_mode 안에 main/sub 모드가 들어 있습니다. */
    fun mode(autopilot: Int, type: Int, customMode: Long): String {
        val n = customMode.toInt()
        return when (autopilot) {
            AUTOPILOT_ARDUPILOT -> when (type) {
                in COPTER_TYPES -> COPTER[n]
                in PLANE_TYPES -> PLANE[n]
                in ROVER_TYPES -> ROVER[n]
                else -> null
            } ?: "MODE $n"
            AUTOPILOT_PX4 -> {
                val main = (n shr 16) and 0xFF
                val sub = (n shr 24) and 0xFF
                if (main == 4) PX4_AUTO[sub] ?: "AUTO" else PX4_MAIN[main] ?: "MODE $n"
            }
            else -> "MODE $n"
        }
    }

    private val MODE_KO = mapOf(
        "STABILIZE" to "자세 유지 수동", "ACRO" to "곡예 수동", "ALT_HOLD" to "고도 유지", "AUTO" to "자동 임무",
        "GUIDED" to "유도", "LOITER" to "제자리 위치 유지", "RTL" to "출발점 귀환", "SMART_RTL" to "왔던 길로 귀환",
        "CIRCLE" to "원 선회", "LAND" to "착륙", "POSHOLD" to "위치 유지", "BRAKE" to "급정지", "AUTOTUNE" to "자동 튜닝",
        "MANUAL" to "수동", "FBWA" to "보조 수동", "TAKEOFF" to "이륙", "HOLD" to "정지", "QLAND" to "수직 착륙",
        "ALTCTL" to "고도 유지", "POSCTL" to "위치 유지", "MISSION" to "자동 임무", "STABILIZED" to "자세 유지 수동",
    )

    fun modeKo(mode: String): String = MODE_KO[mode]?.let { "$mode($it)" } ?: mode

    fun systemStatus(s: Int): String = when (s) {
        0 -> "초기화 전"
        1 -> "부팅 중"
        2 -> "보정 중"
        3 -> "대기"
        4 -> "동작 중"
        5 -> "위험"
        6 -> "비상"
        7 -> "전원 끄는 중"
        8 -> "비행 종료"
        else -> "상태 $s"
    }

    fun gpsFix(f: Int): String = when (f) {
        0 -> "GPS 없음"
        1 -> "위치 못 잡음"
        2 -> "2D 위치"
        3 -> "3D 위치"
        4 -> "3D 위치(DGPS)"
        5 -> "RTK Float"
        6 -> "RTK Fixed"
        7 -> "고정 위치"
        8 -> "PPP"
        else -> "fix $f"
    }

    fun severity(s: Int): String = when (s) {
        0 -> "비상"
        1 -> "경보"
        2 -> "치명"
        3 -> "오류"
        4 -> "경고"
        5 -> "알림"
        6 -> "정보"
        else -> "디버그"
    }

    /** SYS_STATUS 센서 비트 → 이름 (검색어로도 쓰이므로 문서와 같은 낱말을 씁니다) */
    val SENSORS: List<Pair<Long, String>> = listOf(
        0x01L to "자이로",
        0x02L to "가속도계",
        0x04L to "나침반(컴퍼스)",
        0x08L to "기압계",
        0x20L to "GPS",
        0x10000L to "RC 수신기",
        0x200000L to "AHRS(자세 추정)",
        0x1000000L to "로깅(SD 카드)",
        0x2000000L to "배터리",
        0x10000000L to "시동 전 점검(PreArm)",
    )
}
