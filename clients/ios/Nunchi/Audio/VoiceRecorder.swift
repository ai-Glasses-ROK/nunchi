import AVFoundation

enum RecorderError: LocalizedError {
    case noGlassesMic
    case noInput

    var errorDescription: String? {
        switch self {
        case .noGlassesMic:
            return "안경 마이크를 찾지 못했어요. 안경이 블루투스로 연결돼 있는지 확인하거나, 설정에서 '폰 마이크'로 바꿔 보세요."
        case .noInput:
            return "사용할 수 있는 마이크가 없어요."
        }
    }
}

/// 질문 녹음기. 안경 마이크(블루투스 HFP) 또는 폰 마이크로 녹음해 16kHz 모노 WAV를 만듭니다.
///
/// - 안경 마이크: AVAudioSession 옵션 `.allowBluetoothHFP` + 선호 입력을 HFP 포트로 지정 (Meta 공식 샘플과 같은 방식)
/// - 녹음 콜백은 오디오 스레드에서 오므로 샘플 버퍼는 잠금(lock)으로 보호합니다.
final class VoiceRecorder {
    private let engine = AVAudioEngine()
    private let targetFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    private var converter: AVAudioConverter?
    private var converterInputFormat: AVAudioFormat?
    private var samples: [Float] = []
    private let lock = NSLock()

    private(set) var isRecording = false
    private(set) var routeDescription = ""

    func start(source: MicSource) throws {
        let session = AVAudioSession.sharedInstance()
        switch source {
        case .glasses:
            // HFP 옵션을 켜야 블루투스 헤드셋(안경)의 마이크가 입력 목록에 나타납니다.
            try session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetoothHFP])
        case .phone:
            try session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetoothA2DP, .defaultToSpeaker])
        }
        try session.setActive(true)

        if source == .glasses {
            guard let hfp = session.availableInputs?.first(where: { $0.portType == .bluetoothHFP }) else {
                throw RecorderError.noGlassesMic
            }
            try session.setPreferredInput(hfp)
        } else if let builtIn = session.availableInputs?.first(where: { $0.portType == .builtInMic }) {
            try session.setPreferredInput(builtIn)
        }
        routeDescription = session.currentRoute.inputs.map { "\($0.portName) · \(Int(session.sampleRate))Hz" }.joined(separator: ", ")

        lock.lock()
        samples.removeAll(keepingCapacity: true)
        lock.unlock()
        converter = nil
        converterInputFormat = nil

        let input = engine.inputNode
        guard input.outputFormat(forBus: 0).sampleRate > 0 else { throw RecorderError.noInput }
        // format: nil → 입력 노드의 실제 형식 사용 (블루투스 경로 전환으로 샘플레이트가 바뀌어도 안전)
        input.installTap(onBus: 0, bufferSize: 2048, format: nil) { [weak self] buffer, _ in
            self?.append(buffer)
        }
        engine.prepare()
        try engine.start()
        isRecording = true
    }

    /// 녹음을 멈추고 WAV 데이터를 돌려줍니다.
    func stop() -> Data {
        if isRecording {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
        }
        isRecording = false
        lock.lock()
        let pcm = samples
        lock.unlock()
        return WavEncoder.encode(samples: pcm, sampleRate: Int(targetFormat.sampleRate))
    }

    var recordedSeconds: Double {
        lock.lock()
        defer { lock.unlock() }
        return Double(samples.count) / targetFormat.sampleRate
    }

    private func append(_ buffer: AVAudioPCMBuffer) {
        // 입력 형식이 바뀌면(HFP 8kHz ↔ 내장 마이크 48kHz) 변환기를 새로 만듭니다.
        if converter == nil || converterInputFormat != buffer.format {
            converter = AVAudioConverter(from: buffer.format, to: targetFormat)
            converterInputFormat = buffer.format
        }
        guard let converter else { return }
        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else { return }

        var supplied = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if supplied {
                status.pointee = .noDataNow
                return nil
            }
            supplied = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let ch = out.floatChannelData?[0], out.frameLength > 0 else { return }
        let chunk = UnsafeBufferPointer(start: ch, count: Int(out.frameLength))
        lock.lock()
        samples.append(contentsOf: chunk)
        lock.unlock()
    }
}

enum WavEncoder {
    /// Float(-1~1) 샘플 → 16bit PCM WAV
    static func encode(samples: [Float], sampleRate: Int) -> Data {
        var data = Data(capacity: 44 + samples.count * 2)
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
        let byteCount = UInt32(samples.count * 2)
        data.append(Data("RIFF".utf8)); u32(36 + byteCount); data.append(Data("WAVE".utf8))
        data.append(Data("fmt ".utf8)); u32(16); u16(1); u16(1)
        u32(UInt32(sampleRate)); u32(UInt32(sampleRate * 2)); u16(2); u16(16)
        data.append(Data("data".utf8)); u32(byteCount)
        for s in samples {
            let v = Int16(max(-1, min(1, s)) * 32767)
            withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) }
        }
        return data
    }
}
