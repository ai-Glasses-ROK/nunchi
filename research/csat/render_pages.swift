// PDF의 지정한 쪽들을 PNG로 그립니다 (macOS PDFKit).
//   swift render_pages.swift input.pdf out_dir 3.0 0 1 2   ← 배율 3.0, 0·1·2쪽(0부터)
import AppKit
import PDFKit

let args = CommandLine.arguments
guard args.count >= 5, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
    fatalError("사용법: swift render_pages.swift input.pdf out_dir scale page...")
}
let outDir = URL(fileURLWithPath: args[2])
let scale = CGFloat(Double(args[3]) ?? 3.0)

for pageArg in args[4...] {
    guard let index = Int(pageArg), let page = doc.page(at: index) else { continue }
    let box = page.bounds(for: .mediaBox)
    let size = NSSize(width: box.width * scale, height: box.height * scale)
    let image = NSImage(size: size)
    image.lockFocus()
    NSColor.white.setFill()
    NSRect(origin: .zero, size: size).fill()
    if let ctx = NSGraphicsContext.current?.cgContext {
        ctx.scaleBy(x: scale, y: scale)
        page.draw(with: .mediaBox, to: ctx)
    }
    image.unlockFocus()
    guard let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { continue }
    let name = String(format: "page_%03d.png", index)
    try png.write(to: outDir.appendingPathComponent(name))
}
