"""Product keys and barcode canonicalization. Keep every key format in this one file.

    off:<ean13>  Open Food Facts       obf:<ean13>  Open Beauty Facts
    fdc:<gtin>   USDA FoodData Central photo:<12 hex of sha1(brand|name|ingredients)>
"""

from __future__ import annotations

import hashlib
import re

_DIGITS = re.compile(r"\D+")


def digits(code: str) -> str:
    return _DIGITS.sub("", code or "")


def _upce_to_upca(upce: str) -> str | None:
    """Expand an 8-digit UPC-E (number system 0/1) to 12-digit UPC-A. None if not UPC-E."""
    if len(upce) != 8 or upce[0] not in "01":
        return None
    ns, d, check = upce[0], upce[1:7], upce[7]
    last = d[5]
    if last in "012":
        body = d[0:2] + last + "0000" + d[2:5]
    elif last == "3":
        body = d[0:3] + "00000" + d[3:5]
    elif last == "4":
        body = d[0:4] + "00000" + d[4]
    else:
        body = d[0:5] + "0000" + last
    return ns + body + check


def canonical_barcode(code: str) -> str:
    """Canonical form used in keys: EAN-13 for 12/13-digit codes, EAN-8 kept as-is."""
    c = digits(code)
    if len(c) == 12:  # UPC-A -> EAN-13
        return "0" + c
    if len(c) == 14 and c.startswith("0"):  # GTIN-14 with leading 0 -> EAN-13
        return c[1:]
    return c


def barcode_variants(code: str) -> list[str]:
    """Every spelling a database might index this code under, most likely first."""
    c = digits(code)
    out: list[str] = []

    def add(x: str | None) -> None:
        if x and x not in out:
            out.append(x)

    add(canonical_barcode(c))
    add(c)
    if len(c) == 13 and c.startswith("0"):
        add(c[1:])  # EAN-13 -> UPC-A
    if len(c) == 8:
        upca = _upce_to_upca(c)  # VisionKit reports UPC-E as 8 digits
        if upca:
            add("0" + upca)
            add(upca)
    return out


def product_key(source_prefix: str, code: str) -> str:
    return f"{source_prefix}:{canonical_barcode(code)}"


def photo_key(brand: str | None, name: str | None, ingredients: list[str]) -> str:
    raw = "|".join([(brand or "").strip().lower(), (name or "").strip().lower(),
                    ",".join(i.strip().lower() for i in ingredients)])
    return "photo:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def split_key(key: str) -> tuple[str, str]:
    prefix, _, rest = key.partition(":")
    return prefix, rest
