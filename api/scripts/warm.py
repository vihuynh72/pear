"""Warm a running Pear API with the demo products and print a timing table (before judging).

    cd api
    python -m scripts.warm --url https://pear-api-xxxx.us-central1.run.app
    python -m scripts.warm --url http://localhost:8080 --csv ../demo/products.csv --repeat 2

For each barcode in the CSV (rows with a blank barcode are skipped):
  1. POST /v1/scan/barcode with the demo household (fills products/{key}, embedding, scan log,
     and starts the picks prefetch on the server)
  2. POST /v1/picks for that product
  3. POST /v1/prices for the pick keys (warms prices/{key}; best effort, 6 s server cap)
--repeat 2 runs the scans again so the second pass shows cache-hit timings.
Columns: client ms, server total ms, lookup ms, cached?, states (DIFF where they differ from
the CSV's expected_* columns), picks count + ms, prices found + ms.

Debugging: --debug sends ?debug=1 and prints the `debug.cache` / `debug.lookup` blocks;
any non-200 prints the error body. Pass --token for a Firebase ID token when PEAR_AUTH=required.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO / "demo" / "products.csv"
DEFAULT_HOUSEHOLD = REPO / "contracts" / "household.example.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m scripts.warm", description=__doc__.splitlines()[0])
    p.add_argument("--url", required=True, help="API base URL, e.g. https://pear-api-....run.app")
    p.add_argument("--csv", default=str(DEFAULT_CSV), help="demo products CSV (default demo/products.csv)")
    p.add_argument("--household", default=str(DEFAULT_HOUSEHOLD), help="household JSON")
    p.add_argument("--repeat", type=int, default=2, help="scan passes (2 shows cache-hit timings)")
    p.add_argument("--no-picks", action="store_true", help="skip /v1/picks and /v1/prices")
    p.add_argument("--no-prices", action="store_true", help="skip /v1/prices")
    p.add_argument("--token", default=None, help="Firebase ID token (Authorization: Bearer)")
    p.add_argument("--debug", action="store_true", help="send ?debug=1 and print cache/lookup details")
    p.add_argument("--timeout", type=float, default=30.0)
    return p.parse_args(argv)


def load_rows(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("barcode") or "").strip()]


def _states(body: dict[str, Any]) -> dict[str, str]:
    return {v["member_id"]: v["state"] for v in body.get("verdicts") or []}


def _diff(states: dict[str, str], row: dict[str, str]) -> str:
    parts = []
    for k, want in row.items():
        if not k.startswith("expected_") or not want:
            continue
        mid = k.removeprefix("expected_")
        got = states.get(mid)
        short = mid.removeprefix("m_")
        parts.append(f"{short}={got}" if got == want else f"{short}={got}(DIFF want {want})")
    return " ".join(parts) or " ".join(f"{k}={v}" for k, v in states.items())


def _post(http: httpx.Client, url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any], int]:
    t0 = time.perf_counter()
    try:
        r = http.post(url, json=body)
        ms = int((time.perf_counter() - t0) * 1000)
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:300]}
        return r.status_code, data, ms
    except httpx.HTTPError as e:
        return 0, {"error": repr(e)}, int((time.perf_counter() - t0) * 1000)


def warm_one(http: httpx.Client, base: str, row: dict[str, str], household: dict[str, Any],
             args: argparse.Namespace, do_picks: bool) -> dict[str, Any]:
    q = "?debug=1" if args.debug else ""
    code = row["barcode"].strip()
    status, body, ms = _post(http, f"{base}/v1/scan/barcode{q}", {"code": code, "household": household})
    out: dict[str, Any] = {"code": code, "name": (row.get("name") or "")[:34], "status": status, "client_ms": ms}
    if status != 200:
        out["error"] = json.dumps(body.get("error", body))[:200]
        return out
    tm = body.get("timings_ms") or {}
    dbg = body.get("debug") or {}
    out.update(server_ms=tm.get("total"), lookup_ms=tm.get("lookup"), key=body["product"]["key"],
               cached=(dbg.get("cache") or {}).get("hit") if dbg else ("lookup" not in tm),
               states=_diff(_states(body), row))
    if args.debug:
        out["debug"] = {k: dbg.get(k) for k in ("cache", "lookup")}
    if not do_picks:
        return out
    ps, pb, pms = _post(http, f"{base}/v1/picks", {"product_key": out["key"], "household": household})
    items = (pb.get("items") or []) if ps == 200 else []
    out.update(picks=len(items) if ps == 200 else f"err {ps}", picks_ms=pms)
    keys = [it["product"]["key"] for it in items]
    if keys and not args.no_prices:
        rs, rb, rms = _post(http, f"{base}/v1/prices", {"product_keys": keys, "country": "US"})
        prices = (rb.get("prices") or {}) if rs == 200 else {}
        out.update(prices=f"{sum(1 for v in prices.values() if v)}/{len(keys)}" if rs == 200 else f"err {rs}",
                   prices_ms=rms)
    return out


def print_table(rows: list[dict[str, Any]]) -> None:
    cols = ["pass", "code", "name", "status", "client_ms", "server_ms", "lookup_ms", "cached", "picks",
            "picks_ms", "prices", "prices_ms", "states"]
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols if c != "states"}
    print("  ".join(c.ljust(widths.get(c, 0)) for c in cols))
    for r in rows:
        line = "  ".join(str(r.get(c, "")).ljust(widths.get(c, 0)) for c in cols if c != "states")
        print(line + "  " + str(r.get("states") or r.get("error") or ""))
        if r.get("debug"):
            print("      debug: " + json.dumps(r["debug"], ensure_ascii=False)[:600])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base = args.url.rstrip("/")
    rows = load_rows(args.csv)
    household = json.loads(Path(args.household).read_text(encoding="utf-8"))
    if not rows:
        print(f"no barcodes in {args.csv}", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    results: list[dict[str, Any]] = []
    with httpx.Client(http2=True, timeout=args.timeout, headers=headers) as http:
        h = http.get(f"{base}/health")
        print(f"health {h.status_code}: {h.text[:200]}")
        for n in range(1, max(1, args.repeat) + 1):
            for row in rows:
                r = warm_one(http, base, row, household, args, do_picks=(n == 1 and not args.no_picks))
                r["pass"] = n
                results.append(r)
                print(f"  pass {n} {r['code']} -> {r['status']} in {r['client_ms']} ms", flush=True)
    print()
    print_table(results)
    failed = [r for r in results if r["status"] != 200]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
