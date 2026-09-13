"""Minimal Databricks SQL client over the Statement Execution API.

Deliberately stdlib-only. This runs inside a GitHub Actions job and as a tool
call behind an agent, and in both places a dependency that can break the build
costs more than the convenience it buys.

Auth note: Databricks Free Edition has no account console and no account-level
APIs, so a service principal cannot be created. That leaves a workspace
personal access token, held as a GitHub Actions secret. In an enterprise
workspace this would be a service principal with OAuth M2M and the token would
never exist as a long-lived string. The compromise is documented rather than
hidden, because it is the kind of thing worth being asked about.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

API = "/api/2.0/sql/statements"
POLL_SECONDS = 1.5
MAX_POLLS = 40


class DatabricksError(RuntimeError):
    pass


def _warehouse_id(value: str) -> str:
    """Accept either the bare id or the HTTP path the connection panel shows.

    Databricks presents the warehouse as `/sql/1.0/warehouses/<id>` and calls it
    the HTTP path, so that whole string is what people paste. The API wants only
    the last segment, and failing on the more obvious of the two values would be
    a poor trade for one line.
    """
    return value.strip().rstrip("/").rsplit("/", 1)[-1]


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    statement_id: str
    elapsed_ms: int

    def as_records(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    def to_markdown(self, limit: int = 30) -> str:
        """Compact rendering for handing back to an LLM."""
        if not self.rows:
            return "(no rows)"
        head = " | ".join(self.columns)
        sep = " | ".join("---" for _ in self.columns)
        body = "\n".join(
            " | ".join("" if c is None else str(c) for c in row) for row in self.rows[:limit]
        )
        more = "" if len(self.rows) <= limit else f"\n… {len(self.rows) - limit} more rows"
        return f"{head}\n{sep}\n{body}{more}"


class Databricks:
    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        warehouse_id: str | None = None,
    ) -> None:
        self.host = (host or os.environ["DATABRICKS_HOST"]).rstrip("/")
        if not self.host.startswith("https://"):
            self.host = f"https://{self.host}"
        self.token = token or os.environ["DATABRICKS_TOKEN"]
        self.warehouse_id = _warehouse_id(warehouse_id or os.environ["DATABRICKS_WAREHOUSE_ID"])

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.host}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._send(req)

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(
            f"{self.host}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        return self._send(req)

    @staticmethod
    def _send(req: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:600]
            raise DatabricksError(f"HTTP {exc.code} from Databricks: {detail}") from exc
        except urllib.error.URLError as exc:
            raise DatabricksError(f"could not reach Databricks: {exc.reason}") from exc

    def query(self, sql: str, catalog: str | None = None, schema: str | None = None) -> QueryResult:
        started = time.monotonic()
        payload = {
            "statement": sql,
            "warehouse_id": self.warehouse_id,
            "wait_timeout": "30s",
            "on_wait_timeout": "CONTINUE",
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
        }
        if catalog:
            payload["catalog"] = catalog
        if schema:
            payload["schema"] = schema

        body = self._post(API, payload)
        statement_id = body.get("statement_id", "")

        polls = 0
        while body.get("status", {}).get("state") in {"PENDING", "RUNNING"}:
            if polls >= MAX_POLLS:
                raise DatabricksError(f"statement {statement_id} still running after timeout")
            time.sleep(POLL_SECONDS)
            polls += 1
            body = self._get(f"{API}/{statement_id}")

        state = body.get("status", {}).get("state")
        if state != "SUCCEEDED":
            err = body.get("status", {}).get("error", {})
            raise DatabricksError(
                f"{err.get('error_code', state)}: {err.get('message', 'statement failed')}"
            )

        manifest = body.get("manifest", {})
        columns = [c["name"] for c in manifest.get("schema", {}).get("columns", [])]
        rows = body.get("result", {}).get("data_array", []) or []
        return QueryResult(
            columns=columns,
            rows=rows,
            statement_id=statement_id,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
