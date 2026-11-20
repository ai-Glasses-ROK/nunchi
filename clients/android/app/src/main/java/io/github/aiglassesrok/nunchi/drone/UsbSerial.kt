package io.github.aiglassesrok.nunchi.drone

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import androidx.core.content.ContextCompat
import java.io.IOException
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine

/**
 * USB OTG 시리얼 — 외부 라이브러리 없이 안드로이드 USB Host API로 직접.
 *
 * | 꽂는 것 | 칩 | 보드레이트 |
 * |---|---|---|
 * | 픽스호크 본체 micro-USB | CDC-ACM (가상 COM) | 무관(115200) |
 * | 텔레메트리 라디오 지상용(USB) — 예: B-CUBE Radio Telemetry V5, SiK/3DR | FTDI · CP210x · CH340 중 하나 | 57600 (TELEM1 기본) |
 * | USB-시리얼 변환기로 TELEM2에 직접 | FTDI · CP210x · CH340 | 57600 |
 */
enum class UsbChip(val label: String) { CDC("CDC-ACM"), FTDI("FTDI"), CP210X("CP210x"), CH34X("CH340") }

object UsbSerial {
    private const val ACTION_PERMISSION = "io.github.aiglassesrok.nunchi.USB_PERMISSION"

    /** 알려진 비행컨트롤러·USB-시리얼 칩 (usb_device_filter.xml과 같은 목록) */
    private val FC_VENDORS = mapOf(0x26AC to "3DR/픽스호크", 0x1209 to "ArduPilot", 0x2DAE to "CubePilot", 0x3162 to "Holybro", 0x0483 to "STM32")

    fun detect(device: UsbDevice): UsbChip? = when {
        device.vendorId == 0x0403 -> UsbChip.FTDI
        device.vendorId == 0x10C4 -> UsbChip.CP210X
        device.vendorId == 0x1A86 && device.productId in setOf(0x7523, 0x5523, 0x7522) -> UsbChip.CH34X
        (0 until device.interfaceCount).any {
            val c = device.getInterface(it).interfaceClass
            c == UsbConstants.USB_CLASS_COMM || c == UsbConstants.USB_CLASS_CDC_DATA
        } -> UsbChip.CDC
        else -> null
    }

    /** 시리얼로 쓸 수 있는 USB 장치 (UVC 카메라 같은 다른 장치는 뺌) */
    fun devices(context: Context): List<UsbDevice> =
        context.getSystemService(UsbManager::class.java)?.deviceList?.values?.filter { detect(it) != null }.orEmpty()

    fun describe(device: UsbDevice): String {
        val chip = detect(device)?.label ?: "?"
        val vendor = FC_VENDORS[device.vendorId]
        val name = device.productName?.takeIf { it.isNotBlank() } ?: vendor ?: "USB 장치"
        return "%s · %s (%04X:%04X)".format(name, chip, device.vendorId, device.productId)
    }

    /** 처음 연결하는 장치는 사용자에게 USB 권한을 묻습니다. (usb_device_filter로 앱이 열렸다면 이미 허용된 상태) */
    suspend fun requestPermission(context: Context, device: UsbDevice): Boolean {
        val manager = context.getSystemService(UsbManager::class.java) ?: return false
        if (manager.hasPermission(device)) return true
        return suspendCancellableCoroutine { cont ->
            val receiver = object : BroadcastReceiver() {
                override fun onReceive(c: Context, intent: Intent) {
                    if (intent.action != ACTION_PERMISSION) return
                    runCatching { context.unregisterReceiver(this) }
                    if (cont.isActive) cont.resume(intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false) || manager.hasPermission(device))
                }
            }
            ContextCompat.registerReceiver(context, receiver, IntentFilter(ACTION_PERMISSION), ContextCompat.RECEIVER_NOT_EXPORTED)
            cont.invokeOnCancellation { runCatching { context.unregisterReceiver(receiver) } }
            // 안드로이드 12+: 시스템이 결과를 채워 넣으려면 MUTABLE, 14+: 그러려면 패키지를 지정한 명시적 인텐트여야 함
            val intent = Intent(ACTION_PERMISSION).setPackage(context.packageName)
            val pending = PendingIntent.getBroadcast(context, device.deviceId, intent, PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            manager.requestPermission(device, pending)
        }
    }

    // ---------------------------------------------------------------- 칩별 설정값 (단위 테스트 대상)

    /** FTDI 보드레이트 (값, 인덱스). 3MHz 기준 클록을 1/8 단위로 나눔 — FTDI AN232B-05와 같은 결과 */
    fun ftdiBaud(baud: Int, withPort: Boolean = false, port: Int = 0): Pair<Int, Int> {
        var divisor = (24_000_000 shl 1) / baud
        divisor = (divisor + 1) shr 1
        val sub = divisor and 0x07
        divisor = divisor shr 3
        require(divisor in 1..0x3FFF) { "지원하지 않는 보드레이트: $baud" }
        var value = divisor
        var index = 0
        when (sub) {
            1 -> value = value or 0xC000 // 0.125
            2 -> value = value or 0x8000 // 0.25
            3 -> index = 1 // 0.375
            4 -> value = value or 0x4000 // 0.5
            5 -> { value = value or 0x4000; index = 1 } // 0.625
            6 -> { value = value or 0x8000; index = 1 } // 0.75
            7 -> { value = value or 0xC000; index = 1 } // 0.875
        }
        if (withPort) index = (index shl 8) or (port + 1)
        return value to index
    }

    /** CH340 보드레이트 → (0x1312 레지스터 값, 0x0F2C 레지스터 값) */
    fun ch34xBaud(baud: Int): Pair<Int, Int> {
        var factor = 1_532_620_800L / baud
        var divisor = 3L
        while (factor > 0xFFF0 && divisor > 0) {
            factor = factor shr 3
            divisor--
        }
        require(factor <= 0xFFF0) { "지원하지 않는 보드레이트: $baud" }
        factor = 0x10000 - factor
        divisor = divisor or 0x0080 // 이 비트가 없으면 CH341A가 버퍼가 찰 때까지 기다림
        return ((factor and 0xFF00) or divisor).toInt() to (factor and 0xFF).toInt()
    }

    /** FTDI는 USB 패킷(최대 패킷 크기)마다 앞에 상태 바이트 2개를 붙여 보냅니다 → 떼어 냄 */
    fun stripFtdiStatus(src: ByteArray, n: Int, packetSize: Int, dst: ByteArray): Int {
        var out = 0
        var pos = 0
        while (pos < n) {
            val end = minOf(pos + packetSize, n)
            val start = pos + 2
            val len = minOf(end - start, dst.size - out)
            if (len > 0) {
                System.arraycopy(src, start, dst, out, len)
                out += len
            }
            pos = end
        }
        return out
    }
}

class UsbSerialLink(private val context: Context, private val device: UsbDevice, private val baud: Int) : ByteLink {
    private val manager = context.getSystemService(UsbManager::class.java)
    private val chip = UsbSerial.detect(device) ?: UsbChip.CDC
    private var conn: UsbDeviceConnection? = null
    private var inEp: UsbEndpoint? = null
    private var outEp: UsbEndpoint? = null
    private val claimed = mutableListOf<UsbInterface>()
    private val raw = ByteArray(16 * 1024)
    private var lastPresenceCheck = 0L

    override val description: String
        get() = "USB ${device.productName?.takeIf { it.isNotBlank() } ?: chip.label} · ${chip.label}" + if (chip == UsbChip.CDC) "" else " ${baud}bps"

    override fun open() {
        if (manager == null || !manager.hasPermission(device)) throw IOException("USB 권한이 없어요")
        val c = manager.openDevice(device) ?: throw IOException("USB 장치를 열지 못했어요")
        conn = c
        try {
            when (chip) {
                UsbChip.CDC -> openCdc(c)
                UsbChip.FTDI -> openFtdi(c)
                UsbChip.CP210X -> openCp210x(c)
                UsbChip.CH34X -> openCh34x(c)
            }
        } catch (e: Exception) {
            close()
            throw if (e is IOException) e else IOException(e.message ?: "USB 설정 실패", e)
        }
    }

    private fun claim(c: UsbDeviceConnection, itf: UsbInterface) {
        if (!c.claimInterface(itf, true)) throw IOException("USB 인터페이스 ${itf.id}를 잡지 못했어요")
        claimed += itf
    }

    private fun bulkEndpoints(itf: UsbInterface) {
        for (i in 0 until itf.endpointCount) {
            val ep = itf.getEndpoint(i)
            if (ep.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
            if (ep.direction == UsbConstants.USB_DIR_IN) inEp = ep else outEp = ep
        }
        if (inEp == null || outEp == null) throw IOException("USB 데이터 통로(bulk)를 찾지 못했어요")
    }

    private fun ctrl(c: UsbDeviceConnection, type: Int, req: Int, value: Int, index: Int, data: ByteArray? = null) {
        val r = c.controlTransfer(type, req, value, index, data, data?.size ?: 0, TIMEOUT)
        if (r < 0) throw IOException("USB 설정 명령 실패 (req 0x%02X)".format(req))
    }

    /** 픽스호크 본체 USB: 통신(COMM) 인터페이스로 회선 설정, 데이터 인터페이스로 송수신 */
    private fun openCdc(c: UsbDeviceConnection) {
        val all = (0 until device.interfaceCount).map { device.getInterface(it) }
        val control = all.firstOrNull { it.interfaceClass == UsbConstants.USB_CLASS_COMM }
        val data = all.firstOrNull { it.interfaceClass == UsbConstants.USB_CLASS_CDC_DATA }
            ?: all.firstOrNull { itf -> (0 until itf.endpointCount).count { itf.getEndpoint(it).type == UsbConstants.USB_ENDPOINT_XFER_BULK } >= 2 }
            ?: throw IOException("CDC 데이터 인터페이스가 없어요")
        control?.let { claim(c, it) }
        claim(c, data)
        bulkEndpoints(data)
        val index = control?.id ?: 0
        val coding = byteArrayOf(
            (baud and 0xFF).toByte(), (baud shr 8 and 0xFF).toByte(), (baud shr 16 and 0xFF).toByte(), (baud shr 24 and 0xFF).toByte(),
            0, 0, 8, // 정지 비트 1, 패리티 없음, 8비트
        )
        runCatching { ctrl(c, 0x21, 0x20, 0, index, coding) } // SET_LINE_CODING (가상 COM은 무시해도 됨)
        runCatching { ctrl(c, 0x21, 0x22, 0x03, index) } // SET_CONTROL_LINE_STATE: DTR+RTS (일부 보드는 DTR이 켜져야 보내기 시작)
    }

    private fun openFtdi(c: UsbDeviceConnection) {
        val itf = device.getInterface(0)
        claim(c, itf)
        bulkEndpoints(itf)
        // 두 포트 칩(FT2232 등)은 보드레이트 인덱스에 포트 번호를 함께 씀 — bcdDevice로 구분
        val raw = c.rawDescriptors
        val bcd = if (raw != null && raw.size >= 14) (raw[12].toInt() and 0xFF) or ((raw[13].toInt() and 0xFF) shl 8) else 0
        val withPort = bcd in setOf(0x0500, 0x0700, 0x0800, 0x0900)
        ctrl(c, 0x40, 0, 0, 1) // reset
        ctrl(c, 0x40, 1, 0x0303, 1) // DTR+RTS
        ctrl(c, 0x40, 2, 0, 1) // 흐름 제어 없음
        ctrl(c, 0x40, 4, 0x0008, 1) // 8N1
        val (value, index) = UsbSerial.ftdiBaud(baud, withPort)
        ctrl(c, 0x40, 3, value, index)
        runCatching { ctrl(c, 0x40, 9, 1, 1) } // 지연 타이머 1ms (기본 16ms → MAVLink가 뭉쳐서 늦게 옴)
    }

    private fun openCp210x(c: UsbDeviceConnection) {
        val itf = device.getInterface(0)
        claim(c, itf)
        bulkEndpoints(itf)
        val i = itf.id
        ctrl(c, 0x41, 0x00, 0x0001, i) // IFC_ENABLE
        val b = byteArrayOf((baud and 0xFF).toByte(), (baud shr 8 and 0xFF).toByte(), (baud shr 16 and 0xFF).toByte(), (baud shr 24 and 0xFF).toByte())
        ctrl(c, 0x41, 0x1E, 0, i, b) // SET_BAUDRATE
        ctrl(c, 0x41, 0x03, 0x0800, i) // SET_LINE_CTL: 8N1
        runCatching { ctrl(c, 0x41, 0x07, 0x0303, i) } // SET_MHS: DTR+RTS
    }

    private fun openCh34x(c: UsbDeviceConnection) {
        val itf = device.getInterface(0)
        claim(c, itf)
        bulkEndpoints(itf)
        val buf = ByteArray(8)
        fun inq(req: Int, value: Int) = c.controlTransfer(0xC0, req, value, 0, buf, 2, TIMEOUT)
        fun out(req: Int, value: Int, index: Int) = ctrl(c, 0x40, req, value, index)
        fun setBaud() {
            val (a, b) = UsbSerial.ch34xBaud(baud)
            out(0x9A, 0x1312, a)
            out(0x9A, 0x0F2C, b)
        }
        inq(0x5F, 0)
        out(0xA1, 0, 0)
        setBaud()
        inq(0x95, 0x2518)
        out(0x9A, 0x2518, 0x00C3) // 수신·송신 켬, 8비트
        inq(0x95, 0x0706)
        out(0xA1, 0x501F, 0xD90A)
        setBaud()
        out(0xA4, 0x60.inv() and 0xFFFF, 0) // DTR+RTS (반전 논리)
        inq(0x95, 0x0706)
    }

    override fun read(buf: ByteArray, timeoutMs: Int): Int {
        val c = conn ?: return -1
        val ep = inEp ?: return -1
        val n = c.bulkTransfer(ep, raw, raw.size, timeoutMs)
        if (n <= 0) {
            // bulkTransfer는 시간 초과와 뽑힘을 둘 다 -1로 알려 줘서, 가끔 장치 목록으로 확인
            val now = System.currentTimeMillis()
            if (now - lastPresenceCheck > 1_000) {
                lastPresenceCheck = now
                if (manager?.deviceList?.values?.none { it.deviceName == device.deviceName } != false) return -1
            }
            return 0
        }
        return if (chip == UsbChip.FTDI) {
            UsbSerial.stripFtdiStatus(raw, n, ep.maxPacketSize, buf)
        } else {
            val m = minOf(n, buf.size)
            System.arraycopy(raw, 0, buf, 0, m)
            m
        }
    }

    override fun write(data: ByteArray) {
        val c = conn ?: return
        val ep = outEp ?: return
        c.bulkTransfer(ep, data, data.size, TIMEOUT)
    }

    override fun close() {
        conn?.let { c ->
            claimed.forEach { runCatching { c.releaseInterface(it) } }
            c.close()
        }
        claimed.clear()
        conn = null
    }

    companion object {
        private const val TIMEOUT = 1_000
    }
}
