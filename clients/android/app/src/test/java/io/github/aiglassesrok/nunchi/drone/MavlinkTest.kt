package io.github.aiglassesrok.nunchi.drone

import io.dronefleet.mavlink.MavlinkConnection
import io.dronefleet.mavlink.common.Attitude
import io.dronefleet.mavlink.common.BatteryStatus
import io.dronefleet.mavlink.common.CommandLong
import io.dronefleet.mavlink.common.GlobalPositionInt
import io.dronefleet.mavlink.common.GpsFixType
import io.dronefleet.mavlink.common.GpsRawInt
import io.dronefleet.mavlink.common.MavBatteryFunction
import io.dronefleet.mavlink.common.MavBatteryType
import io.dronefleet.mavlink.common.MavSeverity
import io.dronefleet.mavlink.common.RequestDataStream
import io.dronefleet.mavlink.common.Statustext
import io.dronefleet.mavlink.common.SysStatus
import io.dronefleet.mavlink.common.VfrHud
import io.dronefleet.mavlink.minimal.Heartbeat
import io.dronefleet.mavlink.minimal.MavAutopilot
import io.dronefleet.mavlink.minimal.MavState
import io.dronefleet.mavlink.minimal.MavType
import io.dronefleet.mavlink.util.EnumValue
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.math.BigInteger
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 직접 만든 MAVLink 파서·인코더를 공식 XML에서 생성된 독립 구현(dronefleet)과 바이트 단위로 맞춰 봅니다.
 * 여기서 통과하면 실제 픽스호크(ArduPilot·PX4)가 보내는 프레임도 같은 방식으로 읽힙니다.
 */
class MavlinkTest {
    private fun fleetBytes(v2: Boolean, vararg payloads: Any): ByteArray {
        val out = ByteArrayOutputStream()
        val conn = MavlinkConnection.create(ByteArrayInputStream(ByteArray(0)), out)
        for (p in payloads) if (v2) conn.send2(1, 1, p) else conn.send1(1, 1, p)
        return out.toByteArray()
    }

    private val heartbeat = Heartbeat.builder()
        .type(MavType.MAV_TYPE_QUADROTOR).autopilot(MavAutopilot.MAV_AUTOPILOT_ARDUPILOTMEGA)
        .baseMode(EnumValue.create(0x81 or 0x10)).customMode(5).systemStatus(MavState.MAV_STATE_ACTIVE).mavlinkVersion(3).build()
    private val gps = GpsRawInt.builder().timeUsec(BigInteger.valueOf(123456789)).fixType(GpsFixType.GPS_FIX_TYPE_3D_FIX)
        .lat(375487000).lon(1271197000).alt(32000).eph(90).epv(120).vel(400).cog(9000).satellitesVisible(10).build()
    private val sys = SysStatus.builder().onboardControlSensorsPresent(EnumValue.create(0x1020027)).onboardControlSensorsEnabled(EnumValue.create(0x1020027))
        .onboardControlSensorsHealth(EnumValue.create(0x1020023)).load(250).voltageBattery(15820).currentBattery(1150).batteryRemaining(72).build()
    private val pos = GlobalPositionInt.builder().timeBootMs(1000).lat(375487100).lon(1271197200).alt(47000).relativeAlt(15000)
        .vx(-120).vy(380).vz(-5).hdg(10750).build()
    private val att = Attitude.builder().timeBootMs(1000).roll(0.1f).pitch(-0.05f).yaw(1.5f).build()
    private val hud = VfrHud.builder().airspeed(4f).groundspeed(4.2f).heading(107).throttle(45).alt(47f).climb(0.3f).build()
    private val bat = BatteryStatus.builder().id(0).batteryFunction(MavBatteryFunction.MAV_BATTERY_FUNCTION_ALL).type(MavBatteryType.MAV_BATTERY_TYPE_LIPO)
        .temperature(Short.MAX_VALUE.toInt()).voltages(listOf(3950, 3960, 3955, 3958, 65535, 65535, 65535, 65535, 65535, 65535))
        .currentBattery(1150).currentConsumed(1234).energyConsumed(-1).batteryRemaining(72).build()
    private val text = Statustext.builder().severity(MavSeverity.MAV_SEVERITY_WARNING).text("PreArm: Compass not calibrated").build()

    private fun decodeAll(bytes: ByteArray, chunk: Int = bytes.size): List<Pair<MavFrame, MavMessage?>> {
        val parser = MavlinkParser()
        val out = mutableListOf<Pair<MavFrame, MavMessage?>>()
        var i = 0
        while (i < bytes.size) {
            val n = minOf(chunk, bytes.size - i)
            parser.feed(bytes.copyOfRange(i, i + n)).forEach { out += it to MavMessage.decode(it) }
            i += n
        }
        assertEquals("CRC 오류가 없어야 함", 0L, parser.crcErrors)
        return out
    }

    @Test
    fun readsWhatDronefleetWritesInV1AndV2() {
        for (v2 in listOf(false, true)) {
            val frames = decodeAll(fleetBytes(v2, heartbeat, gps, sys, pos, att, hud, bat, text))
            assertEquals("v2=$v2", 8, frames.size)
            assertTrue(frames.all { it.first.version == (if (v2) 2 else 1) && it.first.sysId == 1 })
            val m = frames.map { it.second }
            assertEquals(MavMessage.Heartbeat(type = 2, autopilot = 3, baseMode = 0x91, customMode = 5, systemStatus = 4), m[0])
            assertTrue((m[0] as MavMessage.Heartbeat).armed)
            assertEquals(MavMessage.GpsRaw(fixType = 3, lat = 375487000, lon = 1271197000, altMm = 32000, eph = 90, velCms = 400, satellites = 10), m[1])
            val s = m[2] as MavMessage.SysStatus
            assertEquals(15820, s.voltageMv); assertEquals(1150, s.currentCa); assertEquals(72, s.batteryRemaining)
            assertEquals(0x1020027L, s.sensorsPresent); assertEquals(0x1020023L, s.sensorsHealth)
            assertEquals(MavMessage.GlobalPosition(375487100, 1271197200, 47000, 15000, -120, 380, -5, 10750), m[3])
            val a = m[4] as MavMessage.Attitude
            assertEquals(0.1f, a.roll, 1e-6f); assertEquals(-0.05f, a.pitch, 1e-6f); assertEquals(1.5f, a.yaw, 1e-6f)
            assertEquals(MavMessage.VfrHud(4f, 4.2f, 107, 45, 47f, 0.3f), m[5])
            assertEquals(MavMessage.BatteryStatus(id = 0, cellsMv = listOf(3950, 3960, 3955, 3958), currentCa = 1150, consumedMah = 1234, remaining = 72), m[6])
            assertEquals(MavMessage.StatusText(4, "PreArm: Compass not calibrated"), m[7])
        }
    }

    @Test
    fun survivesByteByByteFeedNoiseAndCorruption() {
        val good = fleetBytes(true, heartbeat, gps, text)
        val corrupted = fleetBytes(true, gps).also { it[it.size - 1] = (it[it.size - 1] + 1).toByte() } // CRC 틀림
        val noise = byteArrayOf(0x00, 0x13, 0xFD.toByte(), 0x05, 0x7F, 0xFE.toByte(), 0x01) // 가짜 시작 바이트 포함
        val stream = noise + good + corrupted + noise + good
        val parser = MavlinkParser()
        val frames = stream.flatMap { b -> parser.feed(byteArrayOf(b)) }
        assertEquals(6, frames.size)
        assertEquals(listOf(0, 24, 253, 0, 24, 253), frames.map { it.msgId })
        assertTrue(parser.crcErrors >= 1)
    }

    @Test
    fun handlesTruncatedV2PayloadAndSignature() {
        // 실제 기체처럼 v2 끝의 0 바이트를 자른 프레임 (VirtualDrone이 쓰는 방식)
        val w = MavlinkWriter(sysId = 1, compId = 1)
        val payload = ByteArray(28).also { it[0] = 1; it[4] = 0x10 } // GLOBAL_POSITION_INT, 뒤쪽은 0
        val truncated = w.frame(MavIds.GLOBAL_POSITION_INT, payload, v2 = true, truncate = true)
        assertTrue(truncated.size < 10 + 28 + 2)
        // 서명 플래그(0x01) + 13바이트 서명이 붙은 프레임도 길이를 맞게 건너뜀
        val signed = w.frame(MavIds.HEARTBEAT, ByteArray(9).also { it[4] = 2; it[5] = 3 }, v2 = true).toMutableList()
        signed[2] = 0x01
        val crc = MavCrc.compute(signed.toByteArray(), 1, signed.size - 2, 50)
        signed[signed.size - 2] = crc.toByte(); signed[signed.size - 1] = (crc shr 8).toByte()
        val bytes = truncated + signed.toByteArray() + ByteArray(13) { 0x55 } + truncated
        val got = decodeAll(bytes)
        assertEquals(listOf(33, 0, 33), got.map { it.first.msgId })
        assertEquals(0x10, (got[0].second as MavMessage.GlobalPosition).lat) // 잘린 뒤쪽은 0으로 채워 읽음
    }

    @Test
    fun dronefleetReadsWhatThePhoneSends() {
        val w = MavlinkWriter()
        for (v2 in listOf(false, true)) {
            val bytes = w.heartbeat(v2) + w.requestDataStream(1, 1, MavlinkWriter.STREAM_POSITION, 3, v2) +
                w.commandLong(1, 1, MavlinkWriter.CMD_REQUEST_MESSAGE, floatArrayOf(242f), v2)
            val conn = MavlinkConnection.create(ByteArrayInputStream(bytes), ByteArrayOutputStream())
            val hb = conn.next()
            assertEquals(255, hb.originSystemId)
            assertEquals(MavType.MAV_TYPE_GCS, (hb.payload as Heartbeat).type().entry())
            val rds = conn.next().payload as RequestDataStream
            assertEquals(listOf(1, 1, 6, 3, 1), listOf(rds.targetSystem(), rds.targetComponent(), rds.reqStreamId(), rds.reqMessageRate(), rds.startStop()))
            val cmd = conn.next().payload as CommandLong
            assertEquals(512, cmd.command().value())
            assertEquals(242f, cmd.param1(), 0f)
        }
    }

    /** CRC_EXTRA를 메시지 정의(이름·필드 타입·이름)에서 다시 계산 — 표의 숫자가 맞는지 */
    @Test
    fun crcExtraMatchesFieldDefinitions() {
        val size = mapOf("uint64_t" to 8, "int64_t" to 8, "uint32_t" to 4, "int32_t" to 4, "float" to 4, "uint16_t" to 2, "int16_t" to 2, "uint8_t" to 1, "int8_t" to 1, "char" to 1)
        fun extra(name: String, fields: List<Triple<String, String, Int>>): Int {
            var crc = 0xFFFF
            fun acc(s: String) { for (b in s.toByteArray()) crc = MavCrc.accumulate(b.toInt() and 0xFF, crc) }
            acc("$name ")
            for ((type, field, arr) in fields.sortedByDescending { size.getValue(it.first) }) {
                acc("$type "); acc("$field ")
                if (arr > 0) crc = MavCrc.accumulate(arr, crc)
            }
            return (crc and 0xFF) xor (crc ushr 8)
        }
        fun f(t: String, n: String, a: Int = 0) = Triple(t, n, a)
        val defs = mapOf(
            MavIds.HEARTBEAT to ("HEARTBEAT" to listOf(f("uint8_t", "type"), f("uint8_t", "autopilot"), f("uint8_t", "base_mode"), f("uint32_t", "custom_mode"), f("uint8_t", "system_status"), f("uint8_t", "mavlink_version"))),
            MavIds.GPS_RAW_INT to ("GPS_RAW_INT" to listOf(f("uint64_t", "time_usec"), f("uint8_t", "fix_type"), f("int32_t", "lat"), f("int32_t", "lon"), f("int32_t", "alt"), f("uint16_t", "eph"), f("uint16_t", "epv"), f("uint16_t", "vel"), f("uint16_t", "cog"), f("uint8_t", "satellites_visible"))),
            MavIds.STATUSTEXT to ("STATUSTEXT" to listOf(f("uint8_t", "severity"), f("char", "text", 50))),
            MavIds.REQUEST_DATA_STREAM to ("REQUEST_DATA_STREAM" to listOf(f("uint8_t", "target_system"), f("uint8_t", "target_component"), f("uint8_t", "req_stream_id"), f("uint16_t", "req_message_rate"), f("uint8_t", "start_stop"))),
            MavIds.HOME_POSITION to ("HOME_POSITION" to listOf(f("int32_t", "latitude"), f("int32_t", "longitude"), f("int32_t", "altitude"), f("float", "x"), f("float", "y"), f("float", "z"), f("float", "q", 4), f("float", "approach_x"), f("float", "approach_y"), f("float", "approach_z"))),
        )
        for ((id, def) in defs) assertEquals(def.first, MavIds.SPECS.getValue(id).first, extra(def.first, def.second))
    }

    @Test
    fun modeNames() {
        assertEquals("LOITER", MavNames.mode(3, 2, 5))
        assertEquals("RTL", MavNames.mode(3, 13, 6))
        assertEquals("FBWA", MavNames.mode(3, 1, 5))
        assertEquals("MISSION", MavNames.mode(12, 2, (4L shl 16) or (4L shl 24)))
        assertEquals("POSCTL", MavNames.mode(12, 2, 3L shl 16))
        assertEquals("LOITER(제자리 위치 유지)", MavNames.modeKo("LOITER"))
    }
}
