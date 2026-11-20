package io.github.aiglassesrok.nunchi.drone

import io.github.aiglassesrok.nunchi.ai.LocalAssistant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 가상 드론 시나리오를 실제 바이트로 흘려서 파서 → 상태 → RAG 글(report·hint)까지 확인 */
class DroneTelemetryTest {
    /** 0초부터 untilMs까지 가상 드론을 돌린 결과 상태 */
    private fun runUntil(untilMs: Long, drone: VirtualDrone = VirtualDrone()): DroneTelemetry {
        val parser = MavlinkParser()
        var t = DroneTelemetry(link = "가상", linkState = LinkState.WAITING)
        var ms = 0L
        while (ms <= untilMs) {
            for (f in parser.feed(drone.step(ms))) MavMessage.decode(f)?.let { t = t.reduce(it, f.sysId, ms) }
            ms += 100
        }
        assertEquals(0L, parser.crcErrors)
        return t
    }

    @Test
    fun bootThenPrearmCompassWarning() {
        val t = runUntil(20_000)
        assertEquals(LinkState.CONNECTED, t.linkState)
        assertEquals("STABILIZE", t.mode)
        assertFalse(t.armed)
        assertEquals(3, t.gpsFix)
        assertEquals(10, t.satellites)
        assertEquals("PreArm: Compass not calibrated", t.messages.last { it.text.startsWith("PreArm") }.text)
        assertTrue(t.events.any { "GPS 3D" in it.text })
        val hint = t.retrievalHint(nowMs = 20_000)
        assertTrue(hint, hint.startsWith("PreArm: Compass not calibrated"))
        assertFalse("중복된 일반 낱말은 빼야 함: $hint", "시동 전 점검" in hint)
        assertFalse("이미 해결된 예전 PreArm은 빼야 함: $hint", "Need 3D Fix" in hint)
        val report = t.report(nowMs = 20_000)
        assertTrue(report, "지금 시동을 막는 점검(PreArm) 1개:\n- PreArm: Compass not calibrated" in report && "시동 꺼짐" in report)
        assertFalse("해결된 예전 PreArm은 보고서에서 빠짐:\n$report", "Need 3D Fix" in report)
    }

    @Test
    fun flyingInLoiter() {
        val t = runUntil(60_000)
        assertTrue(t.armed)
        assertEquals("LOITER", t.mode)
        assertEquals(15.0, t.relAlt!!, 0.2)
        assertEquals(4.0, t.groundSpeed!!, 0.1)
        assertTrue(t.batteryPct!! in 40..80)
        assertEquals(4, t.cellsMv.size)
        assertNotNull(t.homeLat)
        assertTrue(t.events.any { "시동 걸림" in it.text })
        assertTrue(t.events.any { "모드 변경" in it.text && "LOITER" in it.text })
        assertEquals("", t.retrievalHint(nowMs = 60_000 + DroneTelemetry.HINT_WINDOW_MS)) // 오래된 경고는 힌트에서 빠짐
    }

    @Test
    fun lowBatteryReturnsHome() {
        val t = runUntil(80_000)
        assertEquals("RTL", t.mode)
        assertTrue(t.messages.any { it.text.startsWith("Battery 1 is low") })
        assertTrue(t.events.any { "배터리 30% 이하" in it.text })
        assertTrue(t.retrievalHint(nowMs = 80_000).contains("Battery 1 is low"))
        // LLM 없이도 상태 질문에 값으로 답함
        val a = LocalAssistant.telemetryAnswer("배터리 얼마나 남았어?", t)!!
        assertTrue(a, a.startsWith("배터리는 ") && "%" in a)
        val where = LocalAssistant.telemetryAnswer("드론 지금 어디야?", t)!!
        assertTrue(where, "위도" in where && "출발점에서" in where)
    }

    @Test
    fun ignoresOtherGroundStationsAndHeartbeatLossIsVisible() {
        var t = runUntil(15_000)
        // 다른 지상국(미션플래너 등)의 하트비트는 무시
        t = t.reduce(MavMessage.Heartbeat(type = 6, autopilot = 8, baseMode = 0, customMode = 0, systemStatus = 4), 255, 15_100)
        assertEquals("STABILIZE", t.mode)
        assertEquals(1, t.sysId)
        // 다른 기체 번호의 메시지도 무시
        t = t.reduce(MavMessage.StatusText(2, "other vehicle"), 7, 15_200)
        assertFalse(t.messages.any { it.text == "other vehicle" })
    }

    @Test
    fun droneQuestionDetection() {
        assertTrue(DroneTelemetry.isDroneQuestion("배터리 몇 프로야?"))
        assertTrue(DroneTelemetry.isDroneQuestion("왜 시동이 안 걸려?"))
        assertTrue(DroneTelemetry.isDroneQuestion("이거 뭐야?"))
        assertFalse(DroneTelemetry.isDroneQuestion("캠핑장 체크인은 몇 시부터야?"))
    }

    @Test
    fun promptHasLiveBlockThenDocsThenQuestion() {
        val p = LocalAssistant.userPrompt("배터리?", emptyList(), "배터리: 15.8V")
        assertTrue(p.startsWith("[실시간 기체 상태]\n배터리: 15.8V\n\n[참고 문서]\n(관련 문서를 찾지 못함)"))
        assertTrue(p.endsWith("[질문]\n배터리?"))
        assertTrue(LocalAssistant.systemPrompt(live = true, vision = true, droneCamera = true).contains("드론 카메라"))
    }
}
