package io.github.aiglassesrok.nunchi.drone

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

/** USB 시리얼 칩 설정값 — 데이터시트/공개 드라이버의 알려진 값과 비교 */
class UsbSerialMathTest {
    @Test
    fun ftdiBaudMatchesAppNoteTable() {
        // FTDI AN232B-05 '3MHz 기준' 표의 값
        val expected = mapOf(
            9600 to 0x4138, 19200 to 0x809C, 38400 to 0xC04E, 57600 to 0xC034,
            115200 to 0x001A, 230400 to 0x000D, 460800 to 0x4006, 921600 to 0x8003,
        )
        for ((baud, value) in expected) assertEquals("baud $baud", value to 0, UsbSerial.ftdiBaud(baud))
        // 0.375 같은 분수는 인덱스의 비트로 감 (여기서는 보드레이트 76800: 39.0625 → 39 + 0 / 8)
        val (_, idx) = UsbSerial.ftdiBaud(3_000_000 * 8 / (8 * 10 + 3)) // 10.375
        assertEquals(1, idx)
        // 두 포트 칩은 인덱스 아래 바이트에 포트 번호+1
        assertEquals(0xC034 to 0x0001, UsbSerial.ftdiBaud(57600, withPort = true, port = 0))
    }

    @Test
    fun ch340BaudMatchesLinuxDriverTable() {
        // Linux ch341.c의 옛 표 {115200, 0xcc03, 0x0008} + 버퍼 대기 방지 비트(0x80)
        assertEquals(0xCC83 to 0x08, UsbSerial.ch34xBaud(115200))
        assertEquals(0x9883 to 0x10, UsbSerial.ch34xBaud(57600))
    }

    @Test
    fun ftdiStatusBytesAreStrippedPerPacket() {
        // 64바이트 패킷 2개 + 꼬리 10바이트: 각 패킷 앞 2바이트(상태)를 버려야 함
        val src = ByteArray(64 + 64 + 10) { i -> if (i % 64 < 2) 0x31 else (i and 0x7F).toByte() }
        val dst = ByteArray(256)
        val n = UsbSerial.stripFtdiStatus(src, src.size, 64, dst)
        assertEquals(62 + 62 + 8, n)
        val expected = src.indices.filter { it % 64 >= 2 }.map { src[it] }.toByteArray()
        assertArrayEquals(expected, dst.copyOf(n))
        // 받는 쪽 버퍼가 작아도 넘치지 않음
        assertEquals(10, UsbSerial.stripFtdiStatus(src, src.size, 64, ByteArray(10)))
    }
}
