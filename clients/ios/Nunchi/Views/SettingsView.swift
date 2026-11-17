import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @AppStorage(SettingsKey.hubURL) private var hubURL = "http://192.168.0.10:8765"
    @AppStorage(SettingsKey.hubToken) private var hubToken = ""
    @AppStorage(SettingsKey.mode) private var mode = AssistantMode.hub.rawValue
    @AppStorage(SettingsKey.mic) private var mic = MicSource.glasses.rawValue
    @AppStorage(SettingsKey.voice) private var voice = VoiceOutput.phone.rawValue
    @State private var importing = false

    var body: some View {
        NavigationStack {
            Form {
                Section("동작 방식") {
                    Picker("모드", selection: $mode) {
                        ForEach(AssistantMode.allCases) { Text($0.label).tag($0.rawValue) }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                }

                Section {
                    TextField("http://192.168.0.10:8765", text: $hubURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("토큰 (NUNCHI_TOKEN을 정했을 때만)", text: $hubToken)
                    Button("연결 테스트") { Task { await model.checkHub() } }
                    Text(model.hubStatus).font(.footnote).foregroundStyle(.secondary)
                } header: {
                    Text("허브")
                } footer: {
                    Text("허브 컴퓨터에서 `nunchi serve --host 0.0.0.0` 실행 후 화면에 나온 주소를 적으세요. 폰과 컴퓨터는 같은 와이파이(인터넷 연결은 없어도 됨)에 있어야 합니다.")
                }

                Section("소리") {
                    Picker("마이크", selection: $mic) {
                        ForEach(MicSource.allCases) { Text($0.label).tag($0.rawValue) }
                    }
                    Picker("답 읽기", selection: $voice) {
                        ForEach(VoiceOutput.allCases) { Text($0.label).tag($0.rawValue) }
                    }
                }

                GlassesSection(glasses: model.glasses)

                if VirtualGlasses.isSupported {
                    VirtualGlassesSection(virtual: VirtualGlasses.shared)
                }

                Section {
                    LabeledContent("지식베이스", value: model.kbStatus)
                    LabeledContent("기기 내 LLM", value: model.llmStatus)
                    Button("허브에서 지식베이스 받기") { Task { await model.downloadKnowledgeBase() } }
                    Button("파일에서 kb.json 불러오기") { importing = true }
                } header: {
                    Text("단독 모드 준비")
                } footer: {
                    Text("허브와 같은 와이파이에 있을 때 한 번 받아 두면, 그 다음부터는 네트워크 없이 폰 안에서 검색·답변합니다. 허브에서 `nunchi export-mobile -o kb.json` 으로 만든 파일을 AirDrop/파일 앱으로 넣어도 됩니다.")
                }
            }
            .navigationTitle("설정")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("완료") { dismiss() } }
            }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.json]) { result in
                if case .success(let url) = result { model.importKnowledgeBase(from: url) }
            }
        }
    }
}

/// 안경 연결(등록) 상태와 버튼. GlassesController를 직접 관찰합니다.
struct GlassesSection: View {
    @ObservedObject var glasses: GlassesController

    var body: some View {
        Section {
            LabeledContent("등록", value: glasses.registrationText)
            LabeledContent("주변 안경", value: "\(glasses.deviceCount)개")
            LabeledContent("세션", value: glasses.sessionText)
            if glasses.isRegistered {
                Button("연결 해제", role: .destructive) { Task { await glasses.unregister() } }
            } else {
                Button("Meta AI 앱과 연결 (처음 한 번, 인터넷 필요)") { Task { await glasses.register() } }
            }
            if let error = glasses.lastError {
                Text(error).font(.footnote).foregroundStyle(.red)
            }
        } header: {
            Text("안경 (Meta DAT)")
        } footer: {
            Text("Meta AI 앱 > 설정 > 내 안경 > 개발자 모드를 먼저 켜세요. 카메라 권한은 '항상 허용'을 고르면 오프라인에서도 다시 묻지 않습니다.")
        }
    }
}

/// 실물 안경이 없을 때 쓰는 가상 안경(MockDeviceKit) 스위치. Debug 빌드에서만 보입니다.
struct VirtualGlassesSection: View {
    @ObservedObject var virtual: VirtualGlasses

    var body: some View {
        Section {
            Toggle("가상 안경 사용", isOn: Binding(
                get: { virtual.isOn },
                set: { $0 ? virtual.turnOn() : virtual.turnOff() }
            ))
            LabeledContent("상태", value: virtual.status)
            #if !targetEnvironment(simulator)
            Toggle("폰 카메라를 안경 카메라로", isOn: $virtual.usePhoneCamera)
                .disabled(virtual.isOn)
            #endif
        } header: {
            Text("가상 안경 (개발용)")
        } footer: {
            Text("실물 안경 없이 등록·세션·카메라 흐름을 시험합니다. 기본은 예시 계기판(E2) 영상·사진이 들어가고, 실제 아이폰에서는 켜기 전에 '폰 카메라'를 고르면 폰 뒷면 카메라가 안경 카메라 역할을 합니다. 마이크는 '폰 마이크'로 바꿔서 쓰세요.")
        }
    }
}
