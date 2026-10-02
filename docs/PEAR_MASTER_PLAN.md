# PEAR_MASTER_PLAN.md

Pear. Scan once, know for the whole family.

Version 0.2, Thursday October 1, 2026, 11 PM PT. Owner: Vi.
Event: SF Hacks x GDG AI Hackathon, Friday October 2, 2026, Annex I, SF State. Hacking 11:00 AM to 4:45 PM. Track: GDG, Build with AI for Social Good (one track only).
What changed from v0.1: Vi answered every question. Pear is now a native SwiftUI app built solo with Claude Code agents, with real Google sign-in, all three inputs required, and one track. Section 3 lists every decision. PEAR_QUESTIONS.md holds the questions and answers. familiasafe-gdg-feasibility-report.md still holds the event rules and competitor research.

## 0. Read this first

This file is the master journal for Pear. Vi and every Claude Code session read sections 0 through 8 plus their lane brief in section 12 before writing anything. Agents only edit section 16 (status board) and section 17 (decision log).

Event rule: MLH's standard rules say all project work happens during the hackathon. Ideas from before are fine; code and other project materials made before are not. This file is a plan and a spec. No app code, no final assets and no commits before 11:00 AM Friday. Tool setup (Xcode, signing test, package cache) is fine because none of it ships in the project.

## 1. What Pear is

Pear tells a family, in seconds, whether a food or cosmetic product fits each person in the household. Scan the barcode, snap the label, or ask out loud in your own language. Pear finds the ingredients, checks them against each person's allergies, health conditions, skin issues and diet, and answers per person: "Not for Mia: contains sesame. Fine for Linh, Bà and Leo." If it isn't a fit, Pear shows better picks that work for everyone.

Tagline: Pear pairs your family with products that fit.

Promises, in priority order:

1. Never miss an allergen. When unsure, say so. A false alarm is acceptable. A missed allergen is not.
2. Fast. Barcode answers under 2 seconds. Photo or voice under 5.
3. Calm. Graded, friendly results. The red family of colors is only for allergies, and even then it stays small.
4. For everyone. Each person gets results in their own language. A new user understands every button in under 5 seconds.
5. Google at the center. Gemini does the thinking. Google Cloud and Firebase do the rest (section 7.3).

## 2. Who it's for

Primary user: whoever shops for the household. Everyone else has a profile but never has to touch the app.

Job to be done: "When I'm about to buy or use something, tell me whether it's okay for each person I'm buying for, in my language, before I check out."

Demo family (fictional, preloaded in the app):

| Member | Profile | Language |
|---|---|---|
| Linh (Mom, the shopper, signed in) | Fragrance-sensitive skin | English, Vietnamese |
| Bà (grandmother, 72, age group 65+) | High blood pressure, prediabetes | Vietnamese |
| Mia (8, age group kid) | Strict peanut and sesame allergy, eczema; dye flags on by default | English |
| Leo (16, age group teen) | Vegan | Spanish (shows the third live language) |

Leo reading Spanish is a demo choice so all three live languages appear. Change it if it feels forced.

## 3. Decisions (answered October 1)

| Topic | Decision |
|---|---|
| Team | Solo (Vi) running Claude Code sessions in parallel. Anyone who joins at check-in takes a lane from section 11 |
| App | Native SwiftUI, iOS 17 minimum, demo on Vi's iPhone (iOS 27, so Xcode 27 is required) |
| Backend | Python FastAPI on Cloud Run |
| AI coding | Claude Code with XcodeBuildMCP for builds, device runs, logs and screenshots |
| Track | GDG only; one track per project |
| Inputs that must work | Barcode, label photo and voice, all three |
| Voice | One question, one answer |
| Camera | One camera: barcodes auto-detect, a shutter button takes label photos, a missing barcode jumps to "snap the ingredients" |
| Photos | Pear asks for the ingredient panel. A front-only photo still gets a result, marked "Less sure", with a prompt to snap the ingredients |
| Results | Words plus simple pear symbols; the score stays hidden |
| Headline | Names the problem: "Not for Mia: contains sesame. Fine for Linh, Bà and Leo." |
| May contain | Strict allergy plus "may contain" shows the Allergy chip, worded "May contain peanuts" |
| Allergy levels | Strict and Sensitive |
| Leniency | One medium health or skin issue never goes past "Easy does it" |
| Unclear labels | Lenient: partial reads still show results marked "Less sure". Too blurry asks for a retake. Strict allergies never show Ripe on a partial read |
| Certification marks | Weighed together with the ingredients (section 8.5) |
| Diets | Full list; ambiguous ingredients show "Maybe not vegan" (or halal, kosher) |
| Dyes | On by default for baby and kid profiles, opt-in for everyone else |
| Rinse-off products | Count half |
| Extras | Age groups, pregnancy, medications as pharmacist notes, pets if time |
| Supplements | Basic label read: allergens and diet only, no dosing |
| Family | Default limit 5, the user can raise it; one signed-in account owns the family; each scan checks everyone, with a "Who's this for?" toggle |
| Onboarding | Chips plus instant search, demo family preloaded; unknown terms mapped by Gemini and confirmed by the user |
| Languages | English, Vietnamese and Spanish live; each person gets results in their own language |
| Better picks | Food only, must fit everyone, no prices |
| Sign-in | Real sign-in: Google through Firebase Authentication. Sign in with Apple waits for a paid Apple Developer Program account, because free personal teams can't use it |
| Storage | Profiles on the phone; Firestore caches products and logs scans |
| Look | Pear green leads, cocoa text, blush accents, a simple pear logo with no face, bounce plus haptic on Ripe |
| Detail screen | Highlights the exact words that triggered each flag |
| After Friday | Portfolio piece plus a TestFlight beta, which needs the paid developer program |

## 4. Scope and cut order (solo)

P0, protected. These are the demo:

1. Barcode scan to per-person verdicts. No LLM on this path.
2. Label photo to per-person verdicts, including the front-only "Less sure" path.
3. Voice question to a spoken answer in the asker's language.
4. Result sheet: headline, one chip per person, one reason each, Listen button speaking each person's line in their language (English, Vietnamese, Spanish).
5. Demo family preloaded on the phone.
6. Backend on Cloud Run, Gemini through Agent Platform, Firestore product cache, all on the event credits.
7. Rules engine with zero missed allergens on the test set.

P1, in this order:

1. Onboarding chips with instant search: add or edit a person live.
2. Google sign-in through Firebase (timebox 30 minutes; a guest switch in Settings keeps the demo safe if it breaks).
3. Better picks for food.
4. Detail screen with highlighted ingredients.
5. Certification weighting, kid dye defaults, pregnancy rules (mostly knowledge-base data).
6. Per-stage latency logs and a Cloud Monitoring chart.

P2, only if everything is green at 3:00:

1. Medications as pharmacist notes.
2. Pet profiles.
3. Supplements, basic label read.
4. Cloud Text-to-Speech voices.
5. Blob-shaped avatars.

Cut order when behind: drop P2 from the bottom up, then better picks, then sign-in falls back to the guest switch, then onboarding search (chips stay). Never cut the three inputs or the allergen tests.

## 5. User flows and screens

### 5.1 First launch

1. Sign-in screen: the pear logo, the tagline, one big "Continue with Google" button. (Guest switch hidden in Settings for demo safety.)
2. Language: type its name in any language ("Tiếng Việt", "Español") or tap one of six big buttons. Remembered per person.
3. Family: the demo family is preloaded for Friday. "Add person" opens onboarding.

### 5.2 Onboarding a person (under 60 seconds, every step skippable)

1. Name, avatar sticker, type (person or pet, pets are P2), age group (baby, kid, teen, adult, 65+).
2. Allergies: peanut, tree nuts, milk, egg, wheat, soy, fish, shellfish, sesame (the US major nine), plus gluten (celiac), lactose, mustard, sulfites, corn, kiwi. Each chosen allergy gets a Strict or Sensitive toggle.
3. Health: high blood pressure, diabetes, prediabetes, high cholesterol, kidney disease, heart disease, pregnancy.
4. Skin: eczema, psoriasis, sensitive skin, acne-prone, rosacea, fragrance sensitivity.
5. Diet: vegetarian, vegan, pescatarian, halal, kosher, no pork, no beef, no alcohol, Jain, Buddhist vegetarian, gluten-free.
6. Less of: sugar, sodium, saturated fat, caffeine, artificial dyes (on by default for baby and kid), palm oil.
7. Medications (P2): free text, used only for pharmacist notes.
8. Every chip screen has a search bar that filters instantly from a list bundled in the app. An unknown term goes to Gemini, which suggests the closest group ("carmine: insect-based red color, matters for vegans") and asks to confirm.

### 5.3 Home

One big Scan button. A smaller Ask Pear mic button. Family avatars (tap to edit). Recent scans with their most serious chip.

### 5.4 Scan (one camera)

The camera opens with live barcode detection and a shutter button. Barcode found: result in under 2 seconds. Barcode not found: "Snap the ingredients." Shutter: Gemini reads the photo. If the photo shows only the front of the package, Pear identifies the product, gives a "Less sure" result if it can match it, and asks for the ingredient panel.

### 5.5 Ask Pear (voice, one shot)

Hold to talk in any language: "Bà ăn mì này được không?" Pear answers out loud in that language. If the product isn't clear, Pear asks you to scan it.

### 5.6 Result sheet

1. Product photo and name, plus a "Less sure" badge when the data is partial.
2. Headline in the owner's language (rules in 8.6).
3. One chip per person in family order: simple pear symbol, name, one word.
4. Under each chip that isn't Ripe, one line naming the reason, in that person's language.
5. Listen: speaks each person's line in that person's language.
6. Better picks row, which loads after the verdict and never blocks it.
7. Source line: "From Open Food Facts. Check the label." or "Read from your photo."

### 5.7 Details, picks, settings

Details: every reason, the ingredient list with matched words highlighted, the source behind each rule, Report a mistake. Better picks: three food products that pass for everyone. Settings: family limit (default 5), languages, guest switch, About ("Pear isn't medical advice"), sign out.

## 6. Design system

### 6.1 Principles

1. The 5-second rule. If anyone needs more than 5 seconds to understand a button, redesign it. Every button has an icon and a word.
2. One main action per screen.
3. Calm results. No walls of red. Allergy is the only red-family color, on one chip, never a full-screen alarm.
4. Hand-made, not perfect. Slightly uneven corner radii, sticker avatars, tiny rotations. A clean layout with lots of space keeps it tidy.
5. Readable for everyone. Big type, Dynamic Type support, high contrast, color always paired with a symbol and a word.

### 6.2 Color tokens (all text pairs pass WCAG AA 4.5:1, checked October 1)

| Token | Hex | Use |
|---|---|---|
| cream | #FFF8EC | App background |
| card | #FFFDF8 | Cards and sheets |
| cocoa | #4A3426 | Main text (11:1 on cream) |
| latte | #7A5F4B | Secondary text (5.6:1) |
| pear | #8CC084 | Brand green, logo, highlights; not for small text |
| leaf | #46733C | Primary buttons with cream text (5.3:1), green text |
| blush | #F7C9CB | Pink accent, stickers |
| honey | #F2C46B | Easy does it accent |
| peach | #F3A683 | Skip it accent |
| berry | #A93549 | Allergy text and symbol only (5.4:1 on its chip) |
| mist | #D8D1C7 | Unsure, dividers |

Chip backgrounds: Ripe #E3F1DA with leaf text, Easy #FCEFD2, Skip #FBE1D6, Allergy #FBE7EB with berry text, Diet #EFE4DA, Unsure #ECE8E3. Cocoa text on the rest.

### 6.3 Type

Baloo 2 for headings, Nunito for body, Patrick Hand for small handwritten accents. All three are Google Fonts with Vietnamese glyphs. Download the TTF files at the event, add them to the app bundle and list them under UIAppFonts. Chinese, Japanese and Korean fall back to the system font. Don't use Fredoka; it has no Vietnamese.

### 6.4 Logo and result symbols

1. Logo: a simple pear shape with a stem and two leaves, no face. Avoid a single leaf over a rounded body, which reads like Apple's logo; Apple opposed the Prepear app's pear logo in 2020 and Prepear changed its leaf to settle.
2. Result symbols reuse the same pear: Ripe is a filled green pear with a check; Easy does it is a half-filled honey pear; Skip it is a peach pear outline with a minus; Allergy is a small berry shield beside the pear; Diet is a latte pear with a leaf mark; Unsure is a gray dotted pear with a question mark.
3. Draw them as SwiftUI shapes or SF Symbols-style vectors. Gemini image generation in AI Studio can draft the logo at the event.

### 6.5 Motion and copy

1. Ripe for everyone: the pear bounces, light haptic. Allergy: that chip shakes once, warning haptic. Loading: a calm progress animation, never a blank screen.
2. Copy is short and warm. "Easy does it", not "moderate risk". "Not for Mia", not "hazard detected". Every reason names the ingredient. Never say "safe"; say "No problems found for Leo."

## 7. Architecture

### 7.1 Stack

| Layer | Choice |
|---|---|
| App | SwiftUI, iOS 17 minimum, Xcode 27. Project generated by XcodeGen from a project.yml so agents never hand-edit the Xcode project file |
| Camera | VisionKit DataScannerViewController: live barcode recognition and capturePhoto() for labels, in one view |
| Voice in | AVAudioRecorder records m4a, sent to /v1/ask, understood by Gemini |
| Voice out | AVSpeechSynthesizer with vi-VN, es-US and en-US voices; Cloud Text-to-Speech is P2 |
| Networking | URLSession async/await with Codable models that mirror the contracts in 7.6 |
| Local data | The household saved as JSON in Application Support |
| Sign-in | Firebase Authentication with Google Sign-In (Swift Package Manager). The backend verifies Firebase ID tokens with firebase-admin |
| Backend | Python FastAPI on Cloud Run |
| AI | Gemini 3.5 Flash-Lite through Gemini Enterprise Agent Platform (the new name for Vertex AI), billed to the event credits |
| Data | Firestore, Open Food Facts, Open Beauty Facts, USDA FoodData Central |

Why native over Expo now: it's Vi's call, and one upside is real. The phone talks to Cloud Run over the internet, so the venue Wi-Fi blocking phone-to-laptop traffic doesn't matter.

### 7.2 Build workflow (no living in Xcode)

1. Claude Code edits Swift files. XcodeBuildMCP builds, installs and launches on the iPhone over USB, streams logs and takes screenshots.
2. After adding files, run `xcodegen generate`. The generated .xcodeproj stays out of git.
3. UI-only work runs in the Simulator (no camera there; use bundled sample images).
4. Swift tips for agents: SwiftUI with @Observable models; small views; check DataScannerViewController.isSupported and isAvailable; Info.plist needs camera and microphone usage strings, UIAppFonts and the Google sign-in URL scheme. If concurrency errors pile up, set the target to Swift 5 language mode with minimal strict concurrency checking.
5. Free signing (personal team) works for running on the phone. It can't do Sign in with Apple, push notifications or TestFlight.

### 7.3 Google products in Pear

| Google product | Job | Priority |
|---|---|---|
| Gemini 3.5 Flash-Lite | Reads label photos, understands voice questions, maps unknown ingredients and search terms, writes each person's line in their language | P0 |
| Gemini Enterprise Agent Platform | Runs Gemini on the credited project | P0 |
| Cloud Run | Hosts the Pear API | P0 |
| Cloud Build and Artifact Registry | Build the container on each `gcloud run deploy --source .` | P0 |
| Firestore | Product cache and scan log | P0 |
| Google AI Studio | Prompt testing, logo drafts with Gemini image generation | P0 |
| Google Fonts | Baloo 2, Nunito, Patrick Hand | P0 |
| Firebase Authentication | Real accounts | P1 |
| Google Sign-In | "Continue with Google" | P1 |
| Cloud Logging and Monitoring | Per-stage latency and a chart for judges | P1 |
| Secret Manager | Holds the USDA key | P1 |
| Stitch | Screen drafts at 11:00, optional | P1 |
| Cloud Text-to-Speech | Better voices | P2 |
| Material 3 Expressive shapes | Blob avatars | P2 |

In the pitch, count only what got built.

### 7.4 Request flow

```
iPhone (SwiftUI)
  barcode | photo (+ optional front photo) | voice   + household JSON + Firebase ID token
        |
        v
Cloud Run: Pear API (FastAPI)  -- verifies the token (P1)
  /v1/scan/barcode  -> Firestore cache -> Open Food Facts / Open Beauty Facts / USDA FDC
  /v1/scan/photo    -> Gemini 3.5 Flash-Lite: photo -> JSON (ingredients or front-only)
  /v1/ask           -> Gemini 3.5 Flash-Lite: audio -> intent + product query -> lookup
        |
        v
  Normalizer (knowledge base + one Gemini call for unknown terms)
        |
        v
  Rules engine (deterministic, sourced) -> verdict per person + headline  -> phone
        +--> /v1/explain  Gemini polishes each person's line in their language (P1, streamed)
        +--> /v1/picks    food alternatives, re-checked by the same engine (P1)
  Structured logs with per-stage timings -> Cloud Logging and Monitoring
```

P0 reasons come from the rules engine as templated lines in English, Vietnamese and Spanish, so the barcode path needs no Gemini call at all. Gemini's /v1/explain only makes them friendlier.

### 7.5 Latency budget

| Path | Target |
|---|---|
| Barcode, cache hit | under 0.5 s |
| Barcode, cache miss | under 1.5 s |
| Photo | under 4 s |
| Voice | under 5 s |

Rules: no LLM on the barcode path; verdict first, words later; small JSON outputs; thinking at minimal; one warm Cloud Run instance during judging; cache by product. Log real timings and quote only measured numbers.

### 7.6 API contracts

The backend also serves every path under /mock with fixtures from contracts/fixtures, so the app can be built before the pipeline works. Signed-in requests send `Authorization: Bearer <Firebase ID token>`.

Household:

```json
{
  "household_id": "hh_demo",
  "owner_language": "en",
  "member_limit": 5,
  "members": [
    {
      "id": "m_mia",
      "type": "person",
      "species": null,
      "name": "Mia",
      "age_group": "kid",
      "languages": ["en"],
      "allergies": [{"id": "peanut", "level": "strict"}, {"id": "sesame", "level": "strict"}],
      "conditions": [],
      "pregnant": false,
      "skin": ["eczema"],
      "diets": [],
      "less_of": [],
      "avoid_dyes": true,
      "medications": []
    }
  ]
}
```

Endpoints:

| Method and path | Input | Output |
|---|---|---|
| POST /v1/scan/barcode | `{"code": "...", "household": {...}}` | ScanResult |
| POST /v1/scan/photo | multipart: image (JPEG), optional front image, household | ScanResult |
| POST /v1/ask | multipart: audio (m4a), household | transcript, language, intent, product_query, member_ids, next ("scan_needed" or "result"), optional ScanResult |
| POST /v1/explain (P1) | product_key, household | event stream of `{"member_id", "language", "text"}` |
| POST /v1/picks (P1) | product_key, household | items with per-person states |
| POST /v1/profile/parse | text, language | chips `[{"group", "id", "level"}]`, unmatched terms |
| GET /v1/kb/search?q= | query | suggestions (the app also bundles this list) |

ScanResult:

```json
{
  "product": {
    "key": "off:0123456789012",
    "name": "Sesame Crackers",
    "brand": "Example Brand",
    "category": "food",
    "product_type": null,
    "image_url": "https://...",
    "source": "open_food_facts",
    "photo_kind": null,
    "data_confidence": "high",
    "less_sure": false,
    "ask_for_ingredients": false,
    "ingredients": ["wheat flour", "sesame seeds", "salt"],
    "contains": ["wheat", "sesame"],
    "may_contain": ["peanut"],
    "certifications": [],
    "label_language": "en"
  },
  "verdicts": [
    {
      "member_id": "m_mia",
      "state": "ALLERGY",
      "label": "Allergy",
      "reasons": [{"code": "allergen.sesame", "text": "Contains sesame (sesame seeds)", "language": "en", "severity": "hard", "matched": ["sesame seeds"], "source": "fda_major_allergens"}],
      "score": null
    }
  ],
  "headline": {"text": "Not for Mia: contains sesame. Fine for Linh and Leo.", "language": "en"},
  "timings_ms": {"lookup": 420, "rules": 6, "total": 610}
}
```

Allowed values: source is open_food_facts, open_beauty_facts, usda_fdc or photo. photo_kind is ingredients, front_only, other or null. data_confidence is high, medium or low. state is RIPE, EASY, SKIP, ALLERGY, DIET or UNSURE. severity is hard, soft or note. product_type is leave_on, rinse_off or null.

Errors: `{"error": {"code": "not_found|unreadable|too_blurry|rate_limited|internal", "message": "...", "next": "take_photo|retake|retry"}}`

### 7.7 Firestore

products/{key}: normalized product record, source, fetched_at. scans/{id}: user id (from the token), product_key, state per member, timings, created_at, no photos. The knowledge base ships as JSON inside the API container, with a small copy in the app for instant search.

### 7.8 Repo layout (one public repo named pear, created at 11:00)

```
pear/
  PEAR_MASTER_PLAN.md
  CLAUDE.md            "Read PEAR_MASTER_PLAN.md sections 0 to 8 and your lane brief first."
  README.md
  .mcp.json            XcodeBuildMCP at project scope
  contracts/           household.example.json, scan_result.example.json, fixtures/
  ios/
    project.yml        XcodeGen spec (.xcodeproj is generated, not committed)
    Pear/App/          PearApp, routing
    Pear/Theme/        colors, fonts, radii
    Pear/Models/       Codable models from contracts
    Pear/Services/     APIClient, HouseholdStore, SpeechService, AudioRecorder, AuthService, Haptics
    Pear/Features/     Home, Scan, Result, Ask, Onboarding, Family, Details, Picks, Settings, SignIn
    Pear/Resources/    fonts, kb_index.json, Localizable.xcstrings (en, vi, es), sample label images
  api/
    main.py
    pipeline/          lookup_off, lookup_fdc, photo_gemini, ask_gemini, explain, picks, auth
    rules/             engine, scoring, templates (reason lines in en, vi, es)
    kb/                allergens.json, diets.json, conditions.json, skin.json, additives.json, meds.json, pets.json
    tests/             test_rules, cases/*.json
  demo/                products.csv (barcode, path, expected state per member)
  docs/                architecture image, pitch notes
```

### 7.9 Secrets and config

1. On Cloud Run, Gemini needs no API key; the service account gets the Agent Platform user role. Locally, `gcloud auth application-default login`.
2. The current Google Gen AI SDK README uses `genai.Client(enterprise=True, project=..., location="global")` with GOOGLE_GENAI_USE_ENTERPRISE; older guides show `vertexai=True`. Use whichever the installed version accepts.
3. Open Food Facts needs only a custom User-Agent with an app name and contact email.
4. GoogleService-Info.plist goes in the app at the event and stays out of the public repo.
5. Never commit keys. Vi's personal Gemini key stays out of chat, the repo and screenshots.

## 8. The brain: rules engine and scoring

### 8.1 Pipeline

Get ingredients (database or Gemini), normalize them (sub-ingredients, E-numbers, synonyms in English, Vietnamese and Spanish), match each person's profile, decide states (8.3), score graded signals (8.4), apply certification and confidence rules (8.5), build the headline (8.6). Everything except the unknown-term call is plain Python, so the same input always gives the same answer.

### 8.2 Knowledge base files

1. allergens.json: the major nine plus gluten, lactose, mustard, sulfites, celery, lupin, corn, kiwi, each with synonyms and derivatives in three languages (milk: casein, caseinate, whey, lactose, ghee, butter; sesame: tahini, benne, sesame oil; peanut: arachis oil, groundnut).
2. diets.json: forbidden and maybe ids per diet. Vegan forbids gelatin, carmine, honey, beeswax, shellac, whey, lanolin; maybe: natural flavors, mono- and diglycerides, enzymes, vitamin D3, L-cysteine. Halal forbids pork, lard, alcohol; maybe: gelatin of unknown source, L-cysteine. Kosher forbids pork and shellfish; maybe: gelatin. No beef forbids beef and tallow. Jain adds root vegetables, eggs and honey. Buddhist vegetarian often adds onion and garlic.
3. conditions.json, skin.json: thresholds and flagged ingredients with weights from 8.4, including pregnancy (retinoids, caffeine).
4. additives.json: dyes (Red 40, Allura Red AC, E129; Yellow 5, tartrazine, E102; Yellow 6, E110; Blue 1, E133; Blue 2, E132; Green 3, E143; Red 3, erythrosine, E127), phosphates, potassium additives, formaldehyde releasers, methylisothiazolinone, fragrance.
5. meds.json (P2): pharmacist notes only. Grapefruit, Seville oranges, pomelos and tangelos with some statins and some blood pressure drugs (FDA). Vitamin K-rich foods with warfarin: keep intake steady, don't avoid (NIH). More only after a pharmacist reviews them.
6. pets.json (P2): for dogs and cats, xylitol, chocolate, coffee and caffeine, grapes and raisins, onions, garlic and chives, macadamia nuts, alcohol, yeast dough (ASPCA).

Every entry carries a source id from section 21.

### 8.3 States

| State | Chip word | Symbol | When |
|---|---|---|---|
| RIPE | Ripe | Filled green pear with a check | No hits, or only small notes |
| EASY | Easy does it | Half-filled honey pear | Graded issues, score 60 to 84 |
| SKIP | Skip it | Peach pear outline with a minus | Graded issues, score under 60 |
| ALLERGY | Allergy, May contain, Toxic for dogs | Berry shield beside the pear | Strict allergen in ingredients, contains or may-contain; pet toxin |
| DIET | Not vegan, Maybe not vegan, Check | Latte pear with a leaf mark | Diet conflict, ambiguous ingredient, or a certification that disagrees with the ingredients |
| UNSURE | Unsure | Gray dotted pear with a question mark | Partial label and a strict allergy with no allergen found |

Precedence: ALLERGY, UNSURE, DIET, SKIP, EASY, RIPE. A chip shows the highest; details list every reason. Pharmacist notes never change the state.

### 8.4 Scoring (health, skin and less-of signals only)

Score = 100 minus penalties (floor 0). Penalty = base weight × amount factor × context multiplier × product-type multiplier. The score stays hidden and only picks the word.

1. Amount factor uses FDA Daily Value per serving (5% DV or less is low, 20% DV or more is high): under 5% = 0; 5 to 19% = 0.3; 20 to 39% = 1.0; 40% or more = 1.6. Ingredient flags count 1.0 when present.
2. Context: 1.0 for a matching condition, 0.5 for a less-of goal, 0 if it doesn't apply.
3. Cosmetics: leave-on 1.0, rinse-off 0.5.

| Signal | Applies to | Weight |
|---|---|---|
| Sodium | High blood pressure, kidney disease, heart disease; less sodium | 25 |
| Added sugars | Diabetes, prediabetes; less sugar | 25 |
| Fast sugars in the first 3 ingredients | Diabetes, prediabetes | 10 |
| Saturated fat | High cholesterol, heart disease; less saturated fat | 20 |
| Phosphate additives | Kidney disease | 20 |
| Potassium chloride and other potassium additives | Kidney disease with a potassium limit | 30 |
| Synthetic dyes | avoid_dyes on (default for baby and kid) | 15 |
| Fragrance | Eczema, sensitive skin, fragrance sensitivity, rosacea | 20 |
| Methylisothiazolinone, methylchloroisothiazolinone | Eczema, sensitive skin | 45 |
| Formaldehyde releasers | Eczema, sensitive skin | 45 |
| Sodium lauryl sulfate | Eczema, sensitive skin | 10 |
| Retinoids | Pregnancy, with "ask your doctor" | 45 |
| Caffeine | Pregnancy; less caffeine | 15 |

Tiers: 85 to 100 RIPE, 60 to 84 EASY, under 60 SKIP. Leniency rule: one medium issue alone never drops below EASY; SKIP needs a weight-45 hit or two real issues.

Examples: Bà and noodles at 38% DV sodium: score 75, EASY. Add 25% DV added sugars: 50, SKIP. Mia and a leave-on lotion with methylisothiazolinone and fragrance: 35, SKIP. Mia and a rinse-off wash with methylisothiazolinone: 77.5, EASY. Linh and a rinse-off shampoo with fragrance: 90, RIPE with a note.

### 8.5 Certification and confidence rules

Certification marks (vegan, halal, kosher) are weighed with the ingredients:

1. Mark seen, no conflicting ingredients: no diet flag, plus a "Certified" badge.
2. Mark seen, only maybe ingredients: the mark settles them. No flag, a small note ("natural flavors assumed plant-based, label says vegan").
3. Mark seen, a definite conflict (honey on a "vegan" label): DIET "Check: label says vegan but lists honey."
4. No mark: definite conflict shows "Not vegan", maybe-only shows "Maybe not vegan".

Label confidence:

1. Full read: normal results.
2. Partial read: results with a "Less sure" badge. People with strict allergies get ALLERGY if an allergen was found, otherwise UNSURE. Everyone else is graded normally.
3. Too blurry: no verdicts, "Too blurry. Retake?"
4. Front only: Gemini names the product, the backend searches Open Food Facts by brand and name. Match found: results with "Less sure: matched by name" and "Snap the ingredients to confirm". No match: "Snap the ingredients."
5. Database data always shows its source, and strict allergies get a "Check the label" nudge with a one-tap photo check. If photo and database disagree, trust the photo.

Supplements (P2) get allergen and diet checks only (gelatin capsules, fish oil, shellfish-derived glucosamine) plus a fixed line: "Pear doesn't check supplement doses or drug interactions."

### 8.6 Headline rules

1. Everyone RIPE: "Ripe for everyone. Go for it."
2. Otherwise lead with the most serious clause: "Not for Mia: contains sesame." / "Couldn't confirm for Mia. Retake the ingredients?" / "Not vegan for Leo: gelatin." / "Skip it for Mia: skin allergen." / "Easy does it for Bà: high sodium."
3. Then, if anyone is RIPE: "Fine for Linh and Leo."
4. Two sentences maximum, in the owner's language. Each chip's reason is in that person's language.

### 8.7 No-missed-allergen policy

1. Synonyms, derivatives, E-numbers and three languages; contains and may-contain parsed separately.
2. Strict allergy plus may-contain: ALLERGY, "May contain peanuts".
3. A partial or missing ingredient list never shows RIPE for someone with a strict allergy.
4. Gemini never decides a state. It reads, maps and explains.
5. Test gate: zero misses on the allergen test set before the 3:00 checkpoint. A miss stops feature work until fixed.
6. False alarms are allowed but labeled ("possible", "may contain", "maybe not vegan").

### 8.8 Gemini calls (write the prompts at the event)

| Call | Output fields | Rules |
|---|---|---|
| read_label | photo_kind, product_name, brand, category, product_type, ingredients in printed order plus English, contains_statement, may_contain_statement, certifications, nutrition per serving (sodium mg and %DV, added sugars g and %DV, saturated fat), label_language, legibility (full, partial, unreadable) | Copy as printed, never invent missing parts, mark partial when cut off |
| understand_voice | transcript, language, intent, product_query, member_ids | |
| parse_profile | chips with group, id, level; unmatched terms | Knowledge-base ids only; the user confirms |
| normalize_unknown | knowledge-base id or "unknown" per string, with confidence | One batched call |
| explain (P1) | one line per person in their language, 15 words or fewer | No medical advice, never "safe" |

Settings: gemini-3.5-flash-lite, temperature left at the default 1.0 (Google's guidance for Gemini 3 models), thinking at minimal, JSON schema output, default (high) image resolution for labels. Switch read_label to gemini-3.8-flash only if Flash-Lite misses ingredients in testing.

## 9. Data sources

| Source | Use | Limits and notes |
|---|---|---|
| Open Food Facts API v3 | Food by barcode and by name: ingredients, allergens, traces, additives, nutrition, vegan analysis, labels, categories | No key; custom User-Agent; 15 product reads and 10 searches per minute per IP, so call it from Cloud Run and cache |
| Open Beauty Facts | Cosmetics by barcode | Thin coverage; photos are the main path for cosmetics |
| USDA FoodData Central | Backup for US branded foods | Free key; 1,000 requests per hour per IP |
| Gemini on Agent Platform | Photos, voice, profile parsing, unknown terms, explanations | Event credits |
| FDA, NIH, ASPCA pages | Medications and pet rules | Section 21 |

## 10. Languages

1. Each person has a language list; the first is primary.
2. Set a language by typing its name in any language or tapping one of six buttons; Gemini maps unusual names.
3. UI strings live in a String Catalog with English, Vietnamese and Spanish, translated by Gemini at 2:00 PM. Vi checks Vietnamese. Spanish gets a Gemini back-translation check, and ideally a Spanish speaker at the venue looks at it.
4. Reason templates exist in all three languages, so barcode results need no translation call.
5. Listen uses the person's language: vi-VN for Bà, es-US for Leo, en-US for Mia.

## 11. How one person runs this: Claude Code lanes

Vi is the reviewer and integrator: starts each lane with its brief, tests on the phone, merges, decides cuts. Four Claude Code sessions run in parallel:

| Lane | Owns | Where it works | Builds on |
|---|---|---|---|
| L1 iOS shell, design, onboarding, sign-in | ios/Pear/Theme, App, Features/Onboarding, Family, Settings, SignIn, Resources (strings, fonts) | A git worktree on branch `ui` (`git worktree add ../pear-ui -b ui`) | Simulator |
| L2 iOS scan, result, voice, Listen | ios/Pear/Features/Home, Scan, Result, Ask, Details, Picks; Services; Models | main | The iPhone (only L2 builds to the device) |
| L3 Backend | api/ except rules and kb; contracts/fixtures; deploy | main | Cloud Run |
| L4 Brain | api/rules, api/kb, api/tests, ios kb_index export | main | pytest |

Merge `ui` into main at each checkpoint. Lanes stage only their own folders (never `git add -A`). If a teammate joins at check-in: give them L1, or the demo and pitch if they don't code.

## 12. Lane briefs (paste one per Claude Code session)

### 12.0 Rules for every lane

1. Read PEAR_MASTER_PLAN.md sections 0 to 8 first. Follow the contracts in 7.6 exactly. Need a contract change? Stop and ask Vi.
2. Edit only your lane's folders. Stage only your own files. Small commits like "[L3] barcode lookup".
3. No secrets in code or commits.
4. iOS lanes: SwiftUI, iOS 17 minimum, no third-party packages except Firebase Auth and GoogleSignIn. Run `xcodegen generate` after adding files. Build with XcodeBuildMCP and fix every error before handing back.
5. Use the theme tokens for every color, font and radius.
6. Never write medical advice. Never write "safe". Reasons name the ingredient.
7. A working demo by 3:00 PM beats completeness. P0 first.
8. After each task, update your row in section 16.

### 12.1 L1: iOS shell, design, onboarding, sign-in

1. project.yml (bundle id agreed at 11:00, iOS 17, Info.plist usage strings, UIAppFonts), theme tokens, fonts, app shell with routing.
2. Components: BigButton (symbol plus word), Chip, PersonChip (six states, pear symbols from 6.4), Sheet, Avatar, SearchChips filtering kb_index.json instantly.
3. Demo family preloaded through HouseholdStore (shared with L2).
4. Onboarding per 5.2 and the Family screen; unknown search terms call /v1/profile/parse and ask to confirm.
5. 2:00 PM: String Catalog in English, Vietnamese, Spanish (Gemini translation; Vi checks Vietnamese).
6. P1 sign-in: Firebase Auth with Google Sign-In, a SignIn screen, the ID token attached to every request, a guest switch in Settings. Timebox 30 minutes.

Acceptance: a new user adds a person with two allergies in under 30 seconds; every screen passes the 5-second rule; sign-in works on the phone or the guest switch hides it cleanly.

### 12.2 L2: scan, result, voice, Listen

1. Models from contracts; APIClient with a mock switch; HouseholdStore.
2. Scan: DataScannerViewController with barcode recognition (EAN-13, UPC-A, EAN-8, UPC-E) and capturePhoto(); downscale photos to about 1600 px JPEG; barcode not found jumps to "Snap the ingredients".
3. Result sheet per 5.6, "Less sure" badge, retake states, Listen with AVSpeechSynthesizer per person's language, bounce and haptics.
4. Ask: hold-to-talk with AVAudioRecorder, POST /v1/ask, speak the answer in the asker's language.
5. P1: Details with highlighted matches; Picks row.

Acceptance: on the iPhone, a cached demo barcode shows a result in under 1 second; photo and voice work end to end; Listen speaks Vietnamese for Bà and Spanish for Leo.

### 12.3 L3: backend

1. By 11:30: FastAPI with /health and /mock routes serving fixtures; `gcloud run deploy --source .`; share the URL.
2. Barcode path: Firestore cache, Open Food Facts v3, Open Beauty Facts, USDA FDC; map ingredients, allergens, traces, additives, nutrition per serving, vegan analysis, labels, categories.
3. Photo path: Gemini read_label with a JSON schema; legibility and front-only handling (8.5).
4. /v1/ask with Gemini understand_voice and an Open Food Facts name search; /v1/profile/parse.
5. Call L4's engine for verdicts and the headline.
6. P1: Firebase ID token verification, /v1/explain, /v1/picks (food: same category, drop flagged items, re-check 10 candidates, return top 3 that fit everyone, cache by category), timing logs and a Monitoring chart.
7. Before judging: minimum instances 1, cache warmed with demo products.

Acceptance: cached barcode under 500 ms server time, clear label photo under 4 s, voice under 5 s, every response matches the contract.

### 12.4 L4: brain

1. kb files per 8.2 with source ids; reason templates in English, Vietnamese and Spanish.
2. rules/engine: normalize, match, states, scoring, certification and confidence rules, headline. Pure functions; Gemini only through an injected normalize_unknown callback.
3. Tests: 30 cases, including whey, tahini, arachis oil, "may contain tree nuts", gelatin, honey on a vegan-marked label, Red 40 for a kid profile, leave-on vs rinse-off, partial label with a strict allergy.
4. Export ios/Pear/Resources/kb_index.json for instant search.

Acceptance: all tests pass, zero missed allergens, evaluate under 20 ms for a family of five.

### 12.5 Vi: integrate, demo, pitch

Merge at checkpoints, test every path on the phone, decide cuts at 1:45 and 3:00, run the allergen tests, record the backup video, have an agent draft the README (problem, features, Google products actually built, architecture, credits used, AI tools used as MLH requires, rule sources, "not medical advice"), submit on ShipYard by 4:30, rehearse.

## 13. Timeline (Friday, October 2, solo)

| Time | What |
|---|---|
| 9:00 to 10:00 | Check in, Wi-Fi, redeem credits, link billing, enable APIs (Agent Platform, Cloud Run, Cloud Build, Artifact Registry, Firestore, Secret Manager), create Firestore, add Firebase to the credited project, enable Google sign-in, register the iOS bundle id, download GoogleService-Info.plist. No code |
| 10:00 to 11:00 | Opening; confirm the one-track rule and the pre-work rule. Look for a teammate |
| 11:00 to 11:15 | Repo, CLAUDE.md, .mcp.json, contracts and fixtures committed; start L1 to L4 |
| 11:15 to 12:30 | L3 barcode path and deploy; L4 allergens and states; L1 theme, components, demo family; L2 scanner and result on mocks |
| 12:30 | Checkpoint 1: a real barcode result from Cloud Run on the phone. Merge `ui` |
| 12:30 to 1:45 | L3 photo path; L2 photo capture, Listen; L4 scoring, headline, tests; L1 onboarding and search |
| 1:45 | Checkpoint 2: barcode and photo end to end for the demo family. Cut decision |
| 1:45 to 2:45 | L3 and L2 voice; L1 strings in three languages, then sign-in (30-minute timebox); L3 and L4 food picks |
| 2:45 | Checkpoint 3: all three inputs work. Merge `ui` |
| 2:45 to 3:30 | Polish, highlights, certification rules, P2 only if green; L3 minimum instances and cache warm-up |
| 3:30 to 3:45 | Demo three times in a row; allergen tests; backup video |
| 3:45 | Code freeze |
| 3:45 to 4:30 | README and ShipYard submission by 4:30 (hard deadline 4:45) |
| 4:30 to 5:00 | Rehearse twice; phone charged; Cloud Console tabs open |
| 5:00 to 5:45 | Judging at the table |

## 14. Demo and pitch (5 minutes)

| Time | What happens | Criterion |
|---|---|---|
| 0:00 to 0:25 | "Every family carries a list in their head. Mia can't have sesame. Bà watches her salt. Leo is vegan. The label doesn't know your family. Pear does." Add one true personal reason if you have one | Idea, Presentation |
| 0:25 to 1:40 | Signed in with Google. Barcode on the sesame snack: "Not for Mia: contains sesame. Fine for Linh, Bà and Leo." Listen: Bà's line in Vietnamese, Leo's in Spanish. Photo of the lotion: Mia "Skip it", Linh "Easy does it". Ask in Vietnamese: "Bà ăn mì này được không?" and hear the answer. End on a Ripe product: the pear bounces | Implementation, Design |
| 1:40 to 2:30 | How it works: Gemini reads labels and voices in any language; Open Food Facts handles barcodes; a sourced rules engine decides; Firebase handles accounts; Firestore caches; Cloud Run serves; all on the event credits. Show the latency chart | Track Application |
| 2:30 to 3:15 | Hardest problems: no missed allergens (Gemini reads, rules decide, unsure beats wrong, zero misses on the test set) and speed (barcode path skips the LLM; quote the measured number). Bonus: building a native iOS app in a day with AI agents and no Swift background | Presentation |
| 3:15 to 3:45 | Design and responsibility: the 5-second rule, calm colors, one chip per person, no medical advice, a source behind every flag, no photos stored | Design, Idea |
| 3:45 to 5:00 | Questions | |

Short answers: Yuka scores everyone the same and won't rate a Vietnamese label; Fig and IngrediCheck check several people but only food and only English. Gemini Live doesn't keep your family's rules and can answer differently each time; Pear applies saved rules the same way every scan. Accuracy: quote the 3:00 PM test results only. Privacy: claim only what got built (no photos stored, Google sign-in, profiles on the phone).

## 15. Risks

| Risk | Fix |
|---|---|
| Xcode older than 27 can't install to an iOS 27 phone | Check tonight; start the update now |
| First device build fails (signing, Developer Mode, trust) | Throwaway build tonight |
| Swift compile errors with no Swift background | XcodeBuildMCP lets agents read and fix errors; small files; Swift 5 mode if concurrency errors pile up |
| Firebase and GoogleSignIn packages download slowly | Warm the package cache tonight in the throwaway project |
| Sign-in setup eats time | 30-minute timebox; guest switch |
| One person, three inputs | Cut order in section 4; checkpoints; protect P0 |
| Open Food Facts rate limits | Call from Cloud Run, cache in Firestore, warm the demo products |
| Gemini slower than target | Flash-Lite, minimal thinking, small JSON, downscaled photos, progress UI |
| A missed allergen | Synonym lists, tests, the Unsure rule, the 3:00 gate |
| Spanish mistakes | Back-translation check, a Spanish speaker at the venue |
| Free signing limits | Delete the throwaway app from the phone after the test |
| Cable or network trouble | Bring a USB cable for the phone; Cloud Run works on any network; phone hotspot as backup |

## 16. Status board (lanes update their own row)

| Lane | Doing now | Status | Blocker | Updated |
|---|---|---|---|---|
| L1 | | Not started | | |
| L2 | | Not started | | |
| L3 | Code freeze; docs (docs/BACKEND.md, README.md, CLAUDE.md) | Done in code, green offline (3213 tests, smoke 68/68 on a local server). Built: all 8 /v1 endpoints with /mock twins and /debug tools; barcode (Firestore cache, OFF + OBF race, FDC fallback, no LLM); photo (one label_reader call, front-only name match, blurry/retake); ask (JSON + audio fallback, ADK Workflow with direct fallback); picks prefetch (vector + category_main fallback); prices (Search grounding, grounded answers only); explain SSE; profile parse; Firebase token verification (PEAR_AUTH=optional); structured logs, Server-Timing, Cloud Trace spans; infra scripts (IAM, deploy, secrets, index, metrics); smoke, warm, ingest scripts. Real OFF barcodes checked locally (Top Ramen, Haribo, Maruchan, Diet Coke) | On Vi: install gcloud, `gcloud auth login` + `gcloud auth application-default login` (ADC); `make iam`; `make deploy` (WARNING: replaces the "0.1" pear-api service the app talks to now, whose shapes differ; see BACKEND.md 9 A2, or deploy with SERVICE=pear-api-v3); then `make indexes`, `make ingest`, `make metrics`, `make warm`; check the Gemini paths once live (photo, ask, explain, profile, prices, picks); optional `make secrets` (USDA key) and PEAR_CONTACT_EMAIL. App side, not backend: Firebase (add to project, Google sign-in, bundle id, GoogleService-Info.plist; app sign-in is a placeholder), fonts (Baloo 2, Nunito, Patrick Hand TTFs + UIAppFonts) | Oct 2, 3:30 PM |
| L4 | Frozen | Done, green. kb: 17 allergens with en/vi/es terms, INCI, E-numbers, OFF tags, diets, certifications, signals, meds, pets; about 250 red-team terms and extra matcher passes (folded accents, OCR hyphens, footnotes, bare INS codes). Engine: states, Strict / Ingredients only levels, may-contain parsing, diets + certs, scoring, confidence, headline, templates in en/vi/es. Allergen gate 1848 passed, 0 misses (114 red-team cases). Exports: kb_index.json + kb_ids.txt (kb_version ce088993a1); fixtures match the engine; openapi up to date | On Vi: fill the DEMO SLOT barcodes in demo/products.csv, then `make warm URL=...` and check DIFF lines; Maruchan has wrong OFF sodium (use Top Ramen for the sodium beat); decide on known false alarms ("Free from: peanuts" reads as Contains); don't run contracts/swift/sync_to_app.sh until the UI agent switches the app to the kit (duplicate Swift types, app kb_index.json is another format) | Oct 2, 3:30 PM |

## 17. Decision log

| When | Decision | Why | Who |
|---|---|---|---|
| Oct 1 | Name is Pear | Vi's call | Vi |
| Oct 1 | Native SwiftUI, built with Claude Code and XcodeBuildMCP | Vi prefers iOS native; agents write the Swift | Vi |
| Oct 1 | Python FastAPI on Cloud Run | Fast to build, best Gemini SDK, works with any client | Plan |
| Oct 1 | No LLM on the barcode path; reason templates in three languages | Speed | Plan |
| Oct 1 | Rules decide; Gemini reads, maps and explains | No missed allergens, same answer every time | Plan |
| Oct 1 | Google sign-in through Firebase; Sign in with Apple later | Free personal team can't use Sign in with Apple | Vi |
| Oct 1 | GDG track only | One track per project | Vi |
| Oct 1 | All answers in section 3 | Q&A session | Vi |
| Oct 2 | Allergy levels are "Strict" (strict) and "Ingredients only" (sensitive). Both show ALLERGY when the allergen is in the ingredients; only Strict also flags "may contain" (Ingredients only shows it as a note) | The toggle means one thing, an allergen in the ingredients is never softened, matches the design copy | Vi + Claude |
| Oct 2 | Backend repo layout: api/, contracts/, infra/, demo/, docs/ at the repo root; root Dockerfile so `gcloud run deploy --source .` works from the root; Swift kit in contracts/swift synced into Pear/Kit | Xcode project already lives at the root; one deploy command | Vi + Claude |
| Oct 2 | fastapi pinned to 0.141.1 | google-adk 2.3.0 caps opentelemetry-api at 1.42.1 | Claude |
| Oct 2 | Results show in ONE language, the shopper's, not each person's own (overrides section 3 "each person gets results in their own language"). Home has a "Who's shopping today?" switcher (person or language); the app sends a copy of the household with everyone's first language set to it, the saved profiles keep their real languages | Mixed "Ripe / Chín / Madura" chips on one screen were confusing; the shopper reads everything | Vi |

## 18. Roadmap after Friday

| Phase | Dates | Goals |
|---|---|---|
| 1. Make it real | Oct 3 to Oct 18, 2026 | Join the Apple Developer Program ($99 a year) for TestFlight and Sign in with Apple. In-app account deletion. Household sync to Firestore. TestFlight beta. Ten interviews with families who shop for relatives. A dietitian or allergist reviews the knowledge base |
| 2. Fill the gaps | Oct 19 to Dec 15, 2026 | Supplements from the NIH label database, prices where a real source exists, nearby stores with Google Places, offline cache, ten languages, a pharmacist-reviewed medication list, accessibility audit |
| 3. Launch | Jan to Mar 2027 | App Store, privacy policy, review of health-app rules such as the FTC Health Breach Notification Rule, family invites, community corrections |
| 4. Grow | Apr 2027 onward | On-device Gemma for offline ingredient mapping, menu and receipt scanning, pantry re-checks when a profile changes, pilots with clinics and campus food pantries; a write-up, demo video and resume line with real numbers |

## 19. Tonight (Thursday, October 1, after 11 PM)

Do these in order, then sleep:

1. Check versions. iPhone: Settings > General > About. Xcode: Xcode > About Xcode. iOS 27 needs Xcode 27. If Xcode is older, start the update now.
2. Throwaway device build: add your Apple ID in Xcode under Settings > Accounts, create an iOS App from the template, plug in the phone with a cable, turn on Developer Mode (Settings > Privacy & Security, the phone restarts), pick your Personal Team, Run, then trust your developer profile on the phone (Settings > General > VPN & Device Management).
3. In that throwaway project, add the Swift packages firebase-ios-sdk (FirebaseAuth) and GoogleSignIn-iOS and let them resolve, so tomorrow's download comes from cache. Then delete the project and remove the app from the phone.
4. Install tools: Homebrew if missing, `brew install xcodegen`, Node (for XcodeBuildMCP), Python 3.13, the gcloud CLI with `gcloud auth login` and `gcloud auth application-default login`, and `claude mcp add XcodeBuildMCP -- npx -y xcodebuildmcp@latest mcp`.
5. Post on the event Discord: "Hi! Quick question for tomorrow: is it okay to bring a written plan and design spec (colors, fonts, screen list) made before the event, as long as all code and assets are made during hacking? Thanks!"
6. Demo products, tonight if a store is open, otherwise on the way in: a sesame snack, instant noodles with a Vietnamese label and high sodium, gummy candy with gelatin and Red 40, a leave-on lotion or kids' wash with fragrance and methylisothiazolinone, and one product that's Ripe for everyone. Check each barcode in the Open Food Facts app before 11:00.
7. Pack: laptop, phone, both chargers, the phone's USB cable, battery pack. Parking is $11.25 (Lots 19, 20 and 25).

## 20. If something breaks tomorrow

1. Device build won't sign or install: check Developer Mode, trust the profile, delete old sideloaded apps, try the Simulator to keep building while you fix it.
2. Swift errors loop: tell the lane to switch to Swift 5 mode and minimal concurrency checking, and to fix one file at a time.
3. Sign-in fails by 2:45: flip the guest switch and move on.
4. Gemini is slow or down: barcode path still works with no AI; demo barcode first.
5. Venue Wi-Fi dies: phone hotspot for the phone and laptop.

## 21. Sources

Event and rules: SF Hacks x GDG AI Hackathon participant handbook; [ShipYard event page](https://shipyardhq.tech/hackathons/sf-hacks-x-gdg-ai-hackathon); [MLH Standard Hackathon Rules](https://github.com/MLH/mlh-policies/blob/main/standard-hackathon-rules.md).

Apple and iOS tooling: [iOS 27 and Xcode 27](https://www.macrumors.com/2026/09/13/ios-27-release-date-new-features/); [Xcode 27.1 beta](https://www.macrumors.com/2026/09/18/apple-releases-xcode-27-1-beta-iphone-duo-support/); [XcodeBuildMCP guide](https://mcp.directory/blog/xcodebuildmcp-complete-guide-2026); [App Store Review Guidelines](https://developer.apple.com/app-store/review/guidelines/); [Apple developer account tiers](https://developer.apple.com/help/account/basics/about-your-developer-account); [Apple and Prepear logo settlement](https://9to5mac.com/2021/02/09/apple-and-prepear-reach-an-agreement-on-pear-shaped-logo-trademark/).

Google: [Gemini models](https://ai.google.dev/gemini-api/docs/models); [structured output](https://ai.google.dev/gemini-api/docs/structured-output); [Gemini 3 guide](https://ai.google.dev/gemini-api/docs/gemini-3); [media resolution](https://ai.google.dev/gemini-api/docs/media-resolution); [pricing](https://ai.google.dev/gemini-api/docs/pricing); [Agent Platform launch](https://cloud.google.com/blog/products/ai-machine-learning/introducing-gemini-enterprise-agent-platform); [Gen AI SDK for Python](https://github.com/googleapis/python-genai); [Cloud Run source deploys](https://docs.cloud.google.com/run/docs/deploying-source-code); [Cloud Run minimum instances](https://docs.cloud.google.com/run/docs/configuring/min-instances); [Material 3 shape](https://m3.material.io/styles/shape/overview-principles).

Data: [Open Food Facts API](https://openfoodfacts.github.io/openfoodfacts-server/api/); [FoodData Central API guide](https://fdc.nal.usda.gov/api-guide).

Health, medications and pets: [FDA food allergies](https://www.fda.gov/food/food-labeling-nutrition/food-allergies); [FDA Nutrition Facts and %DV](https://www.fda.gov/food/nutrition-facts-label/how-understand-and-use-nutrition-facts-label); [CSPI on the voluntary dye phase-out](https://www.cspi.org/cspi-news/fdas-plan-remove-food-dyes-industry-understanding); [NIDDK eating with CKD](https://www.niddk.nih.gov/health-information/kidney-disease/chronic-kidney-disease-ckd/eating-nutrition); [NKF phosphorus](https://www.kidney.org/kidney-topics/phosphorus-and-your-diet); [NEA contact dermatitis](https://nationaleczema.org/types-of-eczema/contact-dermatitis/); [AAD fragrance-free products](https://www.aad.org/public/diseases/eczema/childhood/triggers/friendly-products); [NACDG patch test results](https://pubmed.ncbi.nlm.nih.gov/42817757/); [Washington formaldehyde releaser list](https://lawfilesext.leg.wa.gov/Law/WSR/2025/18/25-18-057.htm); [FDA on grapefruit and drugs](https://www.fda.gov/consumers/consumer-updates/grapefruit-juice-and-some-drugs-dont-mix); [NIH on vitamin K and warfarin](https://ods.od.nih.gov/factsheets/VitaminK-Consumer/); [ASPCA foods to avoid for pets](https://www.aspca.org/pet-care/aspca-poison-control/people-foods-avoid-feeding-your-pets).

Fonts: Google Fonts metadata for [Baloo 2](https://github.com/google/fonts/blob/main/ofl/baloo2/METADATA.pb), [Nunito](https://github.com/google/fonts/blob/main/ofl/nunito/METADATA.pb), [Patrick Hand](https://github.com/google/fonts/blob/main/ofl/patrickhand/METADATA.pb), [Fredoka](https://github.com/google/fonts/blob/main/ofl/fredoka/METADATA.pb).
