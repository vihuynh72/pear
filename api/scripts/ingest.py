"""Bulk-load US Open Food Facts products into the product store, with embeddings (addendum 8.5).
The picks lane's vector search (find_nearest, category == food) needs these neighbours.

    cd api
    python -m scripts.ingest --categories en:instant-noodles,en:crackers --per-category 300
    python -m scripts.ingest --categories en:instant-noodles --per-category 50 --dry-run

Needs Application Default Credentials (`gcloud auth application-default login`, project
pear-510410) for Gemini embeddings and Firestore. Without them it stops at once with a clear
message, except with --dry-run (fetch + normalize only, nothing embedded or written).

Politeness: Open Food Facts allows 10 searches/min per IP. Pages are 100 products and the
in-process limiter in pipeline.lookup_off sleeps between pages instead of hammering.

Debugging: every page prints fetched / usable / normalized / embedded / written counts; a
normalizer error prints the product key and the error, then the run continues.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from core.clients import Clients, build_clients, close_clients
from core.config import get_settings
from models.domain import NormalizedProduct, RawProduct
from pipeline import embeddings, lookup_off, product_store, products

PAGE_SIZE = 100
WRITE_CONCURRENCY = 16


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m scripts.ingest",
        description="Pull US products per Open Food Facts category, normalize with the kb, embed with "
                    "Gemini Embedding 2 and write them to Firestore products/{key}. Needs Application "
                    "Default Credentials (gcloud auth application-default login) unless --dry-run.",
    )
    p.add_argument("--categories", required=True,
                   help="comma-separated OFF category tags, e.g. en:instant-noodles,en:crackers")
    p.add_argument("--per-category", type=int, default=300, help="max products per category (default 300)")
    p.add_argument("--country", default=lookup_off.US_COUNTRY_TAG,
                   help="OFF country tag filter (default en:united-states; '' for all)")
    p.add_argument("--batch", type=int, default=50, help="texts per embedding call (default 50)")
    p.add_argument("--dry-run", action="store_true", help="fetch + normalize only; no Gemini, no writes")
    return p.parse_args(argv)


def preflight(clients: Clients, dry_run: bool) -> str | None:
    """Error message when the run can't work, else None."""
    if dry_run:
        return None
    missing = []
    if clients.genai is None:
        missing.append(f"Gemini: {clients.genai_error}")
    if clients.db is None:
        missing.append(f"Firestore: {clients.db_error or 'PEAR_STORE=memory (nothing would persist)'}")
    if not missing:
        return None
    return ("ingest needs Application Default Credentials for Gemini embeddings and Firestore.\n  "
            + "\n  ".join(missing)
            + "\nFix: gcloud auth application-default login (project pear-510410) and unset PEAR_STORE, "
              "or pass --dry-run to only fetch and normalize.")


def _normalize_all(rows: list[RawProduct], totals: dict[str, int]) -> list[tuple[RawProduct, NormalizedProduct]]:
    out = []
    for raw in rows:
        try:
            out.append((raw, products.normalize_raw(raw)))
        except Exception as e:  # noqa: BLE001 - one bad product never stops the run
            totals["normalize_errors"] += 1
            print(f"    normalize failed {raw.key}: {e!r}"[:300])
    return out


async def _write_all(clients: Clients, pairs: list[tuple[RawProduct, NormalizedProduct]],
                     vecs: list[list[float] | None]) -> int:
    sem = asyncio.Semaphore(WRITE_CONCURRENCY)

    async def one(raw: RawProduct, np: NormalizedProduct, vec: list[float] | None) -> bool:
        async with sem:
            return await product_store.put(clients, products.stored_from(raw, np, vec))

    results = await asyncio.gather(*(one(r, n, v) for (r, n), v in zip(pairs, vecs, strict=False)))
    return sum(1 for ok in results if ok)


async def ingest_category(clients: Clients, category: str, args: argparse.Namespace,
                          totals: dict[str, int]) -> None:
    got, page = 0, 1
    while got < args.per_category:
        t0 = time.perf_counter()
        try:
            rows = await lookup_off.search_category(clients, category, page=page, page_size=PAGE_SIZE,
                                                    country_tag=args.country or None)
        except Exception as e:  # noqa: BLE001
            print(f"  {category} page {page}: search failed: {e!r}")
            totals["search_errors"] += 1
            break
        if not rows:
            break
        page_full = len(rows) >= PAGE_SIZE
        rows = rows[: args.per_category - got]
        got += len(rows)
        usable = [r for r in rows if r.name and r.ingredients]
        pairs = _normalize_all(usable, totals)
        totals["fetched"] += len(rows)
        totals["usable"] += len(usable)
        totals["normalized"] += len(pairs)
        written = embedded = 0
        if not args.dry_run and pairs:
            vecs = await embeddings.embed_products(clients, [np for _, np in pairs], batch=args.batch)
            embedded = sum(1 for v in vecs if v)
            written = await _write_all(clients, pairs, vecs)
        totals["embedded"] += embedded
        totals["written"] += written
        print(f"  {category} page {page}: fetched {len(rows)}, usable {len(usable)}, normalized {len(pairs)}, "
              f"embedded {embedded}, written {written} ({time.perf_counter() - t0:.1f}s)")
        if not page_full:
            break
        page += 1


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    clients = await build_clients(get_settings())
    try:
        problem = preflight(clients, args.dry_run)
        if problem:
            print(problem, file=sys.stderr)
            return 2
        totals = dict.fromkeys(("fetched", "usable", "normalized", "embedded", "written",
                                "normalize_errors", "search_errors"), 0)
        t0 = time.perf_counter()
        print(f"ingest: {len(categories)} categories x up to {args.per_category} "
              f"({'dry run' if args.dry_run else clients.store_kind}), country {args.country or 'any'}")
        for cat in categories:
            print(f"- {cat}")
            await ingest_category(clients, cat, args, totals)
        print("totals: " + ", ".join(f"{k} {v}" for k, v in totals.items())
              + f" in {time.perf_counter() - t0:.0f}s")
        return 0 if totals["search_errors"] == 0 else 1
    finally:
        await close_clients(clients)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
