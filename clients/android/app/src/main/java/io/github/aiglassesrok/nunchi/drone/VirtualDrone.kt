package io.github.aiglassesrok.nunchi.drone

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * 가상 비행컨트롤러 (ArduCopter 쿼드콥터 흉내). 실제 픽스호크처럼 MAVLink v2(끝의 0 바이트 생략)로 말합니다.
 * 100초짜리 시나리오를 반복합니다 — 언제 묻느냐에 따라 답이 달라지는 걸 시험하기 좋게.
 *
 *   0~12초  부팅·GPS 찾는 중              PreArm: Need 3D Fix
 *  12~40초  GPS 3D, 나침반 보정 안 됨     PreArm: Compass not calibrated   ← "왜 시동이 안 걸려?" 시연
 *  40~75초  시동 → LOITER, 15m 이륙 후 원 비행 (배터리 감소)
 *  75~95초  배터리 부족 → RTL 귀환·착륙·시동 꺼짐
 *  95~100초 대기, 다시 처음부터(배터리 교체)
 */
class VirtualDrone(private val homeLat: Double = 37.5487, private val homeLon: Double = 127.1197) {
    private val writer = MavlinkWriter(sysId = 1, compId = 1)
    private val gcsParser = MavlinkParser()
    private var tick = 0L
    private var lastT = -1.0
    private var lastPrearm = -100.0
    private var lastPrearmText: String? = null

    /** 지상국(폰)이 보낸 하트비트 수 — 연결 확인용 */
    var gcsHeartbeats = 0
        private set

    fun onGcsBytes(data: ByteArray) {
        gcsHeartbeats += gcsParser.feed(data).count { it.msgId == MavIds.HEARTBEAT }
    }

    /** 약 100ms마다 불림. 그동안 나올 프레임을 이어 붙여 돌려줍니다. */
    fun step(elapsedMs: Long): ByteArray {
        val t = (elapsedMs % CYCLE_MS) / 1000.0
        if (t < lastT) { lastPrearm = -100.0; lastPrearmText = null } // 새 바퀴
        val s = state(t)
        val out = ByteArrayOutputStream()
        fun send(id: Int, p: ByteBuffer) = out.write(writer.frame(id, p.array(), v2 = true, truncate = true))

        if (tick % 10 == 0L) send(MavIds.HEARTBEAT, heartbeat(s))
        if (tick % 5 == 0L) {
            send(MavIds.SYS_STATUS, sysStatus(s))
            send(MavIds.GPS_RAW_INT, gpsRaw(s, elapsedMs))
        }
        if (tick % 2 == 0L) {
            send(MavIds.GLOBAL_POSITION_INT, globalPosition(s, elapsedMs))
            send(MavIds.ATTITUDE, attitude(s, elapsedMs))
            send(MavIds.VFR_HUD, vfrHud(s))
        }
        if (tick % 10 == 5L) send(MavIds.BATTERY_STATUS, battery(s))
        if (tick % 50 == 25L && s.fix >= 3) send(MavIds.HOME_POSITION, home())

        // 시간표에 따른 STATUSTEXT (지난 호출 이후 지나간 것만)
        for ((at, sev, text) in SCHEDULE) if (at > lastT && at <= t) send(MavIds.STATUSTEXT, statusText(sev, text))
        val prearm = when {
            t < 12 -> "PreArm: Need 3D Fix"
            t < 40 -> "PreArm: Compass not calibrated"
            else -> null
        }
        if (prearm != null && (t - lastPrearm >= 10 || prearm != lastPrearmText)) { // 실제 ArduPilot은 30초마다, 시연용으로 10초마다·바뀌면 바로
            send(MavIds.STATUSTEXT, statusText(4, prearm))
            lastPrearm = t
            lastPrearmText = prearm
        }
        lastT = t
        tick++
        return out.toByteArray()
    }

    private data class S(
        val armed: Boolean,
        val mode: Int,
        val status: Int,
        val fix: Int,
        val sats: Int,
        val lat: Double,
        val lon: Double,
        val relAlt: Double,
        val speed: Double,
        val climb: Double,
        val heading: Double,
        val voltage: Double,
        val pct: Int,
        val currentA: Double,
        val usedMah: Int,
        val prearmOk: Boolean,
    )

    private fun state(t: Double): S {
        val fix = if (t < 12) 1 else 3
        val sats = if (t < 12) (t / 2).toInt().coerceAtMost(6) else 10
        val armed = t in 40.0..94.0
        val mode = when {
            t < 41 -> 0 // STABILIZE
            t < 75 -> 5 // LOITER
            t < 96 -> 6 // RTL
            else -> 0
        }
        // 비행 경로: 이륙(40~48s) → 반지름 20m 원(48~75s) → 귀환(75~85s) → 하강(85~93s)
        val radius = 20.0
        var north = 0.0
        var east = 0.0
        var alt = 0.0
        var speed = 0.0
        var climb = 0.0
        var heading = 90.0
        when {
            t in 40.0..48.0 -> { alt = (t - 40) / 8 * 15; climb = 15 / 8.0 }
            t in 48.0..75.0 -> {
                val theta = (t - 48) * 4.0 / radius
                north = radius * cos(theta) - radius
                east = radius * sin(theta)
                alt = 15.0
                speed = 4.0
                heading = Math.toDegrees(theta + PI / 2)
            }
            t in 75.0..85.0 -> {
                val theta = 27 * 4.0 / radius
                val k = 1 - (t - 75) / 10
                north = (radius * cos(theta) - radius) * k
                east = radius * sin(theta) * k
                alt = 15.0
                speed = 3.0
                heading = Math.toDegrees(atan2(-east, -north))
            }
            t in 85.0..93.0 -> { alt = 15 * (1 - (t - 85) / 8); climb = -15 / 8.0 }
        }
        // 배터리: 이륙~75초에 98%→28%(14.9V, 'Battery 1 is low' 시점과 맞춤), 귀환·착륙 동안 22%까지
        val cruise = (t.coerceIn(40.0, 75.0) - 40) / 35
        val back = (t.coerceIn(75.0, 94.0) - 75) / 19
        val voltage = 16.6 - 1.7 * cruise - 0.3 * back
        val pct = (98 - 70 * cruise - 6 * back).roundToInt()
        val flying = cruise * 0.9 + back * 0.1 // 사용량(mAh) 비율: 75초에 1850mAh
        return S(
            armed = armed, mode = mode, status = if (armed) 4 else 3, fix = fix, sats = sats,
            lat = homeLat + north / 111_320.0,
            lon = homeLon + east / (111_320.0 * cos(Math.toRadians(homeLat))),
            relAlt = alt, speed = speed, climb = climb, heading = (heading % 360 + 360) % 360,
            voltage = voltage, pct = pct, currentA = if (armed) 11.5 else 0.4, usedMah = (2055 * flying).roundToInt(),
            prearmOk = t >= 40,
        )
    }

    private fun le(n: Int) = ByteBuffer.allocate(n).order(ByteOrder.LITTLE_ENDIAN)

    private fun heartbeat(s: S) = le(9).apply {
        putInt(0, s.mode)
        put(4, 2) // MAV_TYPE_QUADROTOR
        put(5, 3) // MAV_AUTOPILOT_ARDUPILOTMEGA
        put(6, (0x51 or if (s.armed) 0x80 else 0).toByte())
        put(7, s.status.toByte())
        put(8, 3)
    }

    private fun sysStatus(s: S) = le(31).apply {
        val all = 0x01 or 0x02 or 0x04 or 0x08 or 0x20 or 0x10000 or 0x200000 or 0x1000000 or 0x2000000 or 0x10000000
        var health = all
        if (s.fix < 3) health = health and 0x20.inv()
        if (!s.prearmOk) health = health and 0x10000000.inv()
        putInt(0, all); putInt(4, all); putInt(8, health)
        putShort(12, 250)
        putShort(14, (s.voltage * 1000).roundToInt().toShort())
        putShort(16, (s.currentA * 100).roundToInt().toShort())
        put(30, s.pct.toByte())
    }

    private fun gpsRaw(s: S, ms: Long) = le(30).apply {
        putLong(0, ms * 1000)
        putInt(8, (s.lat * 1e7).roundToInt()); putInt(12, (s.lon * 1e7).roundToInt())
        putInt(16, ((HOME_ALT + s.relAlt) * 1000).roundToInt())
        putShort(20, (if (s.fix >= 3) 90 else 0xFFFF).toShort())
        putShort(22, 120)
        putShort(24, (s.speed * 100).roundToInt().toShort())
        putShort(26, (s.heading * 100).roundToInt().toShort())
        put(28, s.fix.toByte()); put(29, s.sats.toByte())
    }

    private fun globalPosition(s: S, ms: Long) = le(28).apply {
        putInt(0, ms.toInt())
        if (s.fix >= 3) { putInt(4, (s.lat * 1e7).roundToInt()); putInt(8, (s.lon * 1e7).roundToInt()) }
        putInt(12, ((HOME_ALT + s.relAlt) * 1000).roundToInt())
        putInt(16, (s.relAlt * 1000).roundToInt())
        val rad = Math.toRadians(s.heading)
        putShort(20, (s.speed * cos(rad) * 100).roundToInt().toShort())
        putShort(22, (s.speed * sin(rad) * 100).roundToInt().toShort())
        putShort(24, (-s.climb * 100).roundToInt().toShort())
        putShort(26, (s.heading * 100).roundToInt().toShort())
    }

    private fun attitude(s: S, ms: Long) = le(28).apply {
        putInt(0, ms.toInt())
        val wobble = sin(ms / 700.0) * 0.02
        putFloat(4, (wobble + if (s.speed > 0) 0.08 else 0.0).toFloat()) // 원 비행 중 살짝 기울어짐
        putFloat(8, (wobble - if (s.speed > 0) 0.05 else 0.0).toFloat())
        putFloat(12, Math.toRadians(if (s.heading > 180) s.heading - 360 else s.heading).toFloat())
    }

    private fun vfrHud(s: S) = le(20).apply {
        putFloat(0, s.speed.toFloat()); putFloat(4, s.speed.toFloat())
        putFloat(8, (HOME_ALT + s.relAlt).toFloat()); putFloat(12, s.climb.toFloat())
        putShort(16, s.heading.roundToInt().toShort())
        putShort(18, (if (s.armed) 45 else 0).toShort())
    }

    private fun battery(s: S) = le(36).apply {
        putInt(0, s.usedMah); putInt(4, -1)
        putShort(8, Short.MAX_VALUE)
        for (i in 0 until 10) putShort(10 + i * 2, (if (i < 4) (s.voltage / 4 * 1000).roundToInt() else 0xFFFF).toShort())
        putShort(30, (s.currentA * 100).roundToInt().toShort())
        put(32, 0); put(33, 1); put(34, 1); put(35, s.pct.toByte())
    }

    private fun home() = le(52).apply {
        putInt(0, (homeLat * 1e7).roundToInt()); putInt(4, (homeLon * 1e7).roundToInt())
        putInt(8, (HOME_ALT * 1000).roundToInt())
        putFloat(24, 1f)
    }

    private fun statusText(severity: Int, text: String) = le(51).apply {
        put(0, severity.toByte())
        text.toByteArray(Charsets.UTF_8).take(50).forEachIndexed { i, b -> put(1 + i, b) }
    }

    companion object {
        const val CYCLE_MS = 100_000L
        private const val HOME_ALT = 32.0 // 해발 고도(m)

        /** (초, 심각도, 문구) — 실제 ArduPilot이 보내는 형식을 흉내 낸 예시 */
        private val SCHEDULE = listOf(
            Triple(0.3, 6, "ArduCopter V4.5.7 (virtual)"),
            Triple(0.6, 6, "Frame: QUAD/X"),
            Triple(14.0, 6, "EKF3 IMU0 is using GPS"),
            Triple(75.0, 4, "Battery 1 is low 14.90V used 1850 mAh"),
        )
    }
}
