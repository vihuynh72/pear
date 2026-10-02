//
//  VerdictStyle.swift
//  Pear
//
//  Shared look for verdicts (design pages c-16 to c-20): the small pear with a state badge,
//  chip colors per state (plan 6.2 chip backgrounds), the product tile, and the result copy in
//  English, Vietnamese and Spanish so the sheet matches the shopper's language
//  (PearEnvironment.displayLanguage). Server text (headline, labels, reasons) is used as sent.
//

import SwiftUI

// MARK: - State colors and symbols

extension VerdictState {
    /// Pear body color for the little chip pear.
    var pearTint: Color {
        switch self {
        case .ripe: .avocado500
        case .easy: .honey
        case .skip: .peach
        case .allergy: .blush
        case .diet: Color(hex: 0xD9C8B4)
        case .unsure: Color(hex: 0xCFC8BF)
        }
    }

    /// Chip background (plan 6.2).
    var chipFill: Color {
        switch self {
        case .ripe: .avocado100
        case .easy: Color(hex: 0xFCEFD2)
        case .skip: Color(hex: 0xFBE1D6)
        case .allergy: Color(hex: 0xFBE7EB)
        case .diet: Color(hex: 0xEFE4DA)
        case .unsure: Color(hex: 0xECE8E3)
        }
    }

    /// Chip word and bullet color; berry only for allergy (plan 6.1.3).
    var ink: Color {
        switch self {
        case .ripe: .avocado700
        case .easy: Color(hex: 0x8A6A1F)
        case .skip: Color(hex: 0xA4502C)
        case .allergy: .berry
        case .diet: .latte
        case .unsure: .latte
        }
    }

    /// Dot in "What we found".
    var dot: Color {
        switch self {
        case .ripe: .avocado700
        case .easy, .skip: Color(hex: 0xC9A04A)
        case .allergy: .berry
        case .diet, .unsure: Color(hex: 0xC9A04A)
        }
    }

    var badgeSymbol: String {
        switch self {
        case .ripe: "checkmark.circle.fill"
        case .easy: "clock.fill"
        case .skip: "minus.circle.fill"
        case .allergy: "exclamationmark.shield.fill"
        case .diet: "leaf.circle.fill"
        case .unsure: "questionmark.circle.fill"
        }
    }
}

/// The small pear from the logo, tinted per state, with a badge at the bottom right.
struct VerdictPear: View {
    let state: VerdictState
    var size: CGFloat = 28

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            ZStack {
                PearLogoPart(part: .leftLeaf).fill(.avocado700)
                PearLogoPart(part: .rightLeaf).fill(.leaf)
                PearLogoPart(part: .stem).fill(.stem)
                PearLogoPart(part: .body).fill(state.pearTint)
            }
            .aspectRatio(PearLogoGeometry.aspectRatio, contentMode: .fit)
            .frame(height: size)

            Image(systemName: state.badgeSymbol)
                .font(.system(size: size * 0.5, weight: .bold))
                .foregroundStyle(state.ink)
                .background(Circle().fill(.white).padding(1))
                .offset(x: size * 0.18, y: size * 0.06)
        }
        .accessibilityHidden(true)
    }
}

/// "🍐 Mia  Allergy" chip.
struct VerdictChip: View {
    let name: String
    let verdict: Verdict

    var body: some View {
        HStack(spacing: PearMetrics.Spacing.xs) {
            VerdictPear(state: verdict.state)
            Text(verbatim: name)
                .font(PearFont.headline)
                .foregroundStyle(.cocoa)
            Text(verbatim: verdict.label)
                .font(PearFont.body)
                .foregroundStyle(verdict.state.ink)
                .lineLimit(2)
                .minimumScaleFactor(0.85)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, PearMetrics.Spacing.xxs)
        .padding(.leading, PearMetrics.Spacing.sm)
        .padding(.trailing, PearMetrics.Spacing.md)
        .frame(minHeight: 48)
        .background(Capsule().fill(verdict.state.chipFill))
        .accessibilityElement(children: .combine)
    }
}

/// Square product tile: the product photo when there is one, else a category icon.
struct ProductTile: View {
    let product: Product
    var size: CGFloat = 64

    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
            .fill(tint)
            .frame(width: size, height: size)
            .overlay {
                if let url = product.imageUrl.flatMap(URL.init(string:)) {
                    AsyncImage(url: url) { image in
                        image.resizable().scaledToFill()
                    } placeholder: {
                        icon
                    }
                    .frame(width: size, height: size)
                    .clipShape(RoundedRectangle(cornerRadius: size * 0.28, style: .continuous))
                } else {
                    icon
                }
            }
            .accessibilityHidden(true)
    }

    private var icon: some View {
        Image(systemName: Self.symbol(for: product.category))
            .font(.system(size: size * 0.42, weight: .medium))
            .foregroundStyle(.cocoa)
    }

    private var tint: Color {
        if product.lessSure { return .mist }
        switch product.category {
        case "cosmetic": return .blush
        case "supplement": return .avocado100
        case "other": return .mist
        default: return .honey
        }
    }

    static func symbol(for category: String) -> String {
        switch category {
        case "cosmetic": "drop"
        case "supplement": "pills"
        case "other": "shippingbox"
        default: "fork.knife"
        }
    }
}

// MARK: - Result copy (en / vi / es)

enum ResultCopy {
    static func t(_ en: String, _ vi: String, _ es: String, _ language: String) -> String {
        switch language.prefix(2) {
        case "vi": vi
        case "es": es
        default: en
        }
    }

    static func and(_ names: [String], _ language: String) -> String {
        let word = t("and", "và", "y", language)
        switch names.count {
        case 0: return ""
        case 1: return names[0]
        default: return names.dropLast().joined(separator: ", ") + " \(word) " + (names.last ?? "")
        }
    }

    static func whatWeFound(_ l: String) -> String { t("What we found", "Pear thấy gì", "Lo que encontramos", l) }
    static func nothingToWorry(_ names: String, _ l: String) -> String {
        t("\(names): nothing to worry about.", "\(names): không có gì phải lo.", "\(names): nada de qué preocuparse.", l)
    }
    static func noAllergens(_ l: String) -> String {
        t("No allergens from anyone's list.", "Không có chất gây dị ứng nào của cả nhà.", "Ningún alérgeno de la lista de nadie.", l)
    }
    static func weRead(_ text: String, _ l: String) -> String {
        t("We read: \(text).", "Pear đọc được: \(text).", "Leímos: \(text).", l)
    }
    static func missingIngredients(_ l: String) -> String {
        t("Missing: the ingredients list.", "Thiếu: danh sách thành phần.", "Falta: la lista de ingredientes.", l)
    }
    static func listen(_ l: String) -> String { t("Listen", "Nghe", "Escuchar", l) }
    static func details(_ l: String) -> String { t("Details", "Chi tiết", "Detalles", l) }
    static func seeBetterPicks(_ l: String) -> String { t("See better picks", "Xem lựa chọn tốt hơn", "Ver mejores opciones", l) }
    static func scanAnother(_ l: String) -> String { t("Scan another", "Quét món khác", "Escanear otro", l) }
    static func snapIngredients(_ l: String) -> String { t("Snap the ingredients", "Chụp bảng thành phần", "Foto de los ingredientes", l) }
    static func typeItIn(_ l: String) -> String { t("Type it in", "Gõ tên sản phẩm", "Escribirlo", l) }
    static func frontOnly(_ l: String) -> String { t("Front of pack only", "Chỉ có mặt trước bao bì", "Solo el frente del paquete", l) }
    static func forEachPerson(_ l: String) -> String { t("For each person", "Từng người", "Para cada persona", l) }
    static func ingredients(_ l: String) -> String { t("Ingredients", "Thành phần", "Ingredientes", l) }
    static func perServing(_ basis: String, _ l: String) -> String {
        basis == "serving" ? t("Per serving", "Mỗi khẩu phần", "Por porción", l) : t("Per 100 g", "Mỗi 100 g", "Por 100 g", l)
    }
    static func sugar(_ l: String) -> String { t("Sugar", "Đường", "Azúcar", l) }
    static func sodium(_ l: String) -> String { t("Sodium", "Natri", "Sodio", l) }
    static func saturatedFat(_ l: String) -> String { t("Saturated fat", "Chất béo bão hòa", "Grasa saturada", l) }
    static func reportMistake(_ l: String) -> String { t("Report a mistake", "Báo lỗi", "Reportar un error", l) }
    static func betterPicks(_ l: String) -> String { t("Better picks", "Lựa chọn tốt hơn", "Mejores opciones", l) }
    static func insteadOf(_ name: String, _ n: Int, _ l: String) -> String {
        t("Instead of \(name), ripe for all \(n)", "Thay cho \(name), hợp với cả \(n) người", "En lugar de \(name), bien para los \(n)", l)
    }
    static func ripeForAll(_ n: Int, _ l: String) -> String {
        t("Ripe for all \(n)", "Hợp với cả \(n) người", "Bien para los \(n)", l)
    }
    static func pickedForFamily(_ l: String) -> String {
        t("Picked for your family. No paid placements.", "Chọn cho gia đình bạn. Không có quảng cáo trả tiền.", "Elegido para tu familia. Sin anuncios pagados.", l)
    }
    static func addToList(_ l: String) -> String { t("Add to my list", "Thêm vào danh sách", "Añadir a mi lista", l) }
    static func addedToList(_ l: String) -> String { t("Added to your list", "Đã thêm vào danh sách", "Añadido a tu lista", l) }
    static func findingPicks(_ l: String) -> String { t("Finding picks that fit everyone…", "Đang tìm món hợp với cả nhà…", "Buscando opciones para todos…", l) }
    static func noPicks(_ l: String) -> String { t("No better picks for this one yet.", "Chưa có lựa chọn nào tốt hơn cho món này.", "Aún no hay mejores opciones para este.", l) }
    static func picksFoodOnly(_ l: String) -> String { t("Better picks are for food for now.", "Hiện chỉ có gợi ý cho thực phẩm.", "Por ahora, solo hay opciones para comida.", l) }
    static func foundWithGoogle(_ l: String) -> String { t("Found with Google Search.", "Tìm bằng Google Search.", "Encontrado con Google Search.", l) }
    static func checkingPrices(_ l: String) -> String { t("Checking prices…", "Đang xem giá…", "Buscando precios…", l) }
    static func source(_ product: Product, _ l: String) -> String {
        product.source == "photo"
            ? t("Read from your photo.", "Đọc từ ảnh bạn chụp.", "Leído de tu foto.", l)
            : t("From Open Food Facts. Check the label.", "Từ Open Food Facts. Hãy xem lại nhãn.", "De Open Food Facts. Revisa la etiqueta.", l)
    }
    static func price(_ price: Price, _ l: String) -> String {
        let amount = "$" + String(format: "%.2f", price.amount)
        return t("About \(amount) at \(price.retailer) (\(price.size))",
                 "Khoảng \(amount) tại \(price.retailer) (\(price.size))",
                 "Unos \(amount) en \(price.retailer) (\(price.size))", l)
    }
}

// MARK: - Headline split ("Not for Mia" / "Contains sesame. Fine for Linh and Leo.")

enum HeadlineParts {
    /// The server's headline is one or two sentences ("Not for Mia: contains sesame. Fine for ...").
    /// The design shows the first clause big and the rest under it.
    static func split(_ text: String) -> (title: String, body: String) {
        if let colon = text.firstIndex(of: ":") {
            let title = String(text[..<colon]).trimmingCharacters(in: .whitespaces)
            var body = String(text[text.index(after: colon)...]).trimmingCharacters(in: .whitespaces)
            if let first = body.first { body = first.uppercased() + body.dropFirst() }
            return (title, body)
        }
        if let dot = text.firstIndex(of: "."), text.index(after: dot) < text.endIndex {
            let title = String(text[..<dot]).trimmingCharacters(in: .whitespaces)
            let body = String(text[text.index(after: dot)...]).trimmingCharacters(in: .whitespaces)
            return (title, body)
        }
        return (text.trimmingCharacters(in: CharacterSet(charactersIn: ". ")), "")
    }
}
