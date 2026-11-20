package io.github.aiglassesrok.nunchi.drone

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.roundToInt

enum class LinkState { DISCONNECTED, CONNECTING, WAITING, CONNECTED, LOST }

/** 시간 + 한 줄. fromVehicle=true 는 기체가 보낸 STATUSTEXT, false 는 앱이 알아챈 변화(모드·시동·GPS…) */
data class DroneEvent(val timeMs: Long, val text: String, val severity: Int = 6, val fromVehicle: Boolean = false)

/**
 * 비행컨트롤러의 '지금 상태' 한 장. MAVLink 메시지가 올 때마다 [reduce]로 새 값을 만듭니다(불변 객체 → Compose 상태로 바로 사용).
 * [report]는 RAG에 넣는 '실시간 문서', [retrievalHint]는 검색어에 붙이는 '상태 키워드'입니다.
 */
data class DroneTelemetry(
    val link: String = "",
    val linkState: LinkState = LinkState.DISCONNECTED,
    val error: String = "",
    val lastHeartbeatMs: Long = 0,
    val sysId: Int = 0,
    val autopilot: Int = -1,
    val vehicleType: Int = -1,
    val mode: String = "",
    val armed: Boolean = false,
    val systemStatus: Int = -1,
    val voltage: Double? = null,
    val current: Double? = null,
    val batteryPct: Int? = null,
    val cellsMv: List<Int> = emptyList(),
    val consumedMah: Int? = null,
    val gpsFix: Int = -1,
    val satellites: Int = -1,
    val hdop: Double? = null,
    val lat: Double? = null,
    val lon: Double? = null,
    val altMsl: Double? = null,
    val relAlt: Double? = null,
    val groundSpeed: Double? = null,
    val climb: Double? = null,
    val heading: Int? = null,
    val roll: Double? = null,
    val pitch: Double? = null,
    val homeLat: Double? = null,
    val homeLon: Double? = null,
    val unhealthy: List<String> = emptyList(),
    val rcRssi: Int? = null,
    val messages: List<DroneEvent> = emptyList(),
    val events: List<DroneEvent> = emptyList(),
    val frames: Long = 0,
    val crcErrors: Long = 0,
) {
    val hasVehicle: Boolean get() = autopilot >= 0
    val isLive: Boolean get() = linkState == LinkState.CONNECTED

    /**
     * 메시지 하나를 반영한 새 상태. sysId가 정해진 뒤에는 다른 기체·지상국이 보낸 것은 무시합니다
     * (텔레메트리 하나를 미션플래너와 같이 쓰는 경우 등).
     */
    fun reduce(msg: MavMessage, fromSys: Int, nowMs: Long): DroneTelemetry {
        if (msg is MavMessage.Heartbeat) {
            if (msg.type == GCS_TYPE || msg.autopilot == AUTOPILOT_INVALID) return this // 다른 지상국·주변 장치
            if (sysId != 0 && fromSys != sysId) return this
            val mode = MavNames.mode(msg.autopilot, msg.type, msg.customMode)
            var next = copy(
                sysId = fromSys, autopilot = msg.autopilot, vehicleType = msg.type, mode = mode, armed = msg.armed,
                systemStatus = msg.systemStatus, lastHeartbeatMs = nowMs, linkState = LinkState.CONNECTED, error = "",
            )
            when {
                !hasVehicle -> next = next.event(nowMs, "기체 연결: ${MavNames.autopilot(msg.autopilot)} ${MavNames.vehicle(msg.type)} · 모드 ${MavNames.modeKo(mode)}")
                linkState == LinkState.LOST -> next = next.event(nowMs, "기체 신호 다시 잡힘")
            }
            if (hasVehicle && armed != msg.armed) next = next.event(nowMs, if (msg.armed) "시동 걸림(ARM)" else "시동 꺼짐(DISARM)", severity = 5)
            if (hasVehicle && this.mode.isNotEmpty() && this.mode != mode) next = next.event(nowMs, "모드 변경: ${MavNames.modeKo(this.mode)} → ${MavNames.modeKo(mode)}", severity = 5)
            return next
        }
        if (sysId != 0 && fromSys != sysId) return this
        return when (msg) {
            is MavMessage.SysStatus -> {
                val bad = MavNames.SENSORS.filter { (bit, _) ->
                    msg.sensorsPresent and bit != 0L && msg.sensorsEnabled and bit != 0L && msg.sensorsHealth and bit == 0L
                }.map { it.second }
                val pct = msg.batteryRemaining.takeIf { it in 0..100 }
                copy(
                    voltage = msg.voltageMv.takeIf { it in 1..65534 }?.let { it / 1000.0 } ?: voltage,
                    current = msg.currentCa.takeIf { it >= 0 }?.let { it / 100.0 } ?: current,
                    batteryPct = pct ?: batteryPct,
                    unhealthy = bad,
                ).batteryEvents(batteryPct, pct, nowMs)
            }
            is MavMessage.BatteryStatus -> copy(
                cellsMv = msg.cellsMv.ifEmpty { cellsMv },
                consumedMah = msg.consumedMah.takeIf { it >= 0 } ?: consumedMah,
                batteryPct = msg.remaining.takeIf { it in 0..100 } ?: batteryPct,
            ).batteryEvents(batteryPct, msg.remaining.takeIf { it in 0..100 }, nowMs)
            is MavMessage.GpsRaw -> {
                var next = copy(
                    gpsFix = msg.fixType,
                    satellites = msg.satellites.takeIf { it != 255 } ?: -1,
                    hdop = msg.eph.takeIf { it != 0xFFFF }?.let { it / 100.0 },
                    lat = if (msg.fixType >= 2 && lat == null) msg.lat / 1e7 else lat,
                    lon = if (msg.fixType >= 2 && lon == null) msg.lon / 1e7 else lon,
                )
                if (gpsFix in 0..2 && msg.fixType >= 3) next = next.event(nowMs, "GPS 3D 위치 잡음 (위성 ${msg.satellites}개)")
                if (gpsFix >= 3 && msg.fixType < 3) next = next.event(nowMs, "GPS 위치 잃음 (${MavNames.gpsFix(msg.fixType)})", severity = 4)
                next
            }
            is MavMessage.GlobalPosition -> copy(
                lat = if (msg.lat != 0 || msg.lon != 0) msg.lat / 1e7 else lat,
                lon = if (msg.lat != 0 || msg.lon != 0) msg.lon / 1e7 else lon,
                altMsl = msg.altMm / 1000.0,
                relAlt = msg.relAltMm / 1000.0,
                heading = msg.hdgCdeg.takeIf { it != 0xFFFF }?.let { (it / 100.0).roundToInt() } ?: heading,
            )
            is MavMessage.VfrHud -> copy(groundSpeed = msg.groundspeed.toDouble(), climb = msg.climb.toDouble(), heading = msg.heading)
            is MavMessage.Attitude -> copy(roll = Math.toDegrees(msg.roll.toDouble()), pitch = Math.toDegrees(msg.pitch.toDouble()))
            is MavMessage.HomePosition -> copy(homeLat = msg.lat / 1e7, homeLon = msg.lon / 1e7)
            is MavMessage.RcChannels -> copy(rcRssi = msg.rssi.takeIf { it != 255 })
            is MavMessage.StatusText -> withMessage(DroneEvent(nowMs, msg.text, msg.severity, fromVehicle = true))
            else -> this
        }
    }

    /** 같은 문구가 반복되면(PreArm 메시지는 30초마다 다시 옴) 새로 쌓지 않고 시간만 갱신 */
    private fun withMessage(e: DroneEvent): DroneTelemetry {
        if (e.text.isBlank()) return this
        val rest = messages.filterNot { it.text == e.text }
        return copy(messages = (rest + e).takeLast(MAX_MESSAGES))
    }

    fun event(nowMs: Long, text: String, severity: Int = 6): DroneTelemetry =
        copy(events = (events + DroneEvent(nowMs, text, severity)).takeLast(MAX_EVENTS))

    private fun batteryEvents(before: Int?, after: Int?, nowMs: Long): DroneTelemetry {
        if (before == null || after == null) return this
        for (limit in listOf(30, 15)) {
            if (before > limit && after <= limit) return event(nowMs, "배터리 $limit% 이하 (${after}%)", severity = if (limit == 15) 2 else 4)
        }
        return this
    }

    // ------------------------------------------------------------------ 글로 바꾸기

    /** 상단 줄용 한 줄 요약 */
    fun summary(): String = when (linkState) {
        LinkState.DISCONNECTED -> if (error.isNotEmpty()) "연결 실패" else "연결 안 됨"
        LinkState.CONNECTING -> "연결 중…"
        LinkState.WAITING -> "신호 기다리는 중"
        LinkState.LOST -> "신호 끊김"
        LinkState.CONNECTED -> buildList {
            add(MavNames.modeKo(mode).substringBefore('('))
            add(if (armed) "시동 ON" else "시동 OFF")
            voltage?.let { add("%.1fV".format(Locale.US, it)) }
            batteryPct?.let { add("$it%") }
            if (gpsFix >= 0) add(if (gpsFix >= 3) "GPS ${satellites}개" else MavNames.gpsFix(gpsFix))
        }.joinToString(" · ")
    }

    /**
     * RAG에 넣는 '실시간 상태 문서'. LLM 프롬프트의 [실시간 기체 상태] 칸과, LLM이 없을 때의 추출형 답변 재료로 씁니다.
     */
    fun report(nowMs: Long = System.currentTimeMillis()): String {
        if (!hasVehicle && linkState != LinkState.LOST) return "비행컨트롤러: ${summary()}${if (error.isNotEmpty()) " ($error)" else ""}"
        val f = { v: Double, d: Int -> "%.${d}f".format(Locale.US, v) }
        val lines = mutableListOf<String>()
        val age = if (lastHeartbeatMs > 0) " · 마지막 신호 ${f((nowMs - lastHeartbeatMs) / 1000.0, 1)}초 전" else ""
        lines += "연결: $link · ${summary().takeIf { linkState != LinkState.CONNECTED } ?: "정상"}$age"
        lines += "기체: ${MavNames.autopilot(autopilot)} ${MavNames.vehicle(vehicleType)} · 모드 ${MavNames.modeKo(mode)} · " +
            (if (armed) "시동 걸림(ARM)" else "시동 꺼짐(DISARM)") + " · 상태 ${MavNames.systemStatus(systemStatus)}"
        val battery = buildList {
            voltage?.let { add("${f(it, 2)}V") }
            batteryPct?.let { add("잔량 $it%") }
            current?.let { add("전류 ${f(it, 1)}A") }
            if (cellsMv.size > 1) add("셀 ${cellsMv.joinToString("/") { f(it / 1000.0, 2) }}V")
            consumedMah?.let { add("사용 ${it}mAh") }
        }
        lines += "배터리: " + battery.ifEmpty { listOf("정보 없음") }.joinToString(" · ")
        lines += "GPS: ${MavNames.gpsFix(gpsFix)}" + (if (satellites >= 0) " · 위성 ${satellites}개" else "") + (hdop?.let { " · HDOP ${f(it, 1)}" } ?: "")
        if (lat != null && lon != null) {
            lines += "위치: 위도 ${f(lat, 6)}, 경도 ${f(lon, 6)}" +
                (relAlt?.let { " · 이륙 지점 기준 고도 ${f(it, 1)}m" } ?: "") + (altMsl?.let { " · 해발 ${f(it, 1)}m" } ?: "")
        }
        if (homeLat != null && homeLon != null && lat != null && lon != null) {
            lines += "출발점(홈)까지: ${f(distanceM(lat, lon, homeLat, homeLon), 0)}m"
        }
        val motion = buildList {
            groundSpeed?.let { add("지면 속도 ${f(it, 1)}m/s") }
            climb?.let { add("상승 속도 ${f(it, 1)}m/s") }
            heading?.let { add("기수 방향 $it°") }
            if (roll != null && pitch != null) add("롤 ${f(roll, 1)}° 피치 ${f(pitch, 1)}°")
        }
        if (motion.isNotEmpty()) lines += "움직임: " + motion.joinToString(" · ")
        rcRssi?.let { lines += "조종기 신호(RSSI): $it/254" }
        lines += "이상 센서: " + unhealthy.ifEmpty { listOf("없음") }.joinToString(", ")
        val prearm = currentPrearm(nowMs)
        if (prearm.isNotEmpty()) {
            lines += "지금 시동을 막는 점검(PreArm) ${prearm.size}개:"
            prearm.forEach { lines += "- ${it.text}" }
        }
        // 지난 묶음에만 있던 PreArm은 이미 해결된 것이라 빼고 보여 줌
        val shown = messages.filter { !it.text.startsWith("PreArm") || it in prearm }
        if (shown.isNotEmpty()) {
            lines += "최근 기체 메시지(새것부터):"
            shown.reversed().take(6).forEach { lines += "- ${clock(it.timeMs)} [${MavNames.severity(it.severity)}] ${it.text}" }
        }
        if (events.isNotEmpty()) {
            lines += "최근 변화(새것부터):"
            events.reversed().take(5).forEach { lines += "- ${clock(it.timeMs)} ${it.text}" }
        }
        return lines.joinToString("\n")
    }

    /**
     * 검색어에 붙일 상태 키워드 (상태→검색, 사진 질문의 '캡션→검색'과 같은 생각).
     * "왜 시동이 안 걸려?"에는 검색어가 부족하지만, 기체가 보낸 "PreArm: Compass not calibrated"를 붙이면
     * 점검 문서의 나침반 보정 절이 1위로 올라옵니다.
     */
    fun retrievalHint(nowMs: Long = System.currentTimeMillis()): String {
        if (!hasVehicle) return ""
        val parts = linkedSetOf<String>()
        val prearm = currentPrearm(nowMs)
        prearm.take(4).forEach { parts += it.text } // 실제 ArduPilot은 실패한 점검을 여러 개 한꺼번에 보냄 (예: 가속도계·나침반·조종기)
        messages.lastOrNull { nowMs - it.timeMs < HINT_WINDOW_MS && it.severity <= 4 && !it.text.startsWith("PreArm") }?.let { parts += it.text }
        // 'PreArm 실패' 센서 비트는 메시지가 있으면 중복이고, '시동 전 점검' 같은 일반 낱말이 엉뚱한 소개 문단을 끌어올려서 뺌
        unhealthy.filterNot { it.startsWith("시동 전 점검") && prearm.isNotEmpty() }.forEach { parts += "$it 이상" }
        // GPS 미수신은 넣지 않음: STABILIZE·ALT_HOLD는 GPS 없이도 시동이 걸려서 '왜 시동이 안 걸려?'를 엉뚱한 GPS 절로 끌고 감
        // (GPS가 시동을 막으면 ArduPilot이 'PreArm: Need 3D Fix' 같은 메시지를 따로 보냄). GPS 상태는 보고서에는 그대로 들어감
        batteryPct?.let { if (it <= 30) parts += "배터리 부족" }
        if (linkState == LinkState.LOST) parts += "기체 신호 끊김"
        return parts.joinToString(" ").take(300)
    }

    /**
     * 지금 시동을 막고 있는 PreArm 메시지들.
     * ArduPilot은 실패한 점검을 30초마다 '한꺼번에'(1초 안에 몇 개씩) 다시 보냅니다 (실기 픽스호크 2.4.8 + ArduCopter 3.6.12에서 확인:
     * 가속도계·나침반×2·조종기 4축·스로틀 페일세이프가 같은 순간에 옴). 그래서 가장 최근 묶음만 '현재'이고,
     * 그 묶음에 없는 예전 메시지는 이미 해결된 것입니다. 시동이 걸렸거나 40초 넘게 새 묶음이 없으면 없음.
     */
    fun currentPrearm(nowMs: Long = System.currentTimeMillis()): List<DroneEvent> {
        if (armed) return emptyList()
        val pre = messages.filter { it.text.startsWith("PreArm") }
        val newest = pre.maxOfOrNull { it.timeMs } ?: return emptyList()
        if (nowMs - newest > PREARM_STALE_MS) return emptyList()
        return pre.filter { newest - it.timeMs <= PREARM_BURST_MS }
    }

    companion object {
        const val PREARM_BURST_MS = 1_500L
        const val PREARM_STALE_MS = 40_000L
        const val GCS_TYPE = 6
        const val AUTOPILOT_INVALID = 8
        const val MAX_MESSAGES = 30
        const val MAX_EVENTS = 30
        const val HINT_WINDOW_MS = 5 * 60_000L

        private val DRONE_WORDS = listOf(
            "드론", "기체", "픽스호크", "pixhawk", "비행", "시동", "이륙", "착륙", "배터리", "전압", "gps", "위성", "고도",
            "모드", "나침반", "컴퍼스", "프리암", "prearm", "경고", "에러", "오류", "메시지", "상태", "위치", "어디", "속도",
            "방향", "자세", "arm", "센서", "신호", "조종기", "홈", "출발점", "귀환", "rtl", "고장", "문제", "왜", "안 돼", "안돼",
        )

        /** 드론 상태를 묻는 질문인가 (상태 키워드를 검색어에 붙일지 정할 때). 짧고 모호한 질문도 포함 */
        fun isDroneQuestion(q: String): Boolean {
            val s = q.lowercase(Locale.ROOT)
            return DRONE_WORDS.any { it in s } || s.replace(" ", "").length <= 8
        }

        fun clock(ms: Long): String = SimpleDateFormat("HH:mm:ss", Locale.KOREA).format(Date(ms))

        fun distanceM(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
            val r = 6_371_000.0
            val dLat = Math.toRadians(lat2 - lat1)
            val dLon = Math.toRadians(lon2 - lon1)
            val a = Math.sin(dLat / 2).let { it * it } +
                Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2)) * Math.sin(dLon / 2).let { it * it }
            return 2 * r * Math.asin(Math.sqrt(a))
        }
    }
}
