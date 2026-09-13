"""Go/no-go check for the whole plan.

The architecture assumes a GitHub Actions runner can reach Databricks Free
Edition over the Statement Execution API with a personal access token. Free
Edition documents no account-level API access, and that restriction is the one
thing that would force a different design, so it gets tested first and on its
own — before any Azure credit starts burning.

    python gishub/databricks/smoke.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import envfile  # noqa: E402

envfile.load()

from databricks.client import Databricks, DatabricksError  # noqa: E402

CHECKS = [
    ("reachable + token accepted", "SELECT 1 AS ok"),
    ("warehouse executes SQL", "SELECT current_version().dbsql_version AS version"),
    ("unity catalog visible", "SHOW CATALOGS"),
]


def main() -> int:
    missing = [
        v
        for v in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID")
        if not os.environ.get(v)
    ]
    if missing:
        print(f"FAIL  missing environment variables: {', '.join(missing)}")
        print("\nSet them from the Databricks workspace:")
        print("  DATABRICKS_HOST          the workspace URL, e.g. dbc-xxxx.cloud.databricks.com")
        print("  DATABRICKS_TOKEN         Settings -> Developer -> Access tokens -> Generate")
        print("  DATABRICKS_WAREHOUSE_ID  SQL Warehouses -> your warehouse -> Connection details")
        return 2

    db = Databricks()
    failed = 0
    for label, sql in CHECKS:
        try:
            res = db.query(sql)
            preview = res.rows[0] if res.rows else "(no rows)"
            print(f"PASS  {label:28s} {res.elapsed_ms:>6} ms   {preview}")
        except DatabricksError as exc:
            failed += 1
            print(f"FAIL  {label:28s} {exc}")

    if failed:
        print(
            "\nAt least one check failed. If the token itself is rejected, Free Edition "
            "is blocking external API access and the data layer has to move to committed "
            "Parquet read in the browser instead. Do not open the Azure account until "
            "this is resolved."
        )
        return 1

    print("\nAll checks passed. The CI runner can drive Databricks; proceed to Azure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
