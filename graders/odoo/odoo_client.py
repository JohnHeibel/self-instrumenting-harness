"""Minimal Odoo JSON-2 client (stdlib): POST /json/2/<model>/<method> with a bearer API key.

Used by the grader (auditor key, read-only) and the demand injector (intake key). The agent has its own
key and its own tools; nothing here is shared with it. Keys come from the keys file written by
hosts/odoo/bootstrap.py and are never printed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_KEYS = Path(os.environ.get("SISYPHUS_ODOO_HOME", Path.home() / ".sisyphus" / "odoo")) / "odoo-keys.json"
DEFAULT_URL = "http://127.0.0.1:8069"


class OdooError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def load_keys(path: Path | str = DEFAULT_KEYS) -> dict:
    return json.loads(Path(path).read_text())


class Odoo:
    def __init__(self, key: str, db: str = "odoo", url: str = DEFAULT_URL, timeout: float = 30) -> None:
        self._key, self.db, self.url, self.timeout = key, db, url.rstrip("/"), timeout

    def call(self, model: str, method: str, **kwargs):
        req = urllib.request.Request(
            f"{self.url}/json/2/{model}/{method}", data=json.dumps(kwargs).encode(), method="POST",
            headers={"Authorization": f"Bearer {self._key}", "X-Odoo-Database": self.db,
                     "Content-Type": "application/json; charset=utf-8", "User-Agent": "sisyphus-odoo-grader"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("message", "")
            except (ValueError, OSError):
                msg = ""
            raise OdooError(f"{model}.{method}: HTTP {e.code} {msg}"[:300], e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise OdooError(f"{model}.{method}: {e}") from None

    def search_count(self, model: str, domain: list | None = None) -> int:
        return self.call(model, "search_count", domain=domain or [])

    def search_read(self, model: str, domain: list | None = None, fields: list | None = None, **kw) -> list[dict]:
        return self.call(model, "search_read", domain=domain or [], fields=fields or [], **kw)
