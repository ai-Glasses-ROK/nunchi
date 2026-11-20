package io.github.aiglassesrok.nunchi.drone

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** UDP·TCP 통로가 진짜 소켓으로 주고받는지 (가상 드론으로는 잡히지 않던 apply 안 port 해석 버그의 회귀 테스트) */
class NetworkLinkTest {
    @Test
    fun udpLinkReceivesAndRepliesToSender() {
        val port = DatagramSocket(0).use { it.localPort } // 빈 포트 하나
        val link = UdpLink(port).apply { open() }
        DatagramSocket(0).use { vehicle ->
            val hb = MavlinkWriter(sysId = 1, compId = 1).frame(MavIds.HEARTBEAT, ByteArray(9).also { it[4] = 2; it[5] = 3 }, v2 = false)
            vehicle.send(DatagramPacket(hb, hb.size, InetSocketAddress(InetAddress.getLoopbackAddress(), port)))
            val buf = ByteArray(2048)
            val n = link.read(buf, 2_000)
            assertEquals(hb.size, n)
            assertEquals(MavIds.HEARTBEAT, MavlinkParser().feed(buf.copyOf(n)).single().msgId)
            // 폰(지상국)의 답은 보낸 쪽으로 돌아감
            link.write(MavlinkWriter().heartbeat(v2 = false))
            vehicle.soTimeout = 2_000
            val back = DatagramPacket(ByteArray(64), 64)
            vehicle.receive(back)
            assertEquals(255, MavlinkParser().feed(back.data.copyOf(back.length)).single().sysId)
        }
        link.close()
    }

    @Test
    fun tcpLinkConnectsToTheGivenPort() {
        ServerSocket(0).use { server ->
            val hb = MavlinkWriter(sysId = 1, compId = 1).frame(MavIds.HEARTBEAT, ByteArray(9).also { it[4] = 2; it[5] = 3 }, v2 = true)
            thread { server.accept().use { it.getOutputStream().write(hb); Thread.sleep(300) } }
            val link = TcpLink("127.0.0.1", server.localPort).apply { open() }
            val buf = ByteArray(2048)
            val n = link.read(buf, 2_000)
            assertTrue(n > 0)
            assertEquals(MavIds.HEARTBEAT, MavlinkParser().feed(buf.copyOf(n)).single().msgId)
            link.close()
        }
    }
}
