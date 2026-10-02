"""Write api/kb/kb_ids.txt from KB.prompt_ids_text(): one "id: english label" line per matchable
kb id. The label_reader system prompt pastes this list (addendum 10: kb_ids only from it).

    cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_kb_ids.py

Re-run after editing kb/*.json. Debug: it prints the number of ids and the kb version.
"""

from __future__ import annotations

import sys
from pathlib import Path

API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))

from rules.kb import get_kb  # noqa: E402

OUT = API / "kb" / "kb_ids.txt"


def main() -> None:
    kb = get_kb()
    text = kb.prompt_ids_text()
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {OUT} ids={len(kb.ids())} kb_version={kb.version}")


if __name__ == "__main__":
    main()
