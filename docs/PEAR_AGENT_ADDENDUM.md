# PEAR_AGENT_ADDENDUM.md

Pear agent graph. Addendum to PEAR_MASTER_PLAN.md v0.2.

Friday October 2, 2026, 11:30 AM PT. Owner: Vi.
Update 11:50 AM: Google Cloud console setup done (section 18), price finder built and measured in Agent Designer (section 9). Prices now get a 6 s cap.
Read after sections 0 to 8 of the master plan. Once Vi approves section 1, this file wins wherever the two disagree. Agents still edit only sections 16 and 17 of the master plan.

## 1. Changes Vi approves before lanes touch contracts

| # | Change | Overrides | Priority |
|---|---|---|---|
| 1 | Voice is on-device dictation. Spoken and typed questions both POST text to /v1/ask. Audio upload stays as the fallback for a language the phone can't transcribe | 5.5, 7.1 (voice in), 7.6 (/v1/ask) | P0 |
| 2 | The photo path makes one Gemini call that reads the label and maps each ingredient to kb ids. normalize_unknown leaves the photo path | 7.4, 8.8 | P0 |
| 3 | Picks are prefetched in the background the moment a product is identified | 7.4 | P1 |
| 4 | Pick candidates come from a Firestore vector index (Gemini Embedding 2), food only, ranked by the rules engine | 12.3 item 6 | P1 |
| 5 | Prices on picks through a new /v1/prices, found by Gemini with Grounding with Google Search | Section 3, "no prices" | P2 |
| 6 | The ask path is an ADK 2.0 Workflow agent. Nodes stay plain async functions, so FastAPI can call them directly | New | P1, after Checkpoint 2 |

## 2. The rule that makes it fast

Code routes. Gemini reads. Rules decide.

The input type (barcode, photo, text) already says which path to take, so no model call picks the route. The endpoint split (/v1/scan/barcode, /v1/scan/photo, /v1/ask) is the router. An LLM orchestrator that chooses a sub-agent, waits for it and then writes a final answer spends three or more model round trips before any product data comes back, and that alone eats the 3 second budget. Every path below makes at most one Gemini call before the verdict. Prices are the only open-ended web search, and they never block the verdict or the picks.

Agent Designer (Agent Platform, Studio) is for prototyping the price finder prompt (section 9). An agent deployed from it runs on Agent Runtime, which adds a service hop and a session layer to every call. The app path stays inside the Pear API on Cloud Run.

## 3. Agent graph

```
iPhone
  barcode | photo | dictated or typed text     + household, last_product_key, Firebase ID token
     |
Cloud Run: Pear API (FastAPI, one warm instance, one worker)
     |
  route_input = the endpoint split (code, no model)
     |
     +-- barcode --> cache_get (Firestore products/{key})
     |                 miss: race Open Food Facts v3 and Open Beauty Facts, 1.1 s cap, cache_put in background
     |
     +-- photo ----> label_reader (Gemini 3.5 Flash-Lite, one call: read and map to kb ids)
     |                 front_only: match_by_name (Firestore vector, then Open Food Facts search)
     |
     +-- text -----> question_reader (Gemini 3.5 Flash-Lite, one call: language, intent, product, members)
     |                 route on intent (code):
     |                   check         resolve_product (last_product_key, else vector match)
     |                   alternatives  picks
     |                   unclear       "Scan it" answer, next = scan_needed
     v
  normalize (kb) --> rules engine (L4) --> ScanResult, plus a templated answer for text
     |
     +-- background, starts at identify: prefetch_picks (food only)
           find_nearest 50 (Firestore, category == food) --> rules for every member --> top 3 that fit everyone
           --> price_finder for each pick, in parallel (Gemini + Google Search, 6 s cap, 24 h cache)
```

The only sub-agents are label_reader, question_reader and price_finder. Everything else is code. No sub-agent looks up ingredients on the web: a web summary of an ingredient list is how an allergen gets missed, and "Snap the ingredients" is faster and safer.

## 4. Latency budget

Targets per stage. Before quoting anything, replace them with measured p50 and p95 from timings_ms (7.5).

| Stage | Barcode, cache hit | Barcode, cache miss | Photo | Text question |
|---|---|---|---|---|
| Phone to Cloud Run and back, warm connection | 0.15 to 0.3 s | 0.15 to 0.3 s | 0.15 to 0.3 s | 0.15 to 0.3 s |
| Upload | tiny | tiny | 0.2 to 0.5 s (about 1600 px, JPEG quality 0.6, under 300 KB) | tiny |
| Identify | Firestore get, 0.03 to 0.08 s | Open Food Facts race, 0.4 to 1.1 s | label_reader, 1.2 to 2.0 s | question_reader 0.5 to 0.9 s, resolve 0.05 to 0.4 s |
| Normalize and rules | 0.02 s | 0.02 s | 0.02 s | 0.02 s |
| Target | under 0.5 s | under 1.5 s | under 3 s | under 2 s, then speech starts |

Alternatives: picks are ready 0.3 to 0.8 s after identify because the prefetch starts there, so the row shows with the verdict or right after it, well inside 5 s on every path. Prices are best effort. Measured in Agent Designer Preview with Gemini 3.5 Flash on October 2: 5.6 s for a found price, 3.3 s for NONE. So each price gets 6 s from the moment its pick is chosen, a fresh price lands roughly 6 to 8 s after the scan, and a cached price shows with the picks. Warm the cache for every demo pick before judging.

Photo levers if the measured p50 is over 3 s, in order: fill ingredient "en" only for labels outside English, Vietnamese and Spanish; drop schema fields the rules don't read; cap max_output_tokens at 1024; confirm Cloud Run runs in the Firestore region (section 13); last resort, a two-phase verdict (on-device OCR text through the rules engine gives a "Less sure" verdict first, the label_reader result replaces it).

## 5. Speed rules in code (L3)

1. One client per process. Build genai.Client, firestore.AsyncClient and one httpx.AsyncClient(http2=True) (pip install "httpx[http2]") in the FastAPI lifespan and reuse them. Each new TLS connection costs 100 to 300 ms.
2. Every network call has a timeout: Open Food Facts 1.1 s, label_reader 4 s, question_reader 2.5 s, price_finder 6 s.
8. Gemini 3 models are served only from the global location (Agent Designer's exported code pins location="global"). Build the genai client with location "global".
3. Open Food Facts requests pass fields= with only what the normalizer reads. Full product records are large.
4. Cache writes, scan logs and embeddings of new products run as background tasks after the response.
5. Every Gemini call: thinking_level minimal, JSON schema output (except price_finder, section 9), and no temperature, top_p or top_k (Google's Gemini 3.5 guidance).
6. The app calls GET /health on launch and when the camera opens. That warms the instance and the phone's TLS connection.
7. One span per stage in Cloud Trace (section 14), so the latency slide is a real request waterfall.

## 6. Contract changes to 7.6

POST /v1/ask with a JSON body (new, primary). The multipart audio form stays valid as the fallback.

```json
{"text": "Bà ăn mì này được không?", "language": "vi", "last_product_key": "off:0123456789012", "household": {"household_id": "hh_demo", "members": []}}
```

Response: the 7.6 fields plus answer. intent is check, alternatives or unclear.

```json
{"transcript": "Bà ăn mì này được không?", "language": "vi", "intent": "check", "product_query": "mì", "member_ids": ["m_ba"], "next": "result", "answer": {"text": "...", "language": "vi"}, "scan_result": {}}
```

POST /v1/picks: same input as 7.6 (product_key, household). Response:

```json
{"items": [{"product": {"key": "off:0987654321098", "name": "...", "brand": "...", "image_url": "https://...", "category": "food"}, "verdicts": [{"member_id": "m_mia", "state": "RIPE", "label": "Ripe"}]}], "prefetched": true, "timings_ms": {"wait": 40, "total": 60}}
```

POST /v1/prices (P2, new). Input:

```json
{"product_keys": ["off:0987654321098", "off:0222222222222"], "country": "US"}
```

Output:

```json
{"prices": {"off:0987654321098": {"amount": 3.99, "currency": "USD", "retailer": "Target", "size": "8 oz", "source_title": "target.com", "source_url": "https://...", "checked_at": "2026-10-02T21:20:00Z", "source": "google_search"}, "off:0222222222222": null}, "timings_ms": {"total": 2100}}
```

It waits up to 6 s for lookups already in flight and returns null for the rest. App copy: "About $3.99 at Target. Found with Google Search."

Photo results get the key photo:<first 12 hex of sha1(brand, name, ingredients)> and are cached in products like any product. Text only, never the image.

## 7. Picks: prefetch, candidates, ranking (L3, with L4's engine)

Prefetch sketch (api/pipeline/picks.py):

```python
import asyncio, hashlib, json

INFLIGHT: dict[str, asyncio.Task] = {}   # holds task references; trim to the newest 200

def hh_hash(household: dict) -> str:
    return hashlib.sha1(json.dumps(household["members"], sort_keys=True).encode()).hexdigest()[:12]

def start_prefetch(product: dict, household: dict) -> None:
    """Call right after identify. Returns at once."""
    if product.get("category") != "food":
        return
    key = f'{product["key"]}|{hh_hash(household)}'
    if key not in INFLIGHT:
        INFLIGHT[key] = asyncio.create_task(compute(product, household))

async def compute(product: dict, household: dict) -> dict:
    items = await top3(product, household)      # find_nearest, evaluate_many, ranking (below)
    for it in items:
        prices.start(it["product"])             # P2: price_finder in the background, cached
    return {"items": items}

async def get(product_key: str, household: dict) -> dict:
    key = f"{product_key}|{hh_hash(household)}"
    task = INFLIGHT.get(key)
    if task is None:                            # cold path: load the product and compute now
        product = await load_product(product_key)
        task = INFLIGHT[key] = asyncio.create_task(compute(product, household))
    return await asyncio.wait_for(asyncio.shield(task), timeout=2.5)
```

Candidate query:

```python
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

vq = (db.collection("products")
        .where(filter=FieldFilter("category", "==", "food"))
        .find_nearest(vector_field="embedding", query_vector=Vector(vec),
                      distance_measure=DistanceMeasure.COSINE, limit=50,
                      distance_result_field="dist"))
docs = await vq.get()
```

Ranking: drop the scanned product and anything ALLERGY, UNSURE, DIET or SKIP for any member. Sort the rest by same category_main first, then everyone RIPE before any EASY, then distance. Return the top 3. Evaluate with stored kb_ids (L4 evaluate_many); measure it, the target is under 100 ms for 50 candidates and five members.

If the vector index isn't ready, query where category_main equals the scanned product's, limit 60, and rank the same way. That's the cut-order fallback.

## 8. Firestore additions

1. products/{key} gains embedding (Vector, 768 dimensions; Firestore caps vectors at 2048), category_main (the most specific Open Food Facts category tag, such as en:instant-noodles) and kb_ids (normalizer output, so picks skip normalization). category (food, cosmetic, supplement) is already in the record.
2. prices/{key}: amount, currency, retailer, size, source_title, source_url, search_entry_html, checked_at. Fresh for 24 hours.
3. Embedding text: "{brand} {name}. {category_main}. {first 8 ingredients}". Model gemini-embedding-2 (the ID on the Agent Platform model page; the March preview was gemini-embedding-2-preview; gemini-embedding-001 is the fallback), output_dimensionality 768, the same settings for stored products and queries. COSINE distance, so vector length doesn't matter.
4. Vector index with the category pre-filter. Firestore needs a composite vector index for where() plus find_nearest. Already created in the console at 11:42 AM (section 18). The gcloud equivalent, for reference:

```
gcloud firestore indexes composite create \
  --collection-group=products \
  --query-scope=COLLECTION \
  --field-config=order=ASCENDING,field-path=category \
  --field-config=field-path=embedding,vector-config='{"dimension":"768","flat":"{}"}'
```

5. Ingest, about 30 minutes, once after Checkpoint 1: for each demo food's category_main, pull up to 300 US products from Open Food Facts search (page_size 100, at most 10 searches a minute), run L4's normalizer, embed, write. Every product scanned later gets embedded in the background too.

## 9. Price finder (P2)

Why it works this way:

1. Google has no API that returns retail prices for any product. Merchant API price data covers only products in your own Merchant Center account.
2. Open Prices (Open Food Facts) returned zero records for a Coca-Cola UPC (049000028911) when checked on October 2. Skip it for a US demo.
3. Grounding with Google Search is the Google path. On gemini-3.5-flash, asking for JSON output has been reported to switch grounding off silently (google-gemini/cookbook issue 1274). So price_finder asks for plain lines, parses them, and throws away any answer without grounding chunks. A price with no search result behind it never reaches the screen.

Prototype: done. pear_price_finder exists in pear-510410 (Agent Platform, Studio, Agents; region us-west1; Gemini 3.5 Flash; Google Search tool only). Agent Designer offers no Flash-Lite. The Get Code export is pear_price_finder_agent.py. Don't Deploy it for the app.

Preview results, October 2, 11:45 AM (times measured from send to reply, including Agent Designer overhead):

| Test input | Reply | Time |
|---|---|---|
| Simple Mills Almond Flour Crackers, Fine Ground Sea Salt, 4.25 oz | PRICE, Target 4.99 and Walmart 4.68 | 4 to 8 s (not timed precisely) |
| Nissin Top Ramen Chicken, 3 oz | PRICE, Target 0.39 and Albertsons 0.49 | under 9 s |
| Made-up product (Pear Test Foods Imaginary Sunflower Crisps) | NONE | 3.3 s |
| Triscuit Original Whole Grain Wheat Crackers, 8.5 oz | PRICE, Walmart 3.97 and Target 3.99 | 5.6 s |

The line format held every time and the made-up product got NONE instead of an invented price. The prices look plausible but weren't checked against the stores. Test gemini-3.5-flash-lite from code for speed; Agent Designer can't.

Prompt (PRICE_PROMPT):

```
Find the current US retail price for this exact product and package size.
Product: {brand} {name}, {size}. UPC {code}.
Search US grocery, drugstore and big-box retailers.
Reply with at most 2 lines, each exactly:
PRICE|<retailer>|<price in USD, digits and a dot only>|<package size>
If you can't find this exact product and size, reply exactly: NONE
```

Code (api/pipeline/prices.py):

```python
import asyncio, logging, re
from google.genai import types

log = logging.getLogger("pear.prices")
PRICE_MODEL = "gemini-3.5-flash"   # same model as the prototype; try gemini-3.5-flash-lite for speed
LINE = re.compile(r"^PRICE\|([^|]+)\|\$?(\d+(?:\.\d{1,2})?)\|(.+)$")

async def find_price(client, p: dict, timeout_s: float = 6.0) -> dict | None:
    prompt = PRICE_PROMPT.format(brand=p.get("brand", ""), name=p["name"],
                                 size=p.get("quantity", ""), code=p["code"])
    cfg = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        max_output_tokens=512,
    )
    try:
        r = await asyncio.wait_for(
            client.aio.models.generate_content(model=PRICE_MODEL, contents=prompt, config=cfg),
            timeout=timeout_s)
    except Exception as e:                     # timeout, quota, network
        log.warning("price_finder %s: %r", p["code"], e)
        return None
    c = r.candidates[0] if r.candidates else None
    gm = c.grounding_metadata if c else None
    chunks = [ch for ch in (gm.grounding_chunks or []) if ch.web] if gm else []
    if not chunks:                             # not grounded: never show it
        return None
    for line in (r.text or "").splitlines():
        m = LINE.match(line.strip())
        if m and 0.25 <= float(m.group(2)) <= 100:
            return {"amount": float(m.group(2)), "currency": "USD",
                    "retailer": m.group(1).strip(), "size": m.group(3).strip(),
                    "source_title": chunks[0].web.title, "source_url": chunks[0].web.uri,
                    "search_entry_html": (gm.search_entry_point.rendered_content
                                          if gm.search_entry_point else None),
                    "source": "google_search"}
    return None

PRICE_INFLIGHT: dict[str, asyncio.Task] = {}

def start(p: dict) -> None:
    if p["key"] not in PRICE_INFLIGHT:
        PRICE_INFLIGHT[p["key"]] = asyncio.create_task(cached_price(p))   # prices/{key}, 24 h, then find_price

async def for_keys(keys: list[str]) -> dict:
    for k in keys:
        if k not in PRICE_INFLIGHT:
            start(await load_product(k))
    tasks = {k: PRICE_INFLIGHT[k] for k in keys}
    if tasks:
        await asyncio.wait(tasks.values(), timeout=6.0)
    return {k: (t.result() if t.done() and not t.cancelled() and t.exception() is None else None)
            for k, t in tasks.items()}
```

cached_price reads prices/{key} and returns a fresh hit at once; otherwise it calls find_price and writes a success back. Before judging, call /v1/prices for every demo pick so the cache is warm.

Google's grounding terms require showing the Search Suggestions that come back with grounded results. Show search_entry_html in a small WKWebView under the prices row.

## 10. label_reader output (replaces read_label in 8.8)

One call reads and maps. Gemini fills fields in schema order, so legibility and the allergen statements come before the ingredient list.

```python
LABEL_SCHEMA = {
  "type": "object",
  "properties": {
    "legibility": {"type": "string", "enum": ["full", "partial", "unreadable"]},
    "photo_kind": {"type": "string", "enum": ["ingredients", "front_only", "other"]},
    "product_name": {"type": "string"},
    "brand": {"type": "string"},
    "category": {"type": "string", "enum": ["food", "cosmetic", "supplement", "other"]},
    "product_type": {"type": "string", "enum": ["leave_on", "rinse_off", "none"]},
    "label_language": {"type": "string"},
    "contains_statement": {"type": "string"},
    "may_contain_statement": {"type": "string"},
    "ingredients": {"type": "array", "items": {"type": "object", "properties": {
        "text": {"type": "string"},
        "en": {"type": "string"},
        "kb_ids": {"type": "array", "items": {"type": "string"}}},
      "required": ["text", "kb_ids"]}},
    "certifications": {"type": "array", "items": {"type": "string"}},
    "nutrition": {"type": "object", "properties": {
        "sodium_mg": {"type": "number"}, "sodium_dv": {"type": "number"},
        "added_sugars_g": {"type": "number"}, "added_sugars_dv": {"type": "number"},
        "sat_fat_g": {"type": "number"}, "sat_fat_dv": {"type": "number"}}}
  },
  "required": ["legibility", "photo_kind", "label_language", "ingredients"]
}
```

Prompt rules: copy each ingredient exactly as printed, in printed order. Never invent missing text; if the list is cut off, legibility is partial. Fill "en" only when the label language isn't English, Vietnamese or Spanish. kb_ids come only from the allowed list (kb_ids.txt from L4, pasted into the system instruction); use [] when none fit. Pass the schema as response_json_schema (or response_schema, whichever the installed google-genai accepts) with response_mime_type application/json, thinking_level minimal, max_output_tokens 1024.

L4 merge rule: allergen ids are the union of the kb synonym match and the model's kb_ids, so the model can only add allergen flags, never remove one. Other ids from the model count only for strings the kb didn't match. Gemini still never decides a state (8.7).

## 11. ADK 2.0 ask agent (P1, after Checkpoint 2, 30-minute timebox)

What it buys: the ask path becomes a real agent graph in Google's Agent Development Kit, with one model node and deterministic routes, which is how Google's own ADK 2 codelab says to build it ("Predictable work stays as functions"). What it doesn't buy: speed. If it fights you past the timebox, FastAPI keeps calling the same functions and the app never notices.

Pin google-adk==2.3.0 (the version in Google's ADK 2 codelab). API from adk.dev/graphs and adk.dev/graphs/routes. If your installed version rejects plain async functions as nodes, use the @node decorator with (ctx, node_input), as the codelab's dynamic workflow does.

```python
# api/agent/graph.py
import json, uuid
from google.adk import Workflow, Event, Runner, InMemorySessionService, START
from api.pipeline import ask, picks   # the same async functions FastAPI calls

def _body(node_input):
    if isinstance(node_input, dict):
        return node_input
    text = node_input if isinstance(node_input, str) else node_input.parts[0].text
    return json.loads(text)

async def read_question(node_input):            # model node: Gemini 3.5 Flash-Lite, one call
    body = _body(node_input)
    q = await ask.understand(body)              # language, intent, product_query, member_ids
    return Event(output={**body, **q}, route=q["intent"])

async def check_product(node_input):            # code: resolve, rules, templated answer
    return Event(output=await ask.check(node_input))

async def suggest(node_input):                  # code: the /v1/picks function
    return Event(output=await picks.for_question(node_input))

def unclear(node_input):                        # code: next = "scan_needed"
    return Event(output=ask.scan_needed(node_input))

ask_agent = Workflow(
    name="pear_ask",
    edges=[
        (START, read_question),
        (read_question, {"check": check_product, "alternatives": suggest, "unclear": unclear}),
    ],
)

runner = Runner(node=ask_agent, session_service=InMemorySessionService(), auto_create_session=True)

async def run_ask(body: dict, user_id: str) -> dict | None:
    out = None
    async for ev in runner.run_async(user_id=user_id, session_id=uuid.uuid4().hex,
                                     new_message=json.dumps(body)):
        if getattr(ev, "output", None) is not None:
            out = ev.output
    return out
```

read_question calls Gemini through the shared client, so thinking level and schema stay under your control. An ADK Agent node (mode="single_turn", output_schema=...) also works if you prefer it. The scan paths can join the same Workflow (a router node with barcode, photo and text routes) if time allows. Measure first and keep the direct call if the Workflow adds more than 50 ms.

## 12. iOS changes (L2, plus one L1 item)

1. Ask: hold the mic to dictate with SFSpeechRecognizer. Partial results stream while the button is held, so the text is ready on release. The locale comes from the asker's first language; a small EN, VI, ES pill switches it. On first run, check that SFSpeechRecognizer.supportedLocales() has vi-VN and es-US on Vi's phone; for a missing locale, record m4a and use the multipart fallback.
2. The same screen has a text field. Typed and dictated text send the JSON body in section 6, always with last_product_key, so "this" resolves without a search.
3. Speak answer.text with AVSpeechSynthesizer in answer.language.
4. Result sheet: call /v1/picks as soon as the ScanResult arrives, then /v1/prices with the three keys. Prices fill in place. Nothing covers the verdict.
5. GET /health on launch and when the camera opens.
6. L1: add NSSpeechRecognitionUsageDescription to project.yml next to the camera and microphone strings.

## 13. Cloud Run settings for judging

```
gcloud run deploy pear-api --source . --project pear-510410 \
  --region us-central1 \
  --service-account pear-api@pear-510410.iam.gserviceaccount.com \
  --min-instances 1 --max-instances 1 \
  --no-cpu-throttling --cpu-boost --cpu 2 --memory 1Gi
```

The service account and its roles come from section 18 item 7. Run that first or the deploy and the Gemini and Firestore calls fail.

1. Region: same as Firestore. The database is in nam5, which means us-central1. Cloud Run talks to Firestore several times per request.
2. --no-cpu-throttling keeps the CPU on after the response is sent. Prefetch, cache writes and price lookups run then; with throttling on they stall until the next request arrives.
3. One instance and one uvicorn worker (web: uvicorn main:app --host 0.0.0.0 --port $PORT). The prefetch for a scan and the /v1/picks call that follows must meet in the same process memory. More workers or instances split it.
4. Raise max instances after Friday.

## 14. Google products added to 7.3

| Google product | Job | Priority |
|---|---|---|
| Gemini Embedding 2 | Product vectors for name matching and picks | P1 |
| Firestore vector search | find_nearest with the category pre-filter | P1 |
| Agent Development Kit 2.0 | The ask agent (Workflow, deterministic routes) | P1 |
| Cloud Trace | One span per stage, the request waterfall for the latency slide | P1 |
| Grounding with Google Search | price_finder | P2 |
| Agent Platform Studio, Agent Designer | price_finder prototype | P2 |

Cloud Trace setup (pip install opentelemetry-sdk opentelemetry-exporter-gcp-trace opentelemetry-instrumentation-fastapi; the service account needs roles/cloudtrace.agent):

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter()))
trace.set_tracer_provider(provider)
FastAPIInstrumentor.instrument_app(app)
tracer = trace.get_tracer("pear")
# each stage: with tracer.start_as_current_span("label_reader"): ...
```

In the pitch, count only what got built.

## 15. Lane deltas

| Lane | Add | When |
|---|---|---|
| L1 | Speech recognition usage string; EN, VI, ES pill | 12:30 to 1:45 |
| L2 | Section 12 | Dictation in the 1:45 voice slot; picks and prices row in the P1 slot |
| L3 | Section 5 now. Vector index, ingest, prefetch and /v1/picks after Checkpoint 1. /v1/ask JSON in the 1:45 voice slot. price_finder and /v1/prices only if green at 3:00. ADK ask agent after Checkpoint 2. Section 13 before judging | As listed |
| L4 | label_reader merge rule (section 10); evaluate_many(products, household) on stored kb_ids; export kb_ids.txt for the prompt | 12:30 to 1:45 |
| Vi | Approve section 1. Run the IAM commands in section 18 item 7 before L3's first deploy. Firebase setup (section 18 item 9). Warm demo prices at 4:30 | Now |

## 16. Cut order (replaces the picks part of section 4)

Cut first: prices (price_finder, /v1/prices). Then the ADK ask agent (FastAPI calls the functions directly). Then the vector index (category_main filter only). Then picks. Never cut the three inputs or the allergen tests.

## 17. Sources

ADK: [ADK 2.0 GA](https://adk.dev/2.0/); [graph workflows](https://adk.dev/graphs/); [graph routes, fan-out and JoinNode](https://adk.dev/graphs/routes/); [ADK 2 codelab](https://codelabs.developers.google.com/adk2/instructions).

Agent Designer: [Agent Platform no-code agent codelab](https://codelabs.developers.google.com/create-no-code-agent-using-agent-platform-agent-builder); [Get Code export and MCP tools](https://medium.com/google-cloud/build-no-code-agents-in-gemini-enterprise-agent-platform-34b207ebdf00).

Gemini: [structured output with built-in tools](https://ai.google.dev/gemini-api/docs/structured-output); [grounding dropped with JSON output on gemini-3.5-flash](https://github.com/google-gemini/cookbook/issues/1274); [Gemini 3.5 thinking levels and sampling guidance](https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.5); [Grounding with Google Search on Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/grounding/grounding-with-google-search); [Gemini Embedding 2](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-embedding-2/); [Gemini Embedding 2 on Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/embedding-2).

Data: [Firestore vector search limits and pre-filtering](https://docs.cloud.google.com/firestore/native/docs/vector-search); [Open Prices check for UPC 049000028911](https://prices.openfoodfacts.org/api/v1/prices?product_code=049000028911); [Merchant price insights](https://developers.google.com/shopping-content/guides/reports/price-insights).

Cloud Run: [source deploys and the Cloud Run Builder role](https://docs.cloud.google.com/run/docs/deploying-source-code).

## 18. Console setup status (October 2, 11:30 to 11:50 AM)

Done in the Google Cloud console:

1. Project: pear (pear-510410), organization vihuynh27-work-org, project number 73536482701.
2. Billing: moved from My Billing Account (free trial) to the [2026-10-02] GDP Credit billing account, so usage comes off the event credits.
3. APIs on: Agent Platform, Cloud Run Admin, Cloud Build, Artifact Registry, Secret Manager, Cloud Firestore, Cloud Trace, Cloud Logging, Cloud Monitoring, Cloud Text-to-Speech and Cloud Storage, plus the set Agent Platform Studio turns on by itself (Agent Identity, Agent Registry, Compute Engine, IAM and others).
4. Firestore: database (default), Standard edition (Native mode), nam5, restrictive security rules. The phone never talks to Firestore directly; Cloud Run's service account isn't bound by the rules.
5. Vector index: products, category ascending plus embedding (768 dimensions, flat), collection scope. Ready (green check) at 11:52 AM.
6. Agent: pear_price_finder in Agent Platform Studio, tested (section 9).

Not done:

7. IAM, on purpose: I don't change IAM. This organization gives the Compute Engine default service account no roles (it has no row on the IAM page), so source deploys and Cloud Run's calls to Gemini and Firestore fail until Vi runs this from the laptop or Cloud Shell:

```
PROJECT=pear-510410
gcloud iam service-accounts create pear-api --project $PROJECT --display-name "Pear API runtime"
SA=pear-api@$PROJECT.iam.gserviceaccount.com
for ROLE in roles/aiplatform.user roles/datastore.user roles/cloudtrace.agent roles/logging.logWriter roles/monitoring.metricWriter roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$SA --role $ROLE --condition None
done
gcloud projects add-iam-policy-binding $PROJECT \
  --member serviceAccount:73536482701-compute@developer.gserviceaccount.com \
  --role roles/run.builder --condition None
```

The last command is Google's documented fix for source deploys when the organization blocks automatic grants to default service accounts. Then deploy with the section 13 command.

8. The agent's Manage permission dialog in Preview still lists five viewer roles as missing (Storage Object Viewer, Agent Registry API Viewer, MCP Tool User, IAM Connector User, Agent Platform Viewer). Preview with Google Search works without them. Grant them only if you add MCP or data store tools.
9. Firebase: adding Firebase to pear, turning on Google sign-in and registering the iOS app (bundle id, GoogleService-Info.plist) need Vi to accept the Firebase terms and pick the bundle id.
10. The brownie project still holds an untitled Agent Designer draft from 11:15. It costs nothing; delete it whenever.
