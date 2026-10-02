//  PearImageUpload.swift
//  Pear Swift kit — shrink camera photos before POST /v1/scan/photo (iOS only).
//
//  What this file does
//  - Addendum 4: upload about 1600 px on the long edge, JPEG quality 0.6, under ~300 KB.
//    A 12 MP HEIC/JPEG from the camera is 2-5 MB; shrinking it saves 0.5-2 s of upload.
//  - `jpegData(from: UIImage)` redraws with UIGraphicsImageRenderer (orientation baked in).
//  - `jpegData(fromImageData: Data)` uses ImageIO thumbnails straight from the camera's
//    file data (AVCapturePhoto.fileDataRepresentation()) without decoding the full image:
//    faster and lighter on memory. Prefer it on the camera path.
//  - If the first encode is over `maxBytes`, it steps down (quality 0.45, then 1280 px).
//
//  How to debug
//  - Print `data.count` of the result and compare with ~300 KB (the scanPhoto DEBUG log line
//    shows the round-trip time). If label_reader says `too_blurry` for sharp photos, try
//    `maxLongEdge: 2000` before changing anything server-side.
//  - Excluded from the macOS decoder build in check.sh (UIKit only); typechecked for iOS.

#if canImport(UIKit)
import Foundation
import ImageIO
import UIKit
import UniformTypeIdentifiers

nonisolated enum PearImageUpload {
    static let maxLongEdge: CGFloat = 1600
    static let jpegQuality: CGFloat = 0.6
    static let maxBytes = 300_000

    /// Step-down ladder used when an encode is over `maxBytes`.
    private static func ladder(_ edge: CGFloat, _ quality: CGFloat) -> [(CGFloat, CGFloat)] {
        [(edge, quality), (edge, min(quality, 0.45)), (min(edge, 1280), min(quality, 0.45))]
    }

    /// UIImage -> JPEG, long edge <= `maxLongEdge` pixels, orientation applied.
    static func jpegData(from image: UIImage, maxLongEdge: CGFloat = maxLongEdge,
                         quality: CGFloat = jpegQuality, maxBytes: Int = maxBytes) -> Data? {
        var best: Data?
        for (edge, q) in ladder(maxLongEdge, quality) {
            guard let data = render(image, maxLongEdge: edge, quality: q) else { continue }
            if best == nil || data.count < best!.count { best = data }
            if data.count <= maxBytes { return data }
        }
        return best
    }

    /// Encoded image bytes (JPEG/HEIC/PNG) -> JPEG, long edge <= `maxLongEdge`, EXIF orientation applied.
    static func jpegData(fromImageData input: Data, maxLongEdge: CGFloat = maxLongEdge,
                         quality: CGFloat = jpegQuality, maxBytes: Int = maxBytes) -> Data? {
        guard let source = CGImageSourceCreateWithData(input as CFData, nil) else { return nil }
        var best: Data?
        for (edge, q) in ladder(maxLongEdge, quality) {
            let options: [CFString: Any] = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceShouldCacheImmediately: true,
                kCGImageSourceThumbnailMaxPixelSize: Int(edge),
            ]
            guard let cg = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary),
                  let data = encodeJPEG(cg, quality: q) else { continue }
            if best == nil || data.count < best!.count { best = data }
            if data.count <= maxBytes { return data }
        }
        return best
    }

    private static func render(_ image: UIImage, maxLongEdge: CGFloat, quality: CGFloat) -> Data? {
        let pixelW = image.size.width * image.scale
        let pixelH = image.size.height * image.scale
        guard pixelW > 0, pixelH > 0 else { return nil }
        let factor = min(1, maxLongEdge / max(pixelW, pixelH))
        let target = CGSize(width: (pixelW * factor).rounded(), height: (pixelH * factor).rounded())
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1  // target is in pixels
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(size: target, format: format)
        return renderer.jpegData(withCompressionQuality: quality) { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
    }

    private static func encodeJPEG(_ image: CGImage, quality: CGFloat) -> Data? {
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out as CFMutableData, UTType.jpeg.identifier as CFString, 1, nil)
        else { return nil }
        CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return out as Data
    }
}
#endif
