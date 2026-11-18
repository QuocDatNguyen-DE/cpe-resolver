"""Zugangsdaten aus einer .env-Datei (KEY=VALUE) lesen."""
from __future__ import annotations

import os
from pathlib import Path


def read_env_file(path: Path) -> dict:
    """KEY=VALUE-Zeilen als dict; leere Zeilen und # werden ignoriert, fehlende Datei -> {}."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def get_secret(name: str, env_file: Path) -> tuple[str | None, str]:
    """Wert und Quelle; eine gesetzte Umgebungsvariable hat Vorrang vor der Datei."""
    if os.environ.get(name):
        return os.environ[name], "Umgebungsvariable"
    value = read_env_file(env_file).get(name)
    if value:
        return value, str(env_file)
    return None, "keine"
