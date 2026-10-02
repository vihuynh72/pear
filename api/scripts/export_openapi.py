"""Export the live FastAPI schema to contracts/openapi.json (for the iOS dev and Swift codegen).

    cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py
    ... scripts/export_openapi.py --out /tmp/openapi.json --check   # exit 1 if it would change

It imports main (which imports every router) and calls app.openapi(). A router that fails
to import is skipped by main.py, so its paths are missing from the schema: the script lists
those router errors on stderr so you know the export is partial. Re-run after other lanes land.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("PEAR_STORE", "memory")
os.environ.setdefault("PEAR_TRACE", "0")
os.environ.setdefault("PEAR_LOG_LEVEL", "WARNING")
DEFAULT_OUT = API_DIR.parent / "contracts" / "openapi.json"


def build() -> tuple[dict, dict]:
    import main

    return main.app.openapi(), dict(main.app.state.router_errors)


def main_cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="don't write; exit 1 if the file would change")
    args = ap.parse_args(argv)

    spec, router_errors = build()
    text = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    for name, err in sorted(router_errors.items()):
        print(f"WARNING router {name} not in the schema ({'required' if err['required'] else 'optional'}): "
              f"{err['error'][:200]}", file=sys.stderr)
    paths = sorted(spec.get("paths", {}))
    v1 = [p for p in paths if p.startswith("/v1/")]
    print(f"{len(paths)} paths ({len(v1)} under /v1): {', '.join(v1) or '-'}")

    old = args.out.read_text(encoding="utf-8") if args.out.exists() else None
    if args.check:
        print("openapi.json is up to date" if old == text else "openapi.json would change")
        return 0 if old == text else 1
    if old != text:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(f"{args.out} unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
