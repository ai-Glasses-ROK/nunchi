package io.github.aiglassesrok.nunchi.drone

import io.github.aiglassesrok.nunchi.ai.LocalAssistant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 실기 캡처로 확인: 픽스호크 2.4.8(PX4v3) + ArduCopter V3.6.12, 본체 USB, MAVLink v1, 약 35초.
 * (scripts/mavlink_bridge.py로 받음. 개인정보 보호를 위해 위치·해발고도·기압 필드는 0으로 지우고 CRC를 다시 계산했으며,
 *  앱이 쓰지 않는 메시지(AHRS2 등, 좌표가 들어 있음)는 뺐습니다. 나머지 바이트는 기체가 보낸 그대로.)
 */
class RealPixhawkCaptureTest {
    private val bytes = javaClass.classLoader!!.getResource("pixhawk_arducopter3612_usb.bin")!!.readBytes()

    /** USB 패킷처럼 64바이트씩 흘려 넣고, 도착 시간은 35초에 고르게 나눔 */
    private fun replay(): Pair<DroneTelemetry, Long> {
        val parser = MavlinkParser()
        var t = DroneTelemetry(link = "USB 픽스호크", linkState = LinkState.WAITING)
        var i = 0
        var now = 0L
        while (i < bytes.size) {
            val n = minOf(64, bytes.size - i)
            now = i * 35_000L / bytes.size
            for (f in parser.feed(bytes.copyOfRange(i, i + n))) MavMessage.decode(f)?.let { t = t.reduce(it, f.sysId, now) }
            i += n
        }
        assertEquals("실기 스트림에서 CRC 오류가 없어야 함", 0L, parser.crcErrors)
        assertTrue("프레임 수 ${parser.framesOk}", parser.framesOk > 900)
        assertEquals(1, parser.lastVersion)
        return t to now
    }

    @Test
    fun readsRealArduCopter() {
        val (t, _) = replay()
        assertEquals(LinkState.CONNECTED, t.linkState)
        assertEquals(MavNames.AUTOPILOT_ARDUPILOT, t.autopilot)
        assertEquals(2, t.vehicleType) // 쿼드콥터
        assertEquals("STABILIZE", t.mode)
        assertFalse(t.armed)
        assertTrue(t.roll != null && t.heading != null)
    }

    @Test
    fun realPrearmBurstIsTheCurrentSet() {
        val (t, now) = replay()
        val current = t.currentPrearm(now).map { it.text }
        // 실기에서 같은 순간에 온 점검들
        for (expected in listOf("PreArm: 3D Accel calibration needed", "PreArm: Compass not calibrated", "PreArm: RC Roll not configured", "PreArm: Throttle below Failsafe")) {
            assertTrue("$expected not in $current", expected in current)
        }
        val hint = t.retrievalHint(now)
        assertTrue(hint, hint.startsWith("PreArm: ") && hint.count { it == ':' } >= 3)
        val report = t.report(now)
        assertTrue(report, "지금 시동을 막는 점검(PreArm) ${current.size}개" in report)
        val answer = LocalAssistant.telemetryAnswer("왜 시동이 안 걸려?", t, now)
        assertTrue(answer, answer!!.contains("시동을 막는 점검이 ${current.size}개"))
        // 시동이 걸리면 PreArm은 더 이상 '현재'가 아님, 40초 넘게 새 묶음이 없어도
        assertTrue(t.copy(armed = true).currentPrearm(now).isEmpty())
        assertTrue(t.currentPrearm(now + DroneTelemetry.PREARM_STALE_MS + 31_000).isEmpty())
    }
}
