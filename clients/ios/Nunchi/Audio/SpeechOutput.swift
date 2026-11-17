import AVFoundation

/// 답을 소리로 내보냅니다.
/// - 폰 음성 합성(AVSpeechSynthesizer, 한국어): 오프라인, 지연이 가장 짧음. 문장이 도착하는 대로 차례로 읽습니다.
/// - 허브 음성(WAV): 받은 순서대로 이어서 재생합니다.
///
/// 녹음이 끝나면 오디오 세션을 `.playback` 으로 바꿔서, 안경 스피커로 HFP(통화 음질)가 아닌
/// A2DP(음악 음질)로 나가게 합니다.
final class SpeechOutput: NSObject, AVSpeechSynthesizerDelegate, AVAudioPlayerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var player: AVAudioPlayer?
    private var wavQueue: [Data] = []
    private let voice = AVSpeechSynthesisVoice(language: "ko-KR")

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func prepareForPlayback() {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try? session.setActive(true)
    }

    func speak(_ text: String) {
        let line = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !line.isEmpty else { return }
        let utterance = AVSpeechUtterance(string: line)
        utterance.voice = voice
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 1.05
        synthesizer.speak(utterance)  // 여러 번 부르면 차례로 이어 읽음
    }

    func play(wav: Data) {
        wavQueue.append(wav)
        if player?.isPlaying != true { playNext() }
    }

    func stopAll() {
        synthesizer.stopSpeaking(at: .immediate)
        wavQueue.removeAll()
        player?.stop()
        player = nil
    }

    private func playNext() {
        guard !wavQueue.isEmpty else { return }
        let data = wavQueue.removeFirst()
        do {
            let p = try AVAudioPlayer(data: data)
            p.delegate = self
            player = p
            p.play()
        } catch {
            playNext()
        }
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        playNext()
    }
}
