package io.github.aiglassesrok.nunchi.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.aiglassesrok.nunchi.DroneAnalysis
import io.github.aiglassesrok.nunchi.MainViewModel
import io.github.aiglassesrok.nunchi.camera.CameraFrame
import io.github.aiglassesrok.nunchi.drone.DroneTelemetry
import io.github.aiglassesrok.nunchi.drone.LinkState
import io.github.aiglassesrok.nunchi.drone.MavNames
import java.util.Locale

/**
 * 드론 화면: 드론 카메라 → Gemma 4 → 화면 출력.
 * 영상 위에 비행컨트롤러 상태(HUD)와 Gemma 4의 분석 결과를 겹쳐 보여 줍니다.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun DroneScreen(viewModel: MainViewModel, onBack: () -> Unit) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val drone by viewModel.drone.collectAsStateWithLifecycle()
    val frame by viewModel.cameraFrame.collectAsStateWithLifecycle()
    val camStatus by viewModel.cameraStatus.collectAsStateWithLifecycle()
    var question by remember { mutableStateOf("") }
    val s = ui.settings
    BackHandler(onBack = onBack)

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("드론 카메라 · Gemma 4")
                        Text(
                            "카메라: $camStatus · 기체: ${drone.summary()}",
                            style = MaterialTheme.typography.labelSmall,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "뒤로") } },
            )
        },
    ) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding).imePadding(),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item { VideoPanel(frame, drone, ui.analysis) }

            item {
                Column(Modifier.padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = question,
                            onValueChange = { question = it },
                            modifier = Modifier.weight(1f),
                            singleLine = true,
                            placeholder = { Text("무엇이 보여? (비우면 장면 설명)") },
                        )
                        Button(onClick = { viewModel.analyzeDroneFrame(question) }, enabled = frame != null && !ui.analysis.busy) {
                            Text(if (ui.analysis.busy) "분석 중" else "분석")
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("자동 분석 (6초마다)")
                            Text(
                                "엔진: " + if (s.engine == "phone") "폰 Gemma 4 (모델 없으면 허브)" else "허브 Gemma 4",
                                style = MaterialTheme.typography.labelSmall,
                            )
                        }
                        Switch(
                            checked = ui.autoAnalyze,
                            onCheckedChange = { viewModel.setAutoAnalyze(it, question = question.ifBlank { MainViewModel.DEFAULT_DRONE_QUESTION }) },
                            enabled = frame != null,
                        )
                    }
                    AnalysisDetails(ui.analysis)
                }
            }

            item { HorizontalDivider() }
            item {
                Section("드론 카메라") {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        for ((key, label) in listOf("usb" to "USB 영상 수신기", "mjpeg" to "네트워크(MJPEG)", "virtual" to "가상 카메라")) {
                            FilterChip(selected = s.cameraSource == key, onClick = { viewModel.saveSettings(s.copy(cameraSource = key)) }, label = { Text(label) })
                        }
                    }
                    if (s.cameraSource == "mjpeg") {
                        var url by remember(s.cameraUrl) { mutableStateOf(s.cameraUrl) }
                        OutlinedTextField(
                            url,
                            { url = it },
                            label = { Text("MJPEG 주소 (ESP32-CAM 등)") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        if (url != s.cameraUrl) OutlinedButton(onClick = { viewModel.saveSettings(s.copy(cameraUrl = url)) }) { Text("주소 저장") }
                    }
                    Text(
                        when (s.cameraSource) {
                            "usb" -> "이 키트의 아날로그 FPV 카메라 → 5.8GHz VTX 영상을 'OTG(UVC) 5.8GHz 수신기'로 받아 폰에 꽂으세요. 수신기 채널을 VTX 채널과 맞춰야 해요."
                            "mjpeg" -> "와이파이로 영상을 보내는 카메라(ESP32-CAM :81/stream, IP Webcam /video 등)."
                            else -> "앱에 든 합성 항공 영상(착륙장 H·도로·차)으로 Gemma 4 경로를 시험해요."
                        },
                        style = MaterialTheme.typography.labelSmall,
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { viewModel.startCamera() }) { Text(if (frame == null) "카메라 켜기" else "다시 연결") }
                        OutlinedButton(onClick = viewModel::stopCamera) { Text("끄기") }
                    }
                }
            }

            item { HorizontalDivider() }
            item { FlightControllerSection(viewModel, drone) }

            if (drone.hasVehicle) {
                item {
                    Section("실시간 기체 상태 (RAG에 들어가는 글)") {
                        Text(drone.report(), fontFamily = FontFamily.Monospace, fontSize = 12.sp, lineHeight = 16.sp)
                    }
                }
            }
            item { Box(Modifier.padding(8.dp)) }
        }
    }
}

@Composable
private fun VideoPanel(frame: CameraFrame?, drone: DroneTelemetry, analysis: DroneAnalysis) {
    val aspect = frame?.let { it.bitmap.width.toFloat() / it.bitmap.height } ?: (16f / 9f)
    Box(Modifier.fillMaxWidth().aspectRatio(aspect.coerceIn(0.75f, 2.2f)).background(Color.Black)) {
        if (frame != null) {
            Image(
                bitmap = frame.bitmap.asImageBitmap(),
                contentDescription = "드론 카메라 영상",
                contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize(),
            )
        } else {
            Text("카메라 꺼짐", color = Color.White, modifier = Modifier.align(Alignment.Center))
        }
        if (drone.linkState != LinkState.DISCONNECTED) {
            Text(
                hud(drone),
                color = Color.White,
                fontSize = 12.sp,
                fontFamily = FontFamily.Monospace,
                maxLines = 4,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.align(Alignment.TopStart).padding(6.dp)
                    .background(Color.Black.copy(alpha = 0.55f), RoundedCornerShape(6.dp)).padding(horizontal = 6.dp, vertical = 3.dp),
            )
        }
        if (analysis.busy || analysis.text.isNotBlank() || analysis.error.isNotBlank()) {
            Column(
                Modifier.align(Alignment.BottomStart).fillMaxWidth().background(Color.Black.copy(alpha = 0.62f)).padding(8.dp),
            ) {
                Text(
                    "✨ ${analysis.engine.ifEmpty { "Gemma 4" }}" + if (analysis.busy) " · 분석 중…" else "",
                    color = Color(0xFFFFD54F),
                    fontSize = 11.sp,
                )
                val body = analysis.error.ifBlank { analysis.text.ifBlank { "…" } }
                Text(body, color = Color.White, fontSize = 14.sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

private fun hud(t: DroneTelemetry): String {
    if (!t.hasVehicle) return t.summary()
    val f = { v: Double, d: Int -> "%.${d}f".format(Locale.US, v) }
    val line1 = listOfNotNull(
        MavNames.modeKo(t.mode).substringBefore('('),
        if (t.armed) "ARMED" else "DISARMED",
        t.voltage?.let { "${f(it, 1)}V" },
        t.batteryPct?.let { "$it%" },
    ).joinToString("  ")
    val line2 = listOfNotNull(
        "GPS ${if (t.gpsFix >= 3) "3D" else MavNames.gpsFix(t.gpsFix)} ${maxOf(t.satellites, 0)}sat",
        t.relAlt?.let { "ALT ${f(it, 1)}m" },
        t.groundSpeed?.let { "SPD ${f(it, 1)}m/s" },
        t.heading?.let { "HDG $it°" },
    ).joinToString("  ")
    val now = System.currentTimeMillis()
    val prearm = t.currentPrearm(now)
    val warn = when {
        prearm.size > 1 -> "\n⚠ PreArm ${prearm.size}개 · " + prearm.joinToString(" / ") { it.text.removePrefix("PreArm: ") }
        prearm.size == 1 -> "\n⚠ ${prearm[0].text}"
        else -> t.messages.lastOrNull { it.severity <= 4 && !it.text.startsWith("PreArm") && now - it.timeMs < 45_000 }?.let { "\n⚠ ${it.text}" } ?: ""
    }
    return "$line1\n$line2$warn"
}

@Composable
private fun AnalysisDetails(a: DroneAnalysis) {
    if (a.caption.isBlank() && a.sources.isEmpty() && a.meta.isBlank()) return
    Column(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp)).padding(8.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        if (a.text.isNotBlank()) Text(a.text, style = MaterialTheme.typography.bodyMedium)
        if (a.caption.isNotBlank()) Text("📷 캡션: ${a.caption}", style = MaterialTheme.typography.labelSmall)
        if (a.sources.isNotEmpty()) Text("참고 문서: " + a.sources.joinToString(", "), style = MaterialTheme.typography.labelSmall)
        if (a.meta.isNotBlank()) Text(a.meta, style = MaterialTheme.typography.labelSmall)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun FlightControllerSection(viewModel: MainViewModel, drone: DroneTelemetry) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val s = ui.settings
    Section("비행컨트롤러 (픽스호크 MAVLink)") {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for ((key, label) in listOf("usb" to "USB", "bt" to "블루투스", "udp" to "Wi-Fi UDP", "tcp" to "TCP", "virtual" to "가상 드론")) {
                FilterChip(selected = s.droneLink == key, onClick = { viewModel.saveSettings(s.copy(droneLink = key)) }, label = { Text(label) })
            }
        }
        when (s.droneLink) {
            "usb", "bt" -> {
                if (s.droneLink == "usb") {
                    val devices = remember(drone.linkState) { viewModel.usbSerialDevices() }
                    Text(
                        if (devices.isEmpty()) "꽂힌 USB 시리얼 장치 없음 — 텔레메트리 라디오 V5(지상용)나 픽스호크를 OTG로 연결" else devices.joinToString("\n"),
                        style = MaterialTheme.typography.labelSmall,
                    )
                } else {
                    val bonded = remember { viewModel.bondedBluetooth() }
                    if (bonded.isEmpty()) Text("페어링된 기기 없음 — 안드로이드 설정에서 HC-05/06 페어링(PIN 1234/0000)", style = MaterialTheme.typography.labelSmall)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        for ((name, addr) in bonded) {
                            FilterChip(selected = s.droneBtAddress == addr, onClick = { viewModel.saveSettings(s.copy(droneBtAddress = addr)) }, label = { Text(name) })
                        }
                    }
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.Center) {
                    Text("보드레이트", style = MaterialTheme.typography.labelMedium, modifier = Modifier.align(Alignment.CenterVertically))
                    for (baud in listOf(57600, 115200)) {
                        FilterChip(selected = s.droneBaud == baud, onClick = { viewModel.saveSettings(s.copy(droneBaud = baud)) }, label = { Text("$baud") })
                    }
                }
                Text(
                    if (s.droneLink == "usb") "텔레메트리 라디오·TELEM 포트는 57600(ArduPilot 기본). 픽스호크 본체 USB는 아무 값이나 돼요." else "TELEM2 기본 57600 (SERIAL2_BAUD=57).",
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            "udp" -> {
                var port by remember(s.droneUdpPort) { mutableStateOf(s.droneUdpPort.toString()) }
                OutlinedTextField(port, { port = it.filter(Char::isDigit) }, label = { Text("UDP 포트 (기본 14550)") }, singleLine = true)
                if (port != s.droneUdpPort.toString()) OutlinedButton(onClick = { port.toIntOrNull()?.let { viewModel.saveSettings(s.copy(droneUdpPort = it)) } }) { Text("저장") }
                Text("ESP8266/ESP32 와이파이 텔레메트리, 노트북의 MAVProxy(--out udp:폰IP:14550)", style = MaterialTheme.typography.labelSmall)
            }
            "tcp" -> {
                var addr by remember(s.droneTcp) { mutableStateOf(s.droneTcp) }
                OutlinedTextField(addr, { addr = it.trim() }, label = { Text("주소:포트 (SITL 기본 5760)") }, singleLine = true)
                if (addr != s.droneTcp) OutlinedButton(onClick = { viewModel.saveSettings(s.copy(droneTcp = addr)) }) { Text("저장") }
            }
            else -> Text("100초 시나리오(부팅→PreArm 경고→이륙·비행→배터리 부족 귀환)를 반복하는 가상 ArduCopter예요.", style = MaterialTheme.typography.labelSmall)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { viewModel.connectDrone() }) { Text(if (drone.linkState == LinkState.DISCONNECTED) "연결" else "다시 연결") }
            OutlinedButton(onClick = viewModel::disconnectDrone, enabled = drone.linkState != LinkState.DISCONNECTED) { Text("끊기") }
        }
        Text(
            "상태: ${drone.summary()}" + (if (drone.link.isNotEmpty()) " · ${drone.link}" else "") +
                (if (drone.frames > 0) " · 프레임 ${drone.frames}" + if (drone.crcErrors > 0) " (CRC 오류 ${drone.crcErrors})" else "" else "") +
                (if (drone.error.isNotEmpty()) "\n⚠ ${drone.error}" else ""),
            style = MaterialTheme.typography.labelMedium,
        )
        if (drone.messages.isNotEmpty() || drone.events.isNotEmpty()) {
            Text("최근 메시지", style = MaterialTheme.typography.labelLarge)
            val recent = (drone.messages + drone.events).sortedByDescending { it.timeMs }.take(8)
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                for (e in recent) {
                    Text(
                        "${DroneTelemetry.clock(e.timeMs)} " + (if (e.fromVehicle) "[${MavNames.severity(e.severity)}] " else "• ") + e.text,
                        style = MaterialTheme.typography.labelSmall,
                        color = if (e.severity <= 4) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Column(Modifier.padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall)
        content()
    }
}
