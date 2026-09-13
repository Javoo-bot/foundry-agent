"""Load the synthetic fleet data into Unity Catalog.

Uploads the generated CSVs to a UC volume over the Files API, then builds
managed Delta tables from them. Scripted rather than clicked through the
upload UI, because the point of the exercise is that the whole pipeline can be
re-run by someone else from an empty workspace.

    python gishub/databricks/load.py

If the Files API is unavailable on Free Edition, fall back to the workspace UI
(Data -> Add -> Upload files to volume) and re-run with --skip-upload; the DDL
below still applies.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import envfile  # noqa: E402

envfile.load()

from databricks.client import Databricks, DatabricksError  # noqa: E402

CATALOG = os.environ.get("GISHUB_CATALOG", "gishub")
SCHEMA = os.environ.get("GISHUB_SCHEMA", "fleet")
VOLUME = "raw"
SEED = Path(__file__).parent.parent / "data" / "seed"

# Explicit schemas. Inferring types from CSV is how a mileage column silently
# becomes a string and every numeric comparison in the agent's SQL starts
# returning nothing.
#
# The columns are declared once and used twice: to create the table, and to
# cast on the way in. CSV has no types, so with inferSchema off every field
# arrives as a string and Delta refuses to merge a string into a DATE column.
# Casting in a subquery keeps the strict schema instead of relaxing it to
# whatever the reader happened to guess.
TABLES: dict[str, list[tuple[str, str]]] = {
    "fleet_customers": [
        ("customer_id", "STRING"), ("customer_name", "STRING"), ("segment", "STRING"),
        ("country", "STRING"), ("contract_start", "DATE"), ("fleet_size_contracted", "INT"),
    ],
    "vehicles": [
        ("vin", "STRING"), ("model", "STRING"), ("model_year", "INT"),
        ("powertrain", "STRING"), ("list_price_eur", "INT"), ("customer_id", "STRING"),
        ("market", "STRING"), ("delivery_date", "DATE"), ("odometer_km", "INT"),
    ],
    "charging_sessions": [
        ("session_id", "STRING"), ("vin", "STRING"), ("started_at", "TIMESTAMP"),
        ("location_type", "STRING"), ("energy_kwh", "DOUBLE"), ("duration_min", "INT"),
        ("cost_eur", "DOUBLE"),
    ],
    "fault_events": [
        ("event_id", "STRING"), ("vin", "STRING"), ("dtc_code", "STRING"),
        ("component", "STRING"), ("severity", "STRING"), ("occurred_at", "DATE"),
        ("odometer_km", "INT"),
    ],
    "warranty_claims": [
        ("claim_id", "STRING"), ("vin", "STRING"), ("event_id", "STRING"),
        ("component", "STRING"), ("opened_at", "DATE"), ("cost_eur", "DOUBLE"),
        ("status", "STRING"), ("market", "STRING"),
    ],
}

COMMENTS = {
    "fleet_customers": "B2B fleet operators holding a contract. Synthetic.",
    "vehicles": "Delivered vehicles and their assignment to a fleet customer. Synthetic.",
    "charging_sessions": "Individual charging events for BEV and PHEV vehicles. Synthetic.",
    "fault_events": "Diagnostic trouble codes raised in the field. Synthetic.",
    "warranty_claims": "Warranty claims, each traceable to the fault event that triggered it. Synthetic.",
}


def upload(db: Databricks, name: str) -> None:
    """PUT one CSV into the UC volume."""
    src = SEED / f"{name}.csv"
    if not src.exists():
        raise SystemExit(f"missing {src} — run: python gishub/data/generate.py")
    target = (
        f"{db.host}/api/2.0/fs/files/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/{name}.csv?overwrite=true"
    )
    req = urllib.request.Request(
        target,
        data=src.read_bytes(),
        headers={
            "Authorization": f"Bearer {db.token}",
            "Content-Type": "application/octet-stream",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
        print(f"  uploaded {name}.csv ({src.stat().st_size / 1e6:.1f} MB)")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise DatabricksError(f"Files API refused {name}.csv: HTTP {exc.code} {detail}") from exc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-upload", action="store_true", help="files already in the volume")
    args = ap.parse_args()

    db = Databricks()

    print(f"provisioning {CATALOG}.{SCHEMA}")
    db.query(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
    db.query(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    db.query(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")

    if not args.skip_upload:
        print("uploading seed files")
        for name in TABLES:
            upload(db, name)

    print("building tables")
    for name, cols in TABLES.items():
        fq = f"{CATALOG}.{SCHEMA}.{name}"
        ddl = ", ".join(f"{c} {t}" for c, t in cols)
        casts = ", ".join(f"cast({c} AS {t}) AS {c}" for c, t in cols)
        db.query(f"DROP TABLE IF EXISTS {fq}")
        db.query(f"CREATE TABLE {fq} ({ddl}) COMMENT '{COMMENTS[name]}'")
        db.query(
            f"COPY INTO {fq} "
            f"FROM (SELECT {casts} "
            f"      FROM '/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/{name}.csv') "
            f"FILEFORMAT = CSV "
            f"FORMAT_OPTIONS ('header' = 'true', 'inferSchema' = 'false') "
            f"COPY_OPTIONS ('mergeSchema' = 'false')"
        )
        n = db.query(f"SELECT count(*) AS n FROM {fq}").rows[0][0]
        print(f"  {name:20s} {int(n):>8,} rows")

    print("\nloaded. sanity check:")
    res = db.query(
        f"""
        SELECT v.model_year, v.powertrain,
               count(DISTINCT v.vin)          AS vehicles,
               round(sum(w.cost_eur), 0)      AS warranty_eur
        FROM {CATALOG}.{SCHEMA}.vehicles v
        LEFT JOIN {CATALOG}.{SCHEMA}.warranty_claims w ON w.vin = v.vin
        GROUP BY v.model_year, v.powertrain
        ORDER BY warranty_eur DESC NULLS LAST
        LIMIT 3
        """
    )
    print(res.to_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
