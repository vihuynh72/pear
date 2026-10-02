//  PearModels.swift
//  Pear Swift kit — Codable mirrors of api/models/contracts.py (plan 7.6 + addendum 6).
//
//  What this file does
//  - One Sendable struct per contract model, with EXPLICIT snake_case CodingKeys.
//    Never set `keyDecodingStrategy = .convertFromSnakeCase`: it would also rewrite
//    dictionary keys such as timings_ms["label_reader"] and the product keys in prices.
//  - Decoding mirrors Pydantic: fields that are required in contracts.py use `decode`,
//    fields with a default use `decodeIfPresent ?? default`, so a missing/null optional
//    never throws.
//  - Every Literal in contracts.py is a `PearOpenEnum`: known cases plus `.unknown(String)`,
//    so an additive backend value ("state": "NEW") decodes instead of crashing.
//  - Household/Member encode exactly the contract JSON, including `"species": null` and
//    `"avoid_dyes": null` when unset (contracts/household.example.json).
//
//  How to debug
//  - `contracts/swift/check.sh` decodes every contracts/fixtures/*.json with these types,
//    re-encodes it, and diffs the JSON (dropped keys = a field missing here).
//  - A `PearAPIError.decoding` carries the body preview and the request id; grep the
//    backend logs for that id (`request` log line) to see what the server sent.
//  - All types are `nonisolated` because the Pear app target sets
//    SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor; without it Codable conformances would be
//    MainActor-isolated and could not be used by the client off the main actor.

import Foundation

// MARK: - Open enums

/// A String-backed enum that never fails to decode: unknown raw values become `.unknown(raw)`.
nonisolated protocol PearOpenEnum: RawRepresentable, Codable, Hashable, Sendable, CustomStringConvertible
where RawValue == String {
    /// Every value the current contract defines, in contract order (handy for pickers).
    static var knownCases: [Self] { get }
    /// The catch-all case for values added on the backend after this kit was written.
    static func unknown(_ rawValue: String) -> Self
    var rawValue: String { get }
}

nonisolated extension PearOpenEnum {
    init(rawValue: String) {
        self = Self.knownCases.first { $0.rawValue == rawValue } ?? Self.unknown(rawValue)
    }

    /// False for `.unknown(...)`: the backend sent a value this kit does not know yet.
    var isKnown: Bool { Self.knownCases.contains(self) }
    var description: String { rawValue }
}

/// Verdict state for one member (plan 8.3).
nonisolated enum PearState: PearOpenEnum {
    case ripe, easy, skip, allergy, diet, unsure
    case unknown(String)

    static let knownCases: [PearState] = [.ripe, .easy, .skip, .allergy, .diet, .unsure]
    var rawValue: String {
        switch self {
        case .ripe: "RIPE"
        case .easy: "EASY"
        case .skip: "SKIP"
        case .allergy: "ALLERGY"
        case .diet: "DIET"
        case .unsure: "UNSURE"
        case .unknown(let raw): raw
        }
    }

    /// Higher = more important to show first (ALLERGY > SKIP > DIET > UNSURE > EASY > RIPE).
    var priority: Int {
        switch self {
        case .allergy: 5
        case .skip: 4
        case .diet: 3
        case .unsure, .unknown: 2
        case .easy: 1
        case .ripe: 0
        }
    }
}

nonisolated enum PearSeverity: PearOpenEnum {
    case hard, soft, note
    case unknown(String)

    static let knownCases: [PearSeverity] = [.hard, .soft, .note]
    var rawValue: String {
        switch self {
        case .hard: "hard"
        case .soft: "soft"
        case .note: "note"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearSource: PearOpenEnum {
    case openFoodFacts, openBeautyFacts, usdaFdc, photo
    case unknown(String)

    static let knownCases: [PearSource] = [.openFoodFacts, .openBeautyFacts, .usdaFdc, .photo]
    var rawValue: String {
        switch self {
        case .openFoodFacts: "open_food_facts"
        case .openBeautyFacts: "open_beauty_facts"
        case .usdaFdc: "usda_fdc"
        case .photo: "photo"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearPhotoKind: PearOpenEnum {
    case ingredients, frontOnly, other
    case unknown(String)

    static let knownCases: [PearPhotoKind] = [.ingredients, .frontOnly, .other]
    var rawValue: String {
        switch self {
        case .ingredients: "ingredients"
        case .frontOnly: "front_only"
        case .other: "other"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearDataConfidence: PearOpenEnum {
    case high, medium, low
    case unknown(String)

    static let knownCases: [PearDataConfidence] = [.high, .medium, .low]
    var rawValue: String {
        switch self {
        case .high: "high"
        case .medium: "medium"
        case .low: "low"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearCategory: PearOpenEnum {
    case food, cosmetic, supplement, other
    case unknown(String)

    static let knownCases: [PearCategory] = [.food, .cosmetic, .supplement, .other]
    var rawValue: String {
        switch self {
        case .food: "food"
        case .cosmetic: "cosmetic"
        case .supplement: "supplement"
        case .other: "other"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearProductType: PearOpenEnum {
    case leaveOn, rinseOff
    case unknown(String)

    static let knownCases: [PearProductType] = [.leaveOn, .rinseOff]
    var rawValue: String {
        switch self {
        case .leaveOn: "leave_on"
        case .rinseOff: "rinse_off"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearMatchedBy: PearOpenEnum {
    case barcode, name, photo, vector
    case unknown(String)

    static let knownCases: [PearMatchedBy] = [.barcode, .name, .photo, .vector]
    var rawValue: String {
        switch self {
        case .barcode: "barcode"
        case .name: "name"
        case .photo: "photo"
        case .vector: "vector"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearNutritionBasis: PearOpenEnum {
    case serving, per100g
    case unknown(String)

    static let knownCases: [PearNutritionBasis] = [.serving, .per100g]
    var rawValue: String {
        switch self {
        case .serving: "serving"
        case .per100g: "100g"
        case .unknown(let raw): raw
        }
    }
}

/// App toggle: "Strict" = .strict, "Ingredients only" = .sensitive (kb/README.md).
nonisolated enum PearAllergyLevel: PearOpenEnum {
    case strict, sensitive
    case unknown(String)

    static let knownCases: [PearAllergyLevel] = [.strict, .sensitive]
    var rawValue: String {
        switch self {
        case .strict: "strict"
        case .sensitive: "sensitive"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearAgeGroup: PearOpenEnum {
    case baby, kid, teen, adult, senior
    case unknown(String)

    static let knownCases: [PearAgeGroup] = [.baby, .kid, .teen, .adult, .senior]
    var rawValue: String {
        switch self {
        case .baby: "baby"
        case .kid: "kid"
        case .teen: "teen"
        case .adult: "adult"
        case .senior: "senior"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearMemberType: PearOpenEnum {
    case person, pet
    case unknown(String)

    static let knownCases: [PearMemberType] = [.person, .pet]
    var rawValue: String {
        switch self {
        case .person: "person"
        case .pet: "pet"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearIntent: PearOpenEnum {
    case check, alternatives, unclear
    case unknown(String)

    static let knownCases: [PearIntent] = [.check, .alternatives, .unclear]
    var rawValue: String {
        switch self {
        case .check: "check"
        case .alternatives: "alternatives"
        case .unclear: "unclear"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearAskNext: PearOpenEnum {
    case scanNeeded, result
    case unknown(String)

    static let knownCases: [PearAskNext] = [.scanNeeded, .result]
    var rawValue: String {
        switch self {
        case .scanNeeded: "scan_needed"
        case .result: "result"
        case .unknown(let raw): raw
        }
    }
}

/// Error codes. The first five are contract codes; the last three are additive.
nonisolated enum PearErrorCode: PearOpenEnum {
    case notFound, unreadable, tooBlurry, rateLimited, internalError
    case badRequest, unauthorized, unavailable
    case unknown(String)

    static let knownCases: [PearErrorCode] = [
        .notFound, .unreadable, .tooBlurry, .rateLimited, .internalError,
        .badRequest, .unauthorized, .unavailable,
    ]
    var rawValue: String {
        switch self {
        case .notFound: "not_found"
        case .unreadable: "unreadable"
        case .tooBlurry: "too_blurry"
        case .rateLimited: "rate_limited"
        case .internalError: "internal"
        case .badRequest: "bad_request"
        case .unauthorized: "unauthorized"
        case .unavailable: "unavailable"
        case .unknown(let raw): raw
        }
    }
}

/// What the UI should offer after an error (`error.next`).
nonisolated enum PearNextAction: PearOpenEnum {
    case takePhoto, retake, retry, signIn
    case unknown(String)

    static let knownCases: [PearNextAction] = [.takePhoto, .retake, .retry, .signIn]
    var rawValue: String {
        switch self {
        case .takePhoto: "take_photo"
        case .retake: "retake"
        case .retry: "retry"
        case .signIn: "sign_in"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearProfileGroup: PearOpenEnum {
    case allergies, conditions, skin, diets, lessOf, medications
    case unknown(String)

    static let knownCases: [PearProfileGroup] = [.allergies, .conditions, .skin, .diets, .lessOf, .medications]
    var rawValue: String {
        switch self {
        case .allergies: "allergies"
        case .conditions: "conditions"
        case .skin: "skin"
        case .diets: "diets"
        case .lessOf: "less_of"
        case .medications: "medications"
        case .unknown(let raw): raw
        }
    }
}

nonisolated enum PearPriceSource: PearOpenEnum {
    case googleSearch
    case unknown(String)

    static let knownCases: [PearPriceSource] = [.googleSearch]
    var rawValue: String {
        switch self {
        case .googleSearch: "google_search"
        case .unknown(let raw): raw
        }
    }
}

// MARK: - Free-form JSON (debug / checks payloads)

/// Any JSON value. Used for `debug` (only with the debug flag) and health `checks`.
nonisolated enum PearJSONValue: Codable, Hashable, Sendable, CustomStringConvertible {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([PearJSONValue])
    case object([String: PearJSONValue])

    init(from decoder: any Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([PearJSONValue].self) { self = .array(v) }
        else { self = .object(try c.decode([String: PearJSONValue].self)) }
    }

    func encode(to encoder: any Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        }
    }

    subscript(key: String) -> PearJSONValue? {
        if case .object(let o) = self { return o[key] }
        return nil
    }

    var stringValue: String? { if case .string(let s) = self { s } else { nil } }
    var numberValue: Double? { if case .number(let n) = self { n } else { nil } }

    /// Pretty-printed JSON, for a debug sheet.
    var description: String {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        guard let data = try? enc.encode(self) else { return "<json>" }
        return String(decoding: data, as: UTF8.self)
    }
}

// MARK: - Decoding helper

nonisolated extension KeyedDecodingContainer {
    /// `decodeIfPresent ?? fallback`: missing key or JSON null -> fallback (Pydantic default).
    func pear<T: Decodable>(_ key: Key, default fallback: @autoclosure () -> T) throws -> T {
        try decodeIfPresent(T.self, forKey: key) ?? fallback()
    }
}

/// Responses that carry the additive `request_id`. The client fills it from the
/// X-Request-ID header when the body has none.
nonisolated protocol PearRequestIdentified: Sendable {
    var requestId: String? { get set }
}

// MARK: - Household (input on every request)

nonisolated struct Allergy: Codable, Hashable, Sendable {
    var id: String
    var level: PearAllergyLevel = .strict

    enum CodingKeys: String, CodingKey { case id, level }
}

nonisolated extension Allergy {
    /// Accepts `{"id": "peanut", "level": "strict"}` or the shorthand `"peanut"` (like the backend).
    init(from decoder: any Decoder) throws {
        if let single = try? decoder.singleValueContainer(), let id = try? single.decode(String.self) {
            self.init(id: id)
            return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(id: try c.decode(String.self, forKey: .id),
                  level: try c.pear(.level, default: .strict))
    }
}

nonisolated struct Member: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var type: PearMemberType = .person
    /// "dog" | "cat" when type == .pet
    var species: String? = nil
    var name: String
    var ageGroup: PearAgeGroup = .adult
    /// Base language tags, first = primary ("en", "vi", "es").
    var languages: [String] = ["en"]
    var allergies: [Allergy] = []
    var conditions: [String] = []
    var pregnant: Bool = false
    var skin: [String] = []
    var diets: [String] = []
    var lessOf: [String] = []
    /// nil = use the age-group default (on for baby and kid). Encoded as JSON null.
    var avoidDyes: Bool? = nil
    var medications: [String] = []

    enum CodingKeys: String, CodingKey {
        case id, type, species, name
        case ageGroup = "age_group"
        case languages, allergies, conditions, pregnant, skin, diets
        case lessOf = "less_of"
        case avoidDyes = "avoid_dyes"
        case medications
    }
}

nonisolated extension Member {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            id: try c.decode(String.self, forKey: .id),
            type: try c.pear(.type, default: .person),
            species: try c.decodeIfPresent(String.self, forKey: .species),
            name: try c.decode(String.self, forKey: .name),
            ageGroup: try c.pear(.ageGroup, default: .adult),
            languages: try c.pear(.languages, default: ["en"]),
            allergies: try c.pear(.allergies, default: []),
            conditions: try c.pear(.conditions, default: []),
            pregnant: try c.pear(.pregnant, default: false),
            skin: try c.pear(.skin, default: []),
            diets: try c.pear(.diets, default: []),
            lessOf: try c.pear(.lessOf, default: []),
            avoidDyes: try c.decodeIfPresent(Bool.self, forKey: .avoidDyes),
            medications: try c.pear(.medications, default: [])
        )
    }

    /// Writes every key, with `null` for unset species / avoid_dyes (contract JSON).
    func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(type, forKey: .type)
        if let species { try c.encode(species, forKey: .species) } else { try c.encodeNil(forKey: .species) }
        try c.encode(name, forKey: .name)
        try c.encode(ageGroup, forKey: .ageGroup)
        try c.encode(languages, forKey: .languages)
        try c.encode(allergies, forKey: .allergies)
        try c.encode(conditions, forKey: .conditions)
        try c.encode(pregnant, forKey: .pregnant)
        try c.encode(skin, forKey: .skin)
        try c.encode(diets, forKey: .diets)
        try c.encode(lessOf, forKey: .lessOf)
        if let avoidDyes { try c.encode(avoidDyes, forKey: .avoidDyes) } else { try c.encodeNil(forKey: .avoidDyes) }
        try c.encode(medications, forKey: .medications)
    }

    /// Primary language for speech / text ("en" when empty).
    var language: String { languages.first ?? "en" }

    /// Mirrors Member.dyes_on in contracts.py.
    var dyesOn: Bool {
        if let avoidDyes { return avoidDyes || lessOf.contains("artificial_dyes") }
        return ageGroup == .baby || ageGroup == .kid || lessOf.contains("artificial_dyes")
    }
}

nonisolated struct Household: Codable, Hashable, Sendable {
    var householdId: String = "hh_guest"
    var ownerLanguage: String = "en"
    var memberLimit: Int = 5
    var members: [Member] = []

    enum CodingKeys: String, CodingKey {
        case householdId = "household_id"
        case ownerLanguage = "owner_language"
        case memberLimit = "member_limit"
        case members
    }
}

nonisolated extension Household {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            householdId: try c.pear(.householdId, default: "hh_guest"),
            ownerLanguage: try c.pear(.ownerLanguage, default: "en"),
            memberLimit: try c.pear(.memberLimit, default: 5),
            members: try c.pear(.members, default: [])
        )
    }

    func member(id: String) -> Member? { members.first { $0.id == id } }

    /// The household as a compact JSON string (multipart `household` field).
    func jsonString() throws -> String {
        String(decoding: try JSONEncoder().encode(self), as: UTF8.self)
    }
}

// MARK: - Product + ScanResult (output)

/// Per-serving values when the source has them; `basis` says which. Additive.
nonisolated struct Nutrition: Codable, Hashable, Sendable {
    var basis: PearNutritionBasis = .serving
    var sodiumMg: Double? = nil
    var sodiumDv: Double? = nil
    var addedSugarsG: Double? = nil
    var addedSugarsDv: Double? = nil
    var sugarsG: Double? = nil
    var satFatG: Double? = nil
    var satFatDv: Double? = nil
    var caffeineMg: Double? = nil

    enum CodingKeys: String, CodingKey {
        case basis
        case sodiumMg = "sodium_mg"
        case sodiumDv = "sodium_dv"
        case addedSugarsG = "added_sugars_g"
        case addedSugarsDv = "added_sugars_dv"
        case sugarsG = "sugars_g"
        case satFatG = "sat_fat_g"
        case satFatDv = "sat_fat_dv"
        case caffeineMg = "caffeine_mg"
    }
}

nonisolated extension Nutrition {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            basis: try c.pear(.basis, default: .serving),
            sodiumMg: try c.decodeIfPresent(Double.self, forKey: .sodiumMg),
            sodiumDv: try c.decodeIfPresent(Double.self, forKey: .sodiumDv),
            addedSugarsG: try c.decodeIfPresent(Double.self, forKey: .addedSugarsG),
            addedSugarsDv: try c.decodeIfPresent(Double.self, forKey: .addedSugarsDv),
            sugarsG: try c.decodeIfPresent(Double.self, forKey: .sugarsG),
            satFatG: try c.decodeIfPresent(Double.self, forKey: .satFatG),
            satFatDv: try c.decodeIfPresent(Double.self, forKey: .satFatDv),
            caffeineMg: try c.decodeIfPresent(Double.self, forKey: .caffeineMg)
        )
    }
}

nonisolated struct Product: Codable, Hashable, Sendable, Identifiable {
    /// "off:0123456789012", "photo:<12 hex>", ... Send it back as product_key / last_product_key.
    var key: String
    var name: String? = nil
    var brand: String? = nil
    var category: PearCategory = .food
    var productType: PearProductType? = nil
    var imageUrl: String? = nil
    var source: PearSource
    var photoKind: PearPhotoKind? = nil
    var dataConfidence: PearDataConfidence = .high
    var lessSure: Bool = false
    var askForIngredients: Bool = false
    var ingredients: [String] = []
    /// kb allergen ids
    var contains: [String] = []
    /// kb allergen ids
    var mayContain: [String] = []
    var certifications: [String] = []
    var labelLanguage: String? = nil
    // additive
    var code: String? = nil
    var quantity: String? = nil
    var nutrition: Nutrition? = nil
    var matchedBy: PearMatchedBy? = nil

    var id: String { key }
    var imageURL: URL? { imageUrl.flatMap(URL.init(string:)) }

    enum CodingKeys: String, CodingKey {
        case key, name, brand, category
        case productType = "product_type"
        case imageUrl = "image_url"
        case source
        case photoKind = "photo_kind"
        case dataConfidence = "data_confidence"
        case lessSure = "less_sure"
        case askForIngredients = "ask_for_ingredients"
        case ingredients, contains
        case mayContain = "may_contain"
        case certifications
        case labelLanguage = "label_language"
        case code, quantity, nutrition
        case matchedBy = "matched_by"
    }
}

nonisolated extension Product {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            key: try c.decode(String.self, forKey: .key),
            name: try c.decodeIfPresent(String.self, forKey: .name),
            brand: try c.decodeIfPresent(String.self, forKey: .brand),
            category: try c.pear(.category, default: .food),
            productType: try c.decodeIfPresent(PearProductType.self, forKey: .productType),
            imageUrl: try c.decodeIfPresent(String.self, forKey: .imageUrl),
            source: try c.decode(PearSource.self, forKey: .source),
            photoKind: try c.decodeIfPresent(PearPhotoKind.self, forKey: .photoKind),
            dataConfidence: try c.pear(.dataConfidence, default: .high),
            lessSure: try c.pear(.lessSure, default: false),
            askForIngredients: try c.pear(.askForIngredients, default: false),
            ingredients: try c.pear(.ingredients, default: []),
            contains: try c.pear(.contains, default: []),
            mayContain: try c.pear(.mayContain, default: []),
            certifications: try c.pear(.certifications, default: []),
            labelLanguage: try c.decodeIfPresent(String.self, forKey: .labelLanguage),
            code: try c.decodeIfPresent(String.self, forKey: .code),
            quantity: try c.decodeIfPresent(String.self, forKey: .quantity),
            nutrition: try c.decodeIfPresent(Nutrition.self, forKey: .nutrition),
            matchedBy: try c.decodeIfPresent(PearMatchedBy.self, forKey: .matchedBy)
        )
    }
}

nonisolated struct Reason: Codable, Hashable, Sendable {
    /// e.g. allergen.sesame, allergen.peanut.may_contain, diet.vegan.maybe (kb/README.md)
    var code: String
    /// In the member's language. Name the ingredient; render as-is.
    var text: String
    var language: String
    var severity: PearSeverity
    /// Exact words as printed on the label, for highlighting.
    var matched: [String] = []
    /// kb source id (kb/sources.json)
    var source: String? = nil

    enum CodingKeys: String, CodingKey { case code, text, language, severity, matched, source }
}

nonisolated extension Reason {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            code: try c.decode(String.self, forKey: .code),
            text: try c.decode(String.self, forKey: .text),
            language: try c.decode(String.self, forKey: .language),
            severity: try c.decode(PearSeverity.self, forKey: .severity),
            matched: try c.pear(.matched, default: []),
            source: try c.decodeIfPresent(String.self, forKey: .source)
        )
    }
}

nonisolated struct Verdict: Codable, Hashable, Sendable, Identifiable {
    var memberId: String
    var state: PearState
    /// English chip word: Ripe, Easy does it, Skip it, Allergy, May contain, ...
    var label: String
    var reasons: [Reason] = []
    /// Hidden: always null unless the debug flag is on.
    var score: Double? = nil

    var id: String { memberId }

    enum CodingKeys: String, CodingKey {
        case memberId = "member_id"
        case state, label, reasons, score
    }
}

nonisolated extension Verdict {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            memberId: try c.decode(String.self, forKey: .memberId),
            state: try c.decode(PearState.self, forKey: .state),
            label: try c.decode(String.self, forKey: .label),
            reasons: try c.pear(.reasons, default: []),
            score: try c.decodeIfPresent(Double.self, forKey: .score)
        )
    }
}

nonisolated struct Headline: Codable, Hashable, Sendable {
    var text: String
    var language: String
}

nonisolated struct ScanResult: Codable, Hashable, Sendable, PearRequestIdentified {
    var product: Product
    var verdicts: [Verdict]
    var headline: Headline
    /// Stage timings, e.g. ["lookup": 420, "rules": 6, "total": 610]. Keys are NOT camel-cased.
    var timingsMs: [String: Int] = [:]
    // additive
    var requestId: String? = nil
    /// Only with the client's debug flag (X-Pear-Debug: 1).
    var debug: PearJSONValue? = nil

    enum CodingKeys: String, CodingKey {
        case product, verdicts, headline
        case timingsMs = "timings_ms"
        case requestId = "request_id"
        case debug
    }

    func verdict(for memberId: String) -> Verdict? { verdicts.first { $0.memberId == memberId } }
}

nonisolated extension ScanResult {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            product: try c.decode(Product.self, forKey: .product),
            verdicts: try c.decode([Verdict].self, forKey: .verdicts),
            headline: try c.decode(Headline.self, forKey: .headline),
            timingsMs: try c.pear(.timingsMs, default: [:]),
            requestId: try c.decodeIfPresent(String.self, forKey: .requestId),
            debug: try c.decodeIfPresent(PearJSONValue.self, forKey: .debug)
        )
    }
}

// MARK: - Requests (Encodable; nil optionals are omitted)

nonisolated struct BarcodeRequest: Encodable, Sendable {
    var code: String
    var household: Household
    /// "Who's this for?" toggle; nil = everyone.
    var memberIds: [String]? = nil

    enum CodingKeys: String, CodingKey {
        case code, household
        case memberIds = "member_ids"
    }
}

/// POST /v1/ask JSON body (addendum 6). Multipart audio is the fallback.
nonisolated struct AskRequest: Encodable, Sendable {
    var text: String
    /// Dictation locale hint, e.g. "vi" (the backend accepts "vi-VN" too).
    var language: String? = nil
    var lastProductKey: String? = nil
    var household: Household
    /// Which member is asking (defaults to the first on the backend).
    var askerId: String? = nil

    enum CodingKeys: String, CodingKey {
        case text, language
        case lastProductKey = "last_product_key"
        case household
        case askerId = "asker_id"
    }
}

/// Body for /v1/explain and /v1/picks.
nonisolated struct ProductKeyRequest: Encodable, Sendable {
    var productKey: String
    var household: Household
    var memberIds: [String]? = nil

    enum CodingKeys: String, CodingKey {
        case productKey = "product_key"
        case household
        case memberIds = "member_ids"
    }
}

nonisolated struct PricesRequest: Encodable, Sendable {
    var productKeys: [String]
    var country: String = "US"

    enum CodingKeys: String, CodingKey {
        case productKeys = "product_keys"
        case country
    }
}

nonisolated struct ProfileParseRequest: Encodable, Sendable {
    var text: String
    var language: String? = nil

    enum CodingKeys: String, CodingKey { case text, language }
}

// MARK: - Other responses

nonisolated struct Answer: Codable, Hashable, Sendable {
    /// Speak this with AVSpeechSynthesizer in `language`.
    var text: String
    var language: String
}

nonisolated struct PickVerdict: Codable, Hashable, Sendable, Identifiable {
    var memberId: String
    var state: PearState
    var label: String

    var id: String { memberId }

    enum CodingKeys: String, CodingKey {
        case memberId = "member_id"
        case state, label
    }
}

nonisolated struct PickProduct: Codable, Hashable, Sendable, Identifiable {
    var key: String
    var name: String? = nil
    var brand: String? = nil
    var imageUrl: String? = nil
    var category: PearCategory = .food

    var id: String { key }
    var imageURL: URL? { imageUrl.flatMap(URL.init(string:)) }

    enum CodingKeys: String, CodingKey {
        case key, name, brand
        case imageUrl = "image_url"
        case category
    }
}

nonisolated extension PickProduct {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            key: try c.decode(String.self, forKey: .key),
            name: try c.decodeIfPresent(String.self, forKey: .name),
            brand: try c.decodeIfPresent(String.self, forKey: .brand),
            imageUrl: try c.decodeIfPresent(String.self, forKey: .imageUrl),
            category: try c.pear(.category, default: .food)
        )
    }
}

nonisolated struct PickItem: Codable, Hashable, Sendable, Identifiable {
    var product: PickProduct
    var verdicts: [PickVerdict]

    var id: String { product.key }
}

nonisolated struct PicksResponse: Codable, Hashable, Sendable, PearRequestIdentified {
    var items: [PickItem]
    var prefetched: Bool
    var timingsMs: [String: Int] = [:]
    var requestId: String? = nil

    enum CodingKeys: String, CodingKey {
        case items, prefetched
        case timingsMs = "timings_ms"
        case requestId = "request_id"
    }
}

nonisolated extension PicksResponse {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            items: try c.decode([PickItem].self, forKey: .items),
            prefetched: try c.decode(Bool.self, forKey: .prefetched),
            timingsMs: try c.pear(.timingsMs, default: [:]),
            requestId: try c.decodeIfPresent(String.self, forKey: .requestId)
        )
    }
}

nonisolated struct AskResponse: Codable, Hashable, Sendable, PearRequestIdentified {
    var transcript: String
    var language: String
    var intent: PearIntent
    var productQuery: String? = nil
    var memberIds: [String] = []
    /// .scanNeeded -> open the camera; .result -> show scanResult / speak answer.
    var next: PearAskNext
    var answer: Answer
    var scanResult: ScanResult? = nil
    // additive
    /// Filled when intent == .alternatives.
    var picks: PicksResponse? = nil
    var timingsMs: [String: Int] = [:]
    var requestId: String? = nil
    var debug: PearJSONValue? = nil

    enum CodingKeys: String, CodingKey {
        case transcript, language, intent
        case productQuery = "product_query"
        case memberIds = "member_ids"
        case next, answer
        case scanResult = "scan_result"
        case picks
        case timingsMs = "timings_ms"
        case requestId = "request_id"
        case debug
    }
}

nonisolated extension AskResponse {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            transcript: try c.decode(String.self, forKey: .transcript),
            language: try c.decode(String.self, forKey: .language),
            intent: try c.decode(PearIntent.self, forKey: .intent),
            productQuery: try c.decodeIfPresent(String.self, forKey: .productQuery),
            memberIds: try c.pear(.memberIds, default: []),
            next: try c.decode(PearAskNext.self, forKey: .next),
            answer: try c.decode(Answer.self, forKey: .answer),
            scanResult: try c.decodeIfPresent(ScanResult.self, forKey: .scanResult),
            picks: try c.decodeIfPresent(PicksResponse.self, forKey: .picks),
            timingsMs: try c.pear(.timingsMs, default: [:]),
            requestId: try c.decodeIfPresent(String.self, forKey: .requestId),
            debug: try c.decodeIfPresent(PearJSONValue.self, forKey: .debug)
        )
    }
}

nonisolated struct Price: Codable, Hashable, Sendable {
    var amount: Double
    var currency: String = "USD"
    var retailer: String
    var size: String? = nil
    var sourceTitle: String? = nil
    var sourceUrl: String? = nil
    /// ISO 8601 UTC
    var checkedAt: String
    var source: PearPriceSource = .googleSearch
    /// Google Search Suggestions chip HTML. When present it must be shown (Grounding terms).
    var searchEntryHtml: String? = nil

    enum CodingKeys: String, CodingKey {
        case amount, currency, retailer, size
        case sourceTitle = "source_title"
        case sourceUrl = "source_url"
        case checkedAt = "checked_at"
        case source
        case searchEntryHtml = "search_entry_html"
    }

    /// "$3.99"
    var displayAmount: String { amount.formatted(.currency(code: currency)) }
    /// "About $3.99 at Target" (addendum 6 app copy; follow with `Price.attribution`).
    var displayText: String { "About \(displayAmount) at \(retailer)" }
    static let attribution = "Found with Google Search."
    var sourceURL: URL? { sourceUrl.flatMap(URL.init(string:)) }
    var checkedDate: Date? { try? Date(checkedAt, strategy: .iso8601) }
}

nonisolated extension Price {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            amount: try c.decode(Double.self, forKey: .amount),
            currency: try c.pear(.currency, default: "USD"),
            retailer: try c.decode(String.self, forKey: .retailer),
            size: try c.decodeIfPresent(String.self, forKey: .size),
            sourceTitle: try c.decodeIfPresent(String.self, forKey: .sourceTitle),
            sourceUrl: try c.decodeIfPresent(String.self, forKey: .sourceUrl),
            checkedAt: try c.decode(String.self, forKey: .checkedAt),
            source: try c.pear(.source, default: .googleSearch),
            searchEntryHtml: try c.decodeIfPresent(String.self, forKey: .searchEntryHtml)
        )
    }
}

nonisolated struct PricesResponse: Codable, Hashable, Sendable, PearRequestIdentified {
    /// product key -> price, or nil when no price was found in time.
    var prices: [String: Price?]
    var timingsMs: [String: Int] = [:]
    var requestId: String? = nil

    enum CodingKeys: String, CodingKey {
        case prices
        case timingsMs = "timings_ms"
        case requestId = "request_id"
    }

    /// Flattened lookup: nil both for "no price" and "key not asked".
    func price(for key: String) -> Price? { prices[key] ?? nil }
}

nonisolated extension PricesResponse {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            prices: try c.decode([String: Price?].self, forKey: .prices),
            timingsMs: try c.pear(.timingsMs, default: [:]),
            requestId: try c.decodeIfPresent(String.self, forKey: .requestId)
        )
    }
}

/// One SSE `data:` line from /v1/explain.
nonisolated struct ExplainEvent: Codable, Hashable, Sendable {
    var memberId: String
    var language: String
    var text: String

    enum CodingKeys: String, CodingKey {
        case memberId = "member_id"
        case language, text
    }
}

nonisolated struct ProfileChip: Codable, Hashable, Sendable, Identifiable {
    var group: PearProfileGroup
    var id: String
    /// Only for allergies.
    var level: PearAllergyLevel? = nil
    /// Additive: display label in the request language.
    var label: String? = nil
    /// Additive: e.g. "carmine: insect-based red color, matters for vegans".
    var note: String? = nil

    enum CodingKeys: String, CodingKey { case group, id, level, label, note }
}

nonisolated extension ProfileChip {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            group: try c.decode(PearProfileGroup.self, forKey: .group),
            id: try c.decode(String.self, forKey: .id),
            level: try c.decodeIfPresent(PearAllergyLevel.self, forKey: .level),
            label: try c.decodeIfPresent(String.self, forKey: .label),
            note: try c.decodeIfPresent(String.self, forKey: .note)
        )
    }
}

nonisolated struct ProfileParseResponse: Codable, Hashable, Sendable, PearRequestIdentified {
    var chips: [ProfileChip]
    var unmatched: [String]
    var timingsMs: [String: Int] = [:]
    var requestId: String? = nil

    enum CodingKeys: String, CodingKey {
        case chips, unmatched
        case timingsMs = "timings_ms"
        case requestId = "request_id"
    }
}

nonisolated extension ProfileParseResponse {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            chips: try c.decode([ProfileChip].self, forKey: .chips),
            unmatched: try c.decode([String].self, forKey: .unmatched),
            timingsMs: try c.pear(.timingsMs, default: [:]),
            requestId: try c.decodeIfPresent(String.self, forKey: .requestId)
        )
    }
}

nonisolated struct KbSuggestion: Codable, Hashable, Sendable, Identifiable {
    /// allergies | conditions | skin | diets | less_of | ...
    var group: String
    var id: String
    /// In the requested language.
    var label: String
    /// en / vi / es
    var labels: [String: String] = [:]
    var score: Double = 0

    enum CodingKeys: String, CodingKey { case group, id, label, labels, score }
}

nonisolated extension KbSuggestion {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            group: try c.decode(String.self, forKey: .group),
            id: try c.decode(String.self, forKey: .id),
            label: try c.decode(String.self, forKey: .label),
            labels: try c.pear(.labels, default: [:]),
            score: try c.pear(.score, default: 0)
        )
    }
}

nonisolated struct KbSearchResponse: Codable, Hashable, Sendable {
    var query: String
    var suggestions: [KbSuggestion]
}

nonisolated struct ErrorBody: Codable, Hashable, Sendable {
    var code: PearErrorCode
    /// User-facing English sentence from the backend.
    var message: String
    var next: PearNextAction? = nil
    var requestId: String? = nil

    enum CodingKeys: String, CodingKey {
        case code, message, next
        case requestId = "request_id"
    }
}

nonisolated extension ErrorBody {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            code: try c.decode(PearErrorCode.self, forKey: .code),
            message: try c.decode(String.self, forKey: .message),
            next: try c.decodeIfPresent(PearNextAction.self, forKey: .next),
            requestId: try c.decodeIfPresent(String.self, forKey: .requestId)
        )
    }
}

nonisolated struct ErrorResponse: Codable, Hashable, Sendable {
    var error: ErrorBody
    /// Only with the debug flag: server-side detail (never shown to users).
    var debug: PearJSONValue? = nil

    enum CodingKeys: String, CodingKey { case error, debug }
}

nonisolated extension ErrorResponse {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            error: try c.decode(ErrorBody.self, forKey: .error),
            debug: try c.decodeIfPresent(PearJSONValue.self, forKey: .debug)
        )
    }
}

nonisolated struct HealthResponse: Codable, Hashable, Sendable {
    var ok: Bool
    var service: String
    var version: String
    var env: String
    var region: String? = nil
    /// firestore | memory
    var store: String
    /// ok | unavailable: <why>
    var gemini: String
    /// off | optional | required
    var auth: String
    /// adk | direct
    var askEngine: String
    var kbVersion: String? = nil
    var uptimeS: Int
    var features: [String: Bool] = [:]
    /// Only with ?deep=1.
    var checks: PearJSONValue? = nil

    enum CodingKeys: String, CodingKey {
        case ok, service, version, env, region, store, gemini, auth
        case askEngine = "ask_engine"
        case kbVersion = "kb_version"
        case uptimeS = "uptime_s"
        case features, checks
    }
}

nonisolated extension HealthResponse {
    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            ok: try c.decode(Bool.self, forKey: .ok),
            service: try c.decode(String.self, forKey: .service),
            version: try c.decode(String.self, forKey: .version),
            env: try c.decode(String.self, forKey: .env),
            region: try c.decodeIfPresent(String.self, forKey: .region),
            store: try c.decode(String.self, forKey: .store),
            gemini: try c.decode(String.self, forKey: .gemini),
            auth: try c.decode(String.self, forKey: .auth),
            askEngine: try c.decode(String.self, forKey: .askEngine),
            kbVersion: try c.decodeIfPresent(String.self, forKey: .kbVersion),
            uptimeS: try c.decode(Int.self, forKey: .uptimeS),
            features: try c.pear(.features, default: [:]),
            checks: try c.decodeIfPresent(PearJSONValue.self, forKey: .checks)
        )
    }
}
