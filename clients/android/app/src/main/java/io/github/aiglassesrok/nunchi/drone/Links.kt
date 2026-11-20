package io.github.aiglassesrok.nunchi.drone

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothSocket
import android.content.Context
import java.io.Closeable
import java.io.IOException
import java.io.InputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketAddress
import java.net.SocketTimeoutException
import java.util.UUID

/**
 * 비행컨트롤러와 바이트를 주고받는 통로. 모든 함수는 IO 스레드에서 부릅니다.
 * read: 읽은 바이트 수, 시간 안에 아무것도 없으면 0, 끊어졌으면 -1
 */
interface ByteLink : Closeable {
    val description: String
    fun open()
    fun read(buf: ByteArray, timeoutMs: Int): Int
    fun write(data: ByteArray)
}

/**
 * 와이파이 텔레메트리 (ESP8266/ESP32 'mavesp' 모듈, 노트북의 MAVProxy·SITL 등).
 * 폰이 UDP 14550을 열고 기다리면 기체 쪽이 보내 오고, 폰은 마지막으로 보낸 쪽에 답합니다(미션플래너·QGC와 같은 방식).
 */
class UdpLink(private val port: Int) : ByteLink {
    private var socket: DatagramSocket? = null
    @Volatile private var peer: SocketAddress? = null

    override val description: String
        get() = "Wi-Fi UDP :$port" + (peer?.let { " ← ${it.toString().trimStart('/')}" } ?: "")

    override fun open() {
        // apply 안에서 그냥 port라고 쓰면 DatagramSocket.getPort()(연결 전 -1)로 해석되므로 밖에서 꺼내 둠
        val listenPort = port
        socket = DatagramSocket(null).apply {
            reuseAddress = true
            bind(InetSocketAddress(listenPort))
        }
    }

    override fun read(buf: ByteArray, timeoutMs: Int): Int {
        val s = socket ?: return -1
        s.soTimeout = timeoutMs.coerceAtLeast(1)
        val packet = DatagramPacket(buf, buf.size)
        return try {
            s.receive(packet)
            peer = packet.socketAddress
            packet.length
        } catch (_: SocketTimeoutException) {
            0
        }
    }

    override fun write(data: ByteArray) {
        val to = peer ?: return
        socket?.send(DatagramPacket(data, data.size, to))
    }

    override fun close() {
        socket?.close()
    }
}

/** TCP 클라이언트 (ArduPilot SITL의 tcp:5760, ESP32 TCP 브리지, MAVProxy --out tcpin:…) */
class TcpLink(private val host: String, private val port: Int) : ByteLink {
    private var socket: Socket? = null
    override val description = "TCP $host:$port"

    override fun open() {
        val target = InetSocketAddress(host, port) // apply 밖에서 (안에서는 port가 Socket.getPort()로 해석됨)
        socket = Socket().apply {
            connect(target, 5_000)
            tcpNoDelay = true
        }
    }

    override fun read(buf: ByteArray, timeoutMs: Int): Int {
        val s = socket ?: return -1
        s.soTimeout = timeoutMs.coerceAtLeast(1)
        return try {
            s.getInputStream().read(buf)
        } catch (_: SocketTimeoutException) {
            0
        }
    }

    override fun write(data: ByteArray) {
        socket?.getOutputStream()?.write(data)
    }

    override fun close() {
        socket?.close()
    }
}

/**
 * 블루투스 클래식 SPP (HC-05·HC-06 모듈을 픽스호크 TELEM2에 연결). 안드로이드 설정에서 먼저 페어링(기본 PIN 1234 또는 0000)해 두세요.
 * BLUETOOTH_CONNECT 권한은 앱 시작 때 MainActivity가 받습니다.
 */
@SuppressLint("MissingPermission")
class BluetoothSppLink(private val context: Context, private val address: String, private val name: String) : ByteLink {
    private var socket: BluetoothSocket? = null
    private var input: InputStream? = null
    override val description = "블루투스 $name"

    @SuppressLint("DiscouragedPrivateApi")
    override fun open() {
        val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
            ?: throw IOException("이 폰에서는 블루투스를 쓸 수 없어요")
        if (!adapter.isEnabled) throw IOException("블루투스가 꺼져 있어요")
        val device = adapter.getRemoteDevice(address)
        // HC-05/06은 표준 SPP로 붙지만, 일부 모듈·폰 조합은 보안 연결이나 서비스 검색에서 실패해서 차례로 시도합니다.
        val attempts: List<() -> BluetoothSocket> = listOf(
            { device.createRfcommSocketToServiceRecord(SPP_UUID) },
            { device.createInsecureRfcommSocketToServiceRecord(SPP_UUID) },
            { device.javaClass.getMethod("createRfcommSocket", Int::class.javaPrimitiveType).invoke(device, 1) as BluetoothSocket },
        )
        var last: Exception? = null
        for (make in attempts) {
            val s = runCatching { make() }.getOrElse { last = it as? Exception; null } ?: continue
            try {
                s.connect()
                socket = s
                input = s.inputStream
                return
            } catch (e: IOException) {
                last = e
                runCatching { s.close() }
            }
        }
        throw IOException("블루투스 연결 실패: ${last?.message ?: "알 수 없음"} (페어링·전원·TELEM2 배선 확인)")
    }

    override fun read(buf: ByteArray, timeoutMs: Int): Int {
        val inp = input ?: return -1
        val deadline = System.currentTimeMillis() + timeoutMs
        // BluetoothSocket 입력에는 시간 제한이 없어서 available()로 기다립니다 (끊기면 IOException)
        while (true) {
            val avail = inp.available()
            if (avail > 0) return inp.read(buf, 0, minOf(avail, buf.size))
            if (System.currentTimeMillis() >= deadline) return 0
            Thread.sleep(10)
        }
    }

    override fun write(data: ByteArray) {
        socket?.outputStream?.write(data)
    }

    override fun close() {
        socket?.close()
    }

    companion object {
        val SPP_UUID: UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
    }
}

/**
 * 가상 드론: 실물 없이 전체 경로(바이트 → 파서 → 상태 → RAG)를 시험합니다. 가상 안경과 같은 역할.
 * [VirtualDrone]이 만든 진짜 MAVLink v2 바이트를 그대로 흘려보내므로 USB로 받은 것과 똑같이 처리됩니다.
 */
class VirtualDroneLink(private val drone: VirtualDrone = VirtualDrone()) : ByteLink {
    private var pending = ByteArray(0)
    private var started = 0L
    override val description = "가상 드론 (시나리오)"

    override fun open() {
        started = System.currentTimeMillis()
    }

    override fun read(buf: ByteArray, timeoutMs: Int): Int {
        if (pending.isEmpty()) {
            Thread.sleep(minOf(timeoutMs, 100).toLong())
            pending = drone.step(System.currentTimeMillis() - started)
        }
        val n = minOf(pending.size, buf.size)
        System.arraycopy(pending, 0, buf, 0, n)
        pending = pending.copyOfRange(n, pending.size)
        return n
    }

    override fun write(data: ByteArray) {
        drone.onGcsBytes(data)
    }

    override fun close() = Unit
}
