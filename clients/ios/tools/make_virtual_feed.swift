// 가상 안경(MockDeviceKit)용 카메라 영상 원본 그림을 만듭니다. (macOS에서 실행)
//   swift make_virtual_feed.swift panel.png
// 앱 안의 VirtualGlasses.makeSamplePhoto()와 같은 '발전기 계기판(E2)' 그림을 세로(9:16) 화면에 그립니다.
import AppKit

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "panel.png"
let size = NSSize(width: 720, height: 1280)
let image = NSImage(size: size)
image.lockFocus()

NSColor(white: 0.85, alpha: 1).setFill()
NSRect(origin: .zero, size: size).fill()

// 계기판 몸체 (화면 가운데)
let body = NSRect(x: 40, y: 380, width: 640, height: 520)
NSColor(red: 0.95, green: 0.55, blue: 0.10, alpha: 1).setFill()
NSBezierPath(roundedRect: body, xRadius: 32, yRadius: 32).fill()

let center = NSMutableParagraphStyle()
center.alignment = .center
("HG-2000" as NSString).draw(
    in: NSRect(x: 40, y: 820, width: 640, height: 60),
    withAttributes: [.font: NSFont.boldSystemFont(ofSize: 44), .foregroundColor: NSColor.black, .paragraphStyle: center]
)

// 액정 + E2
let screen = NSRect(x: 160, y: 580, width: 400, height: 200)
NSColor(red: 0.05, green: 0.12, blue: 0.08, alpha: 1).setFill()
NSBezierPath(roundedRect: screen, xRadius: 12, yRadius: 12).fill()
("E2" as NSString).draw(
    in: screen.insetBy(dx: 0, dy: 10),
    withAttributes: [
        .font: NSFont.monospacedSystemFont(ofSize: 140, weight: .bold),
        .foregroundColor: NSColor(red: 1, green: 0.25, blue: 0.2, alpha: 1),
        .paragraphStyle: center,
    ]
)

// 경고등 (빨강·노랑)
NSColor.red.setFill()
NSBezierPath(ovalIn: NSRect(x: 220, y: 440, width: 70, height: 70)).fill()
NSColor.yellow.setFill()
NSBezierPath(ovalIn: NSRect(x: 430, y: 440, width: 70, height: 70)).fill()

image.unlockFocus()

guard
    let tiff = image.tiffRepresentation,
    let rep = NSBitmapImageRep(data: tiff),
    let png = rep.representation(using: .png, properties: [:])
else { fatalError("PNG 변환 실패") }
try png.write(to: URL(fileURLWithPath: out))
print("저장: \(out)")
