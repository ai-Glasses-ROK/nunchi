import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @State private var input = ""
    @State private var showSettings = false
    @AppStorage(SettingsKey.mode) private var mode = AssistantMode.hub.rawValue

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                statusBar
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 10) {
                            ForEach(model.messages) { m in
                                MessageRow(message: m).id(m.id)
                            }
                        }
                        .padding()
                    }
                    .onChange(of: model.messages.count) {
                        if let last = model.messages.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
                    }
                }
                composer
            }
            .navigationTitle("Nunchi")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("초기화") { model.resetConversation() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("설정")
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView().environmentObject(model)
            }
            .task {
                #if DEBUG
                if UserDefaults.standard.bool(forKey: "NunchiShowSettings") { showSettings = true }
                #endif
                if mode == AssistantMode.hub.rawValue { await model.checkHub() }
            }
        }
    }

    private var statusBar: some View {
        HStack(spacing: 8) {
            Label(mode == AssistantMode.hub.rawValue ? "허브" : "단독", systemImage: mode == AssistantMode.hub.rawValue ? "desktopcomputer" : "iphone")
            Text(mode == AssistantMode.hub.rawValue ? model.hubStatus : model.kbStatus)
                .lineLimit(1)
                .foregroundStyle(.secondary)
            Spacer()
            GlassesBadge(glasses: model.glasses)
        }
        .font(.caption)
        .padding(.horizontal)
        .padding(.vertical, 6)
        .background(.bar)
    }

    private var composer: some View {
        HStack(spacing: 10) {
            Button {
                let q = input
                input = ""
                Task { await model.askWithGlassesPhoto(question: q) }
            } label: {
                Image(systemName: "camera.fill").font(.title3).frame(width: 44, height: 44)
            }
            .buttonStyle(.bordered)
            .accessibilityLabel("안경으로 사진 찍어 묻기")
            .disabled(model.isBusy || model.isRecording)

            TextField("질문 입력", text: $input)
                .textFieldStyle(.roundedBorder)
                .submitLabel(.send)
                .onSubmit {
                    let q = input
                    input = ""
                    Task { await model.ask(q) }
                }

            Button {
                Task { await model.toggleRecording() }
            } label: {
                Image(systemName: model.isRecording ? "stop.fill" : "mic.fill")
                    .font(.title2)
                    .foregroundStyle(.white)
                    .frame(width: 64, height: 64)
                    .background(Circle().fill(model.isRecording ? Color.red : Color.accentColor))
            }
            .accessibilityLabel(model.isRecording ? "녹음 끝내고 보내기" : "눌러서 말하기")
            .disabled(model.isBusy && !model.isRecording)
        }
        .padding()
        .background(.bar)
    }
}

/// 안경 연결 상태 표시. GlassesController를 직접 관찰해야 상태가 바뀔 때 화면이 갱신됩니다
/// (AppModel 안에 든 ObservableObject의 변화는 AppModel을 관찰하는 뷰에 자동 전달되지 않음).
struct GlassesBadge: View {
    @ObservedObject var glasses: GlassesController

    var body: some View {
        Label(glasses.registrationText, systemImage: "eyeglasses")
            .lineLimit(1)
            .foregroundStyle(glasses.isRegistered ? .green : .secondary)
    }
}

struct MessageRow: View {
    let message: AppModel.Message

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 40) }
            VStack(alignment: .leading, spacing: 6) {
                if let image = message.image {
                    Image(uiImage: image).resizable().scaledToFit().frame(maxWidth: 220).clipShape(RoundedRectangle(cornerRadius: 10))
                }
                Text(message.text.isEmpty ? "…" : message.text)
                    .textSelection(.enabled)
                if !message.sources.isEmpty {
                    DisclosureGroup("참고 문서 \(message.sources.count)개") {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(message.sources, id: \.self) { Text($0).font(.caption) }
                        }
                    }
                    .font(.caption)
                }
                if !message.meta.isEmpty {
                    Text(message.meta).font(.caption2).foregroundStyle(.secondary)
                }
            }
            .padding(10)
            .background(background)
            .foregroundStyle(message.role == .user ? Color.white : Color.primary)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            if message.role != .user { Spacer(minLength: 40) }
        }
    }

    private var background: Color {
        switch message.role {
        case .user: return .accentColor
        case .assistant: return Color(.secondarySystemBackground)
        case .notice: return Color(.tertiarySystemBackground)
        }
    }
}
