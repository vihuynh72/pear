// PearImage.swift
// Shrinks a camera photo before /v1/scan/photo: about 1600 px on the long edge, JPEG quality 0.6
// (roughly 100 to 300 KB). Upright output, since draw(in:) applies the photo's orientation. iOS only (UIKit).

import UIKit

nonisolated enum PearImage {
    static func jpegForUpload(_ image: UIImage, maxEdge: CGFloat = 1600, quality: CGFloat = 0.6) -> Data? {
        let pixelWidth = image.size.width * image.scale
        let pixelHeight = image.size.height * image.scale
        let longest = max(pixelWidth, pixelHeight)
        guard longest > 0 else { return nil }
        let ratio = min(1, maxEdge / longest)
        let target = CGSize(width: (pixelWidth * ratio).rounded(), height: (pixelHeight * ratio).rounded())
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(size: target, format: format)
        let resized = renderer.image { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
        return resized.jpegData(compressionQuality: quality)
    }
}
