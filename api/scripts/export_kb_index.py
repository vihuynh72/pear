"""Write contracts/kb_index.json: the profile options (allergies, conditions, skin, diets, less_of)
with en/vi/es labels and search synonyms, for the app to bundle for instant chip search.

    cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_kb_index.py

Does NOT write into the iOS app folder; copy contracts/kb_index.json into Pear/ when ready.
Debug: the output prints group counts and the kb version it was built from.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))

from rules.kb import PROFILE_GROUPS, get_kb  # noqa: E402

OUT = API.parent / "contracts" / "kb_index.json"


def _synonyms(opt: dict) -> list[str]:
    out: list[str] = []
    for a in opt.get("aliases", []):
        out.append(a.replace("_", " "))
    for key in ("synonyms", "terms"):
        for lang, items in (opt.get(key) or {}).items():
            if lang == "inci":
                continue
            out.extend(items)
    seen: set[str] = set()
    uniq = []
    for s in out:
        k = s.casefold()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


def build() -> dict:
    kb = get_kb()
    groups: dict[str, list[dict]] = {}
    for g in PROFILE_GROUPS:
        groups[g] = [
            {"id": o["id"], "labels": {lg: (o.get("label") or {}).get(lg, o["id"]) for lg in ("en", "vi", "es")},
             "synonyms": _synonyms(o)}
            for o in kb.options.get(g, [])
        ]
    return {"version": kb.version, "groups": groups,
            "levels": {"strict": {"en": "Strict", "vi": "Nghiêm ngặt", "es": "Estricto"},
                       "sensitive": {"en": "Ingredients only", "vi": "Chỉ thành phần", "es": "Solo ingredientes"}}}


def main() -> None:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    counts = {g: len(v) for g, v in data["groups"].items()}
    print(f"wrote {OUT} kb_version={data['version']} {counts}")


if __name__ == "__main__":
    main()
