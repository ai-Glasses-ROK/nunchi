import SwiftUI

/// 드론 화면: 드론 카메라 → (허브) Gemma 4 → 화면 출력. 영상 위에 비행컨트롤러 상태(HUD)와 분석 결과를 겹쳐 보여 줍니다.
struct DroneView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var camera: DroneCamera
    @ObservedObject var link: DroneLink
    @Environment(\.dismiss) private var dismiss
    @State private var question = ""
    @AppStorage("droneCameraSource") private var source = "virtual" // virtual | mjpeg
    @AppStorage("droneCameraURL") private var cameraURL = "http://192.168.4.1:81/stream"
    @AppStorage("droneUDPPort") private var udpPort = 14550

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    videoPanel
                    analyzeControls
                    Divider()
                    cameraSection
                    Divider()
                    telemetrySection
                }
                .padding(.bottom, 24)
            }
            .navigationTitle("드론 카메라 · Gemma 4")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("닫기") { dismiss() } }
            }
        }
    }

    // MARK: - 영상 + 겹쳐 보이는 정보

    private var videoPanel: some View {
        ZStack(alignment: .topLeading) {
            Rectangle().fill(Color.black)
            if let image = camera.frame {
                Image(uiImage: image).resizable().scaledToFit().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Text("카메라 꺼짐").foregroundStyle(.white).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            if link.telemetry.live || link.telemetry.hasVehicle {
                Text(hud)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.white)
                    .padding(6)
                    .background(Color.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 6))
                    .padding(6)
            }
            if model.droneAnswer.busy || !model.droneAnswer.text.isEmpty || !model.droneAnswer.error.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    Text("✨ 허브 Gemma 4" + (model.droneAnswer.busy ? " · 분석 중…" : ""))
                        .font(.caption2).foregroundStyle(.yellow)
                    Text(model.droneAnswer.error.isEmpty ? (model.droneAnswer.text.isEmpty ? "…" : model.droneAnswer.text) : model.droneAnswer.error)
                        .font(.subheadline).foregroundStyle(.white).lineLimit(4)
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.black.opacity(0.62))
                .frame(maxHeight: .infinity, alignment: .bottom)
            }
        }
        .aspectRatio(aspect, contentMode: .fit)
        .frame(maxWidth: .infinity)
    }

    private var aspect: CGFloat {
        guard let s = camera.frame?.size, s.height > 0 else { return 16 / 9 }
        return min(max(s.width / s.height, 0.75), 2.2)
    }

    private var hud: String {
        let t = link.telemetry
        guard t.hasVehicle else { return t.linkState }
        var line1 = "\(t.mode)  \(t.armed ? "ARMED" : "DISARMED")"
        if let v = t.voltage { line1 += String(format: "  %.1fV", v) }
        if let b = t.batteryPct { line1 += "  \(b)%" }
        var line2 = "GPS \(t.gpsFix >= 3 ? "3D" : DroneTelemetry.fixName(t.gpsFix)) \(max(t.satellites, 0))sat"
        if let a = t.relAlt { line2 += String(format: "  ALT %.1fm", a) }
        if let h = t.heading { line2 += "  HDG \(h)°" }
        let prearm = t.currentPrearm()
        let warn = prearm.isEmpty ? "" : "\n⚠ PreArm \(prearm.count)개 · " + prearm.map { $0.text.replacingOccurrences(of: "PreArm: ", with: "") }.joined(separator: " / ")
        return line1 + "\n" + line2 + warn
    }

    private var analyzeControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                TextField("무엇이 보여? (비우면 장면 설명)", text: $question).textFieldStyle(.roundedBorder)
                Button(model.droneAnswer.busy ? "분석 중" : "분석") {
                    Task { await model.analyzeDroneFrame(question: question) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(camera.frame == nil || model.droneAnswer.busy)
            }
            Toggle(isOn: Binding(get: { model.autoAnalyze }, set: { model.setAutoAnalyze($0, question: question) })) {
                VStack(alignment: .leading) {
                    Text("자동 분석 (6초마다)")
                    Text("아이폰은 허브(컴퓨터)의 Gemma 4로 분석해요").font(.caption2).foregroundStyle(.secondary)
                }
            }
            .disabled(camera.frame == nil)
            if !model.droneAnswer.caption.isEmpty || !model.droneAnswer.sources.isEmpty || !model.droneAnswer.meta.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    if !model.droneAnswer.text.isEmpty { Text(model.droneAnswer.text).font(.callout) }
                    if !model.droneAnswer.caption.isEmpty { Text("📷 캡션: \(model.droneAnswer.caption)").font(.caption) }
                    if !model.droneAnswer.sources.isEmpty { Text("참고 문서: " + model.droneAnswer.sources.joined(separator: ", ")).font(.caption) }
                    if !model.droneAnswer.meta.isEmpty { Text(model.droneAnswer.meta).font(.caption2).foregroundStyle(.secondary) }
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(.horizontal)
    }

    // MARK: - 카메라

    private var cameraSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("드론 카메라").font(.headline)
            Picker("영상", selection: $source) {
                Text("네트워크(MJPEG)").tag("mjpeg")
                Text("가상 카메라").tag("virtual")
            }
            .pickerStyle(.segmented)
            if source == "mjpeg" {
                TextField("MJPEG 주소", text: $cameraURL)
                    .textFieldStyle(.roundedBorder).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                Text("ESP32-CAM(:81/stream), 또는 컴퓨터에 꽂은 5.8GHz 수신기를 중계한 주소(scripts/video_relay.py, :8090/stream.mjpg). 아이폰은 USB 영상 수신기를 앱에서 직접 쓸 수 없어요.")
                    .font(.caption2).foregroundStyle(.secondary)
            } else {
                Text("앱에 든 합성 항공 영상(착륙장 H·도로·차)으로 Gemma 4 경로를 시험해요.").font(.caption2).foregroundStyle(.secondary)
            }
            HStack {
                Button(camera.frame == nil ? "카메라 켜기" : "다시 연결") {
                    if source == "mjpeg" { camera.startMJPEG(cameraURL) } else { camera.startVirtual() }
                }
                .buttonStyle(.borderedProminent)
                Button("끄기") {
                    model.setAutoAnalyze(false)
                    camera.stop()
                }
                .buttonStyle(.bordered)
            }
            Text("카메라: \(camera.status)").font(.caption).foregroundStyle(.secondary)
        }
        .padding(.horizontal)
    }

    // MARK: - 비행컨트롤러

    private var telemetrySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("비행컨트롤러 (픽스호크 MAVLink · 와이파이 UDP)").font(.headline)
            HStack {
                Text("UDP 포트")
                TextField("14550", value: $udpPort, format: .number.grouping(.never))
                    .textFieldStyle(.roundedBorder).keyboardType(.numberPad).frame(width: 90)
            }
            Text("ESP8266·ESP32 와이파이 텔레메트리, 또는 맥에 꽂은 픽스호크·텔레메트리 라디오를 중계: python3 scripts/mavlink_bridge.py --udp 아이폰IP:\(udpPort)")
                .font(.caption2).foregroundStyle(.secondary)
            HStack {
                Button("연결") { try? link.startUDP(port: UInt16(clamping: udpPort)) }.buttonStyle(.borderedProminent)
                Button("끊기") { link.stop() }.buttonStyle(.bordered)
            }
            Text("상태: \(link.telemetry.summary()) · 프레임 \(link.telemetry.frames)").font(.caption)
            if link.telemetry.hasVehicle {
                Text(link.telemetry.report()).font(.system(size: 11, design: .monospaced))
            }
        }
        .padding(.horizontal)
    }
}
