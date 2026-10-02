# agent/ : the /v1/ask path as a Google ADK Workflow

`agent/graph.py` builds one ADK 2.x Workflow called **`pear_ask`** (google-adk==2.3.0, addendum 11):

```
START -> read_question --route = intent--> "check"        -> check_product  (pipeline.ask.check)
                                           "alternatives" -> suggest        (pipeline.ask.alternatives)
                                           "unclear"      -> unclear        (pipeline.ask.scan_needed)
```

- `read_question` is the **only** node that calls a model: `pipeline.ask.understand(clients, body)`,
  which makes one Gemini call through `pipeline/gemini.py`. Code picks the route from its `intent`.
  An unknown or missing intent goes to `unclear`.
- The three routed nodes are deterministic code, and each is a thin wrapper over the
  `pipeline.ask` function that the direct engine also calls. That is why `pipeline.ask.answer`
  can fall back to `answer_direct` at any time.
- ADK itself never calls a model and does not need `GOOGLE_API_KEY`. The tests swap
  `google.genai.Client` for a function that raises, and the graph still runs.
- Entry point: `await agent.graph.run_ask(clients, body, uid) -> dict | None`. It returns the
  routed node's AskResponse-shaped dict. An exception raised inside a node reaches the caller
  unchanged (`PearError` too). It returns `None` only if no node produced the workflow output.

## ADK 2.3.0 API, as actually installed (the addendum sample is slightly off)

| Thing | 2.3.0 reality |
|---|---|
| START sentinel | `from google.adk.workflow import START` (`from google.adk import START` fails) |
| Top-level exports | `google.adk` exports only `Agent, Context, Event, Runner, Workflow` |
| Session service | `from google.adk.sessions import InMemorySessionService` |
| Nodes | Plain sync/async functions work. Each is wrapped in a `FunctionNode`. A parameter named `node_input` receives the previous node's output, a `ctx` parameter (optional) receives the Context, and any other parameter is bound from `ctx.state`. No `@node` decorator is needed. |
| Output + route | Return `Event(output=..., route="check")`. A plain return value is wrapped as `Event(output=value)`. |
| Conditional edges | `(read_question, {"check": check_product, ...})` inside `Workflow(edges=[...])` |
| Runner | `Runner(node=workflow, session_service=..., auto_create_session=True)`. `app_name` defaults to `workflow.name`. |
| Run | `runner.run_async(user_id=, session_id=, new_message=types.Content(...))` yields events. The workflow's final output is the event whose `node_info.output_for` contains the root path `pear_ask@1`. |

## How per-request data reaches the nodes (no globals leaking)

The Workflow and Runner are built **once**, on first use or by calling `agent.graph.warm()`.
Importing ADK takes about 0.7 to 1.0 s, and that cost is reported once as `adk.build`. After
that, every request shares the same objects.

`clients` and the original request body travel in a **per-call `ContextVar`** (`_RunScope`).
`run_ask()` sets it before the run and resets it in `finally`. asyncio copies the current
context into every task ADK creates, so each concurrent run sees only its own scope. The test
`test_concurrent_runs_see_only_their_own_clients_and_body` runs 12 requests at once to check
this. We do **not** put clients in ADK session state: `InMemorySessionService` deep-copies
sessions, which would also copy the genai, httpx and Firestore clients.

Each run uses a fresh `session_id` (uuid4). The session is deleted afterwards, so memory stays
flat. The ADK user message is only the question text, and the routed node receives
`{**body, **understood}` as `node_input`.

Because the request `ContextVar`s flow into ADK's tasks the same way, `core.timing.stage()`
calls inside the pipeline functions still appear in the response's `timings_ms`.

## Switching engines

```
PEAR_ASK_ENGINE=adk     # default (core/config.py): pipeline.ask.answer -> agent.graph.run_ask
PEAR_ASK_ENGINE=direct  # pipeline.ask.answer -> answer_direct (same functions, no ADK)
```

`GET /health` shows the active `ask_engine`. If anything in ADK raises, `pipeline.ask.answer`
logs the error and answers through `answer_direct`, so the app never notices.

## Measured overhead

Overhead is the total `run_ask` time minus the time spent inside node functions. It was
measured by `tests/test_agent.py::test_overhead_median_under_50ms` over 20 runs on a MacBook,
with fake pipeline functions and after one warm-up run:

| metric | value |
|---|---|
| median overhead | **0.49 ms** |
| max overhead | 0.63 ms |
| first build (ADK import + graph) | ~0.7 to 1.0 s, once per process |

That is far under the 50 ms budget in addendum 11, so ADK stays the default engine. Call
`agent.graph.warm()` at startup so the first real question does not pay the import cost.

## Run it standalone / debug it

```
cd api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_agent.py -q -s   # no network, no key
```

With real credentials (ADC or `GEMINI_API_KEY`), from a Python shell in `api/`:

```python
import asyncio, json
from core.config import get_settings
from core.clients import build_clients
from agent import graph

async def main():
    clients = await build_clients(get_settings())
    body = {"text": "can grandma have this?", "language": "en", "last_product_key": "off:3017620422003",
            "household": json.load(open("../contracts/household.example.json"))}
    print(await graph.run_ask(clients, body, "dev"))
    print(graph.stats())

asyncio.run(main())
```

- `POST /v1/ask?debug=1` adds `debug.ask_engine = "adk"` and
  `debug.adk = {route, nodes, total_ms, node_ms, overhead_ms, ok, error?, build_ms?}`.
- `timings_ms.adk` is the whole graph run, including the node work.
- Log lines: `adk.build` (once), and `adk.run` per question (with `route`, `nodes`, `total_ms`,
  `node_ms`, `overhead_ms`, `ok`, `error`). Filter on `jsonPayload.event="adk.run"` in Cloud Logging.
- `agent.graph.stats()` returns run, ok, failed and no-output counters, `last_error`, and the
  median and max overhead of the last 100 runs.
- ADK logs unexpected node failures under the `google_adk.*` loggers. For expected
  `PearError`s such as `not_found`, those tracebacks are filtered out.
- `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS` defaults to `false`, so household text stays out of
  ADK's trace spans.
