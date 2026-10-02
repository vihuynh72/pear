//
//  PearLogo.swift
//  Pear
//
//  The Pear mark (b-1): a pear made of a smaller oval on top of a larger circle,
//  a brown stem, two leaves and a soft highlight. No face. Scales to any size.
//

import SwiftUI

struct PearLogo: View {
    /// Pass false when the logo sits next to the word "Pear" so VoiceOver doesn't read it twice.
    var isAccessibilityElement = true

    var body: some View {
        ZStack {
            PearLogoPart(part: .leftLeaf).fill(.avocado700)
            PearLogoPart(part: .rightLeaf).fill(.leaf)
            PearLogoPart(part: .stem).fill(.stem)
            PearLogoPart(part: .body).fill(.avocado500)
            PearLogoPart(part: .highlight).fill(Color.white.opacity(0.35))
        }
        .aspectRatio(PearLogoGeometry.aspectRatio, contentMode: .fit)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("Pear logo"))
        .accessibilityHidden(!isAccessibilityElement)
    }
}

/// One piece of the logo, drawn in the 720 x 720 design space of the logo artboard and
/// scaled to fit the proposed rect.
struct PearLogoPart: Shape {
    enum Part {
        case body, stem, leftLeaf, rightLeaf, highlight
    }

    var part: Part

    nonisolated func path(in rect: CGRect) -> Path {
        let transform = PearLogoGeometry.transform(fitting: rect)
        return designPath.applying(transform)
    }

    private nonisolated var designPath: Path {
        switch part {
        case .body:
            // Smaller oval on top, larger round body below, and a soft shoulder that blends them.
            let top = Path(ellipseIn: CGRect(x: 237.5, y: 143, width: 245, height: 284))
            let bottom = Path(ellipseIn: CGRect(x: 161.5, y: 268, width: 397, height: 384))
            // A short straight shoulder joins the two, like the mark on the logo board.
            var shoulder = Path()
            shoulder.addLines([
                CGPoint(x: 237.5, y: 288),
                CGPoint(x: 482.5, y: 288),
                CGPoint(x: 507, y: 332),
                CGPoint(x: 213, y: 332)
            ])
            shoulder.closeSubpath()
            return top.union(bottom).union(shoulder)
        case .stem:
            var line = Path()
            line.move(to: CGPoint(x: 370, y: 80))
            line.addLine(to: CGPoint(x: 357, y: 150))
            return line.strokedPath(StrokeStyle(lineWidth: 22, lineCap: .round))
        case .leftLeaf:
            return Self.ellipse(center: CGPoint(x: 322, y: 138), size: CGSize(width: 78, height: 36), degrees: 22)
        case .rightLeaf:
            return Self.ellipse(center: CGPoint(x: 410, y: 133), size: CGSize(width: 112, height: 46), degrees: -27)
        case .highlight:
            return Self.ellipse(center: CGPoint(x: 271, y: 450), size: CGSize(width: 42, height: 92), degrees: 20)
        }
    }

    private nonisolated static func ellipse(center: CGPoint, size: CGSize, degrees: Double) -> Path {
        let base = Path(ellipseIn: CGRect(x: -size.width / 2, y: -size.height / 2, width: size.width, height: size.height))
        let rotation = CGAffineTransform(rotationAngle: degrees * .pi / 180)
        let move = CGAffineTransform(translationX: center.x, y: center.y)
        return base.applying(rotation.concatenating(move))
    }
}

nonisolated enum PearLogoGeometry {
    /// The part of the 720 x 720 artboard the mark occupies (stem tip to the bottom of the body).
    static let designBounds = CGRect(x: 160, y: 66, width: 400, height: 588)
    static let aspectRatio = designBounds.width / designBounds.height

    /// Scales the design bounds uniformly into `rect` and centers them.
    static func transform(fitting rect: CGRect) -> CGAffineTransform {
        let scale = min(rect.width / designBounds.width, rect.height / designBounds.height)
        let drawnWidth = designBounds.width * scale
        let drawnHeight = designBounds.height * scale
        let originX = rect.minX + (rect.width - drawnWidth) / 2
        let originY = rect.minY + (rect.height - drawnHeight) / 2
        return CGAffineTransform(translationX: -designBounds.minX, y: -designBounds.minY)
            .concatenating(CGAffineTransform(scaleX: scale, y: scale))
            .concatenating(CGAffineTransform(translationX: originX, y: originY))
    }
}

#Preview("Logo sizes") {
    HStack(alignment: .bottom, spacing: 24) {
        PearLogo().frame(height: 40)
        PearLogo().frame(height: 96)
        PearLogo().frame(height: 225)
    }
    .padding(24)
    .pearBackground()
}

#Preview("Lockup") {
    HStack(spacing: 20) {
        PearLogo(isAccessibilityElement: false).frame(height: 120)
        Text("Pear")
            .font(PearFont.display)
            .foregroundStyle(.cocoa)
    }
    .padding(24)
    .background(.white)
}
