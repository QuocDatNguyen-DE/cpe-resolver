"""CPE-2.3-Formatted-Strings zerlegen, maskieren und bauen."""
from __future__ import annotations

import re

CPE_FIELDS = ["part", "vendor", "product", "version", "update", "edition",
              "language", "sw_edition", "target_sw", "target_hw", "other"]
EXTRA_FIELDS = CPE_FIELDS[4:]  # alles nach version
WILDCARDS = {"*", "-"}


def split_cpe(cpe: str) -> dict:
    """Zerlegt einen CPE-2.3-Formatted-String an nicht maskierten Doppelpunkten."""
    parts, current, escaped = [], [], False
    for ch in cpe:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            current.append(ch)
            escaped = True
        elif ch == ":":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))

    if len(parts) != 13 or parts[0] != "cpe" or parts[1] != "2.3":
        raise ValueError(f"kein gültiger CPE-2.3-Name: {cpe}")
    return dict(zip(CPE_FIELDS, parts[2:]))


def unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def escape(value: str) -> str:
    """Maskiert Sonderzeichen für den Formatted String (z. B. & -> \\&)."""
    return "".join(c if re.match(r"[a-z0-9._\-]", c) else "\\" + c for c in value.lower())


def build_cpe(part: str, vendor: str, product: str, version: str = "*") -> str:
    return ":".join(["cpe", "2.3", part, vendor, product, version] + ["*"] * 7)
