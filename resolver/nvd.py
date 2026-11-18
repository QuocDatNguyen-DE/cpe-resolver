"""Minimaler Client für die NVD-CPE-API 2.0 mit Datei-Cache und Rate-Limit."""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

NVD_URL = "https://services.nvd.nist.gov/rest/json/cpes/2.0"
# NVD-Rate-Limit: ohne Key 5 Requests / 30 s, mit Key 50 / 30 s
NVD_INTERVAL_WITH_KEY = 0.7
NVD_INTERVAL_WITHOUT_KEY = 6.5
NVD_RETRY_HTTP_CODES = (403, 429, 500, 502, 503)
NVD_MAX_ATTEMPTS = 4
NVD_RETRY_WAIT = 30


class SearchTooBroad(Exception):
    """Die NVD lehnt eine keywordSearch mit zu vielen Treffern ab (HTTP 404)."""


class NvdClient:
    def __init__(self, cache_dir: Path, api_key: str | None = None):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.min_interval = NVD_INTERVAL_WITH_KEY if api_key else NVD_INTERVAL_WITHOUT_KEY
        self._last_request = 0.0

    def products(self, **params) -> list[dict]:
        """Alle Treffer (paginiert) als Liste von {cpeName, deprecated, title}."""
        cache_file = self._cache_file(params)
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))

        products, start = [], 0
        while True:
            page = self._request({**params, "resultsPerPage": 10000, "startIndex": start})
            products += [self._simplify(p["cpe"]) for p in page.get("products", [])]
            start += page.get("resultsPerPage", 0)
            if start >= page.get("totalResults", 0) or not page.get("resultsPerPage"):
                break

        cache_file.write_text(json.dumps(products), encoding="utf-8")
        return products

    def _cache_file(self, params: dict) -> Path:
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    @staticmethod
    def _simplify(cpe: dict) -> dict:
        title = next((t["title"] for t in cpe.get("titles", []) if t.get("lang") == "en"), "")
        return {"cpeName": cpe["cpeName"], "deprecated": cpe.get("deprecated", False),
                "title": title}

    def _request(self, params: dict) -> dict:
        url = f"{NVD_URL}?{urllib.parse.urlencode(params, safe=':*')}"
        headers = {"User-Agent": "cpe-resolver-poc/0.1"}
        if self.api_key:
            headers["apiKey"] = self.api_key

        for attempt in range(NVD_MAX_ATTEMPTS):
            self._wait_for_rate_limit()
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                is_last_attempt = attempt == NVD_MAX_ATTEMPTS - 1
                if e.code in NVD_RETRY_HTTP_CODES and not is_last_attempt:
                    print(f"  NVD HTTP {e.code}, warte {NVD_RETRY_WAIT} s ...", file=sys.stderr)
                    time.sleep(NVD_RETRY_WAIT)
                    continue
                # die NVD nennt den Grund im Header "message", z. B. "Invalid apiKey."
                reason = e.headers.get("message") or e.reason
                if "too broad" in str(reason).lower():
                    raise SearchTooBroad(reason) from e
                sys.exit(f"NVD-Anfrage fehlgeschlagen: HTTP {e.code} - {reason}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                # Timeout / Verbindungsabbruch: wie HTTP 5xx erneut versuchen
                if attempt == NVD_MAX_ATTEMPTS - 1:
                    sys.exit(f"NVD nicht erreichbar nach {NVD_MAX_ATTEMPTS} Versuchen: {e}")
                print(f"  NVD-Verbindungsfehler ({e}), warte {NVD_RETRY_WAIT} s ...",
                      file=sys.stderr)
                time.sleep(NVD_RETRY_WAIT)
        raise RuntimeError("NVD nicht erreichbar")

    def _wait_for_rate_limit(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()


def active(products: list[dict]) -> list[dict]:
    """Nur nicht veraltete (deprecated) Einträge."""
    return [p for p in products if not p["deprecated"]]
