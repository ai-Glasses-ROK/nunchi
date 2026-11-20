import AVFoundation
import UIKit

/// 드론 카메라 (Android `camera/DroneCamera.kt`의 아이폰판).
/// 아이폰은 USB 영상 수신기(UVC)를 앱에서 쓸 수 없어서 와이파이 영상(MJPEG)과 가상 영상을 지원합니다.
///  - 네트워크: ESP32-CAM(`http://…:81/stream`), IP 카메라, 또는 컴퓨터에 꽂은 5.8GHz 수신기·카메라를 `scripts/video_relay.py`로 중계한 주소
///  - 가상: 앱에 든 합성 항공 영상(착륙장 H·도로·차)
@MainActor
final class DroneCamera: ObservableObject {
    @Published private(set) var frame: UIImage?
    @Published private(set) var status = "꺼짐"
    private var latestJPEG: Data?
    private var task: Task<Void, Never>?
    private var stream: MJPEGStream?
    private var count = 0
    private var windowStart = Date()
    private var sourceName = ""

    func startVirtual() {
        stop()
        sourceName = "가상 드론 카메라"
        status = "\(sourceName) · 준비 중"
        task = Task { await runVirtual() }
    }

    func startMJPEG(_ text: String) {
        stop()
        var s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !s.hasPrefix("http://") && !s.hasPrefix("https://") { s = "http://" + s }
        guard let url = URL(string: s) else { status = "주소가 올바르지 않아요"; return }
        sourceName = "네트워크 카메라"
        status = "\(sourceName) · 연결 중…"
        let st = MJPEGStream(url: url) { [weak self] image, jpeg in
            Task { @MainActor in self?.deliver(image, jpeg) }
        } onError: { [weak self] message in
            Task { @MainActor in self?.status = "카메라 오류: \(message)" }
        }
        st.start()
        stream = st
    }

    func stop() {
        task?.cancel(); task = nil
        stream?.stop(); stream = nil
        status = "꺼짐"
    }

    /// Gemma에 보낼 JPEG (긴 변 768px 이하)
    func jpegForAnalysis(maxSide: CGFloat = 768) -> Data? {
        guard let image = frame else { return nil }
        let longest = max(image.size.width, image.size.height)
        if let jpeg = latestJPEG, longest <= maxSide { return jpeg }
        let scale = min(1, maxSide / longest)
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let resized = UIGraphicsImageRenderer(size: size).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        return resized.jpegData(compressionQuality: 0.85)
    }

    private func deliver(_ image: UIImage, _ jpeg: Data?) {
        frame = image
        latestJPEG = jpeg
        count += 1
        let elapsed = Date().timeIntervalSince(windowStart)
        if elapsed >= 1 {
            status = "\(sourceName) · \(Int(image.size.width))×\(Int(image.size.height)) · \(Int(Double(count) / elapsed))fps"
            count = 0
            windowStart = Date()
        }
    }

    private func runVirtual() async {
        guard let url = Bundle.main.url(forResource: "virtual-drone-feed", withExtension: "mp4") else {
            status = "가상 영상 파일이 없어요"
            return
        }
        let asset = AVURLAsset(url: url)
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = CMTime(value: 50, timescale: 1000)
        let seconds = (try? await asset.load(.duration).seconds) ?? 8
        var frames: [UIImage] = []
        // 한 바퀴를 미리 뽑아 두고 초당 10장으로 반복
        for ms in stride(from: 0, to: Int(seconds * 1000), by: 100) {
            if Task.isCancelled { return }
            if let result = try? await generator.image(at: CMTime(value: CMTimeValue(ms), timescale: 1000)) {
                frames.append(UIImage(cgImage: result.image))
            }
        }
        guard !frames.isEmpty else { status = "가상 영상을 읽지 못했어요"; return }
        var i = 0
        while !Task.isCancelled {
            deliver(frames[i % frames.count], nil)
            i += 1
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
    }
}

/// `multipart/x-mixed-replace` MJPEG 스트림을 JPEG 시작(FFD8)·끝(FFD9) 표시로 잘라 읽습니다. JPEG 한 장 주소면 반복해서 받습니다.
final class MJPEGStream: NSObject, URLSessionDataDelegate {
    private let url: URL
    private let onFrame: (UIImage, Data) -> Void
    private let onError: (String) -> Void
    private var session: URLSession?
    private var buffer = Data()
    private var single = false
    private var stopped = false
    private var lastEmit = Date.distantPast

    init(url: URL, onFrame: @escaping (UIImage, Data) -> Void, onError: @escaping (String) -> Void) {
        self.url = url
        self.onFrame = onFrame
        self.onError = onError
    }

    func start() {
        stopped = false
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 10
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
        session?.dataTask(with: url).resume()
    }

    func stop() {
        stopped = true
        session?.invalidateAndCancel()
        session = nil
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        single = (response.mimeType ?? "").hasPrefix("image/")
        buffer.removeAll()
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        while let start = buffer.range(of: Data([0xFF, 0xD8])) {
            guard let end = buffer.range(of: Data([0xFF, 0xD9]), in: start.upperBound..<buffer.endIndex) else {
                if start.lowerBound > buffer.startIndex { buffer.removeSubrange(buffer.startIndex..<start.lowerBound) }
                break
            }
            let jpeg = buffer.subdata(in: start.lowerBound..<end.upperBound)
            buffer.removeSubrange(buffer.startIndex..<end.upperBound)
            // 화면은 초당 15장이면 충분 (나머지는 건너뜀)
            if Date().timeIntervalSince(lastEmit) >= 0.066, let image = UIImage(data: jpeg) {
                lastEmit = Date()
                onFrame(image, jpeg)
            }
        }
        if buffer.count > 8_000_000 { buffer.removeAll() }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard !stopped else { return }
        if let error, (error as NSError).code != NSURLErrorCancelled { onError(error.localizedDescription) }
        // 사진 한 장 주소이거나 스트림이 끊기면 잠시 뒤 다시 받기
        DispatchQueue.global().asyncAfter(deadline: .now() + (single ? 0.2 : 1.0)) { [weak self] in
            guard let self, !self.stopped else { return }
            self.session?.dataTask(with: self.url).resume()
        }
    }
}
