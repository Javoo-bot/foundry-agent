"""Synthetic B2B fleet data for the GIS:Hub agent demo.

Everything here is invented. No real vehicle, customer or claim is represented,
and nothing derives from a Volkswagen dataset. The shape is plausible for a
commercial fleet data product across the vehicle life cycle: who owns the
vehicle, how it charges, what breaks, and what the warranty costs.

The seed is fixed on purpose. The golden answers used by the evaluation suite
are computed against this exact dataset, so a regenerated dataset must produce
byte-identical output or the evals stop meaning anything.
"""

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20260913
OUT = Path(__file__).parent / "seed"

N_CUSTOMERS = 40
N_VEHICLES = 2_000
HORIZON_START = date(2024, 1, 1)
HORIZON_END = date(2026, 8, 31)

MARKETS = ["ES", "DE", "FR", "IT", "PT", "NL", "PL", "SE", "AT"]
SEGMENTS = ["leasing", "corporate", "rental", "municipal"]
MODELS = [
    # (model, powertrain, list_price_eur)
    ("ID.3", "BEV", 39_000),
    ("ID.4", "BEV", 44_000),
    ("ID.7", "BEV", 56_000),
    ("Passat GTE", "PHEV", 48_000),
    ("Tiguan", "ICE", 36_000),
    ("Caddy Cargo", "ICE", 28_000),
    ("Transporter", "ICE", 42_000),
]
MODEL_YEARS = [2023, 2024, 2025]

# Diagnostic trouble codes, with the component they implicate, a severity, and
# the powertrains the code can physically occur on. An ICE van has no
# high-voltage battery and cannot raise P0AA6; a BEV has no exhaust and cannot
# raise P0420. Getting this wrong is the kind of detail that tells a reader the
# dataset was sampled at random rather than modelled.
DTC = [
    ("P0AA6", "battery_hv", "high", {"BEV", "PHEV"}),
    ("P0A80", "battery_hv", "high", {"BEV", "PHEV"}),
    ("U0111", "battery_mgmt", "medium", {"BEV", "PHEV"}),
    ("P0C73", "charging_port", "medium", {"BEV", "PHEV"}),
    ("B1B01", "hvac", "low", {"BEV", "PHEV", "ICE"}),
    ("C1A12", "brakes", "medium", {"BEV", "PHEV", "ICE"}),
    ("P0420", "emissions", "medium", {"PHEV", "ICE"}),
    ("U1113", "telematics", "low", {"BEV", "PHEV", "ICE"}),
    ("P061B", "powertrain_ctrl", "high", {"BEV", "PHEV", "ICE"}),
]

COMPONENTS = [
    ("battery_hv", 4_200),
    ("battery_mgmt", 1_150),
    ("charging_port", 480),
    ("hvac", 620),
    ("brakes", 390),
    ("emissions", 1_480),
    ("telematics", 260),
    ("powertrain_ctrl", 2_100),
    ("infotainment", 540),
]

LOCATION_TYPES = ["depot", "public_ac", "public_dc", "home"]


def _days(a: date, b: date) -> int:
    return (b - a).days


def _rand_date(rng: random.Random, a: date, b: date) -> date:
    return a + timedelta(days=rng.randint(0, _days(a, b)))


def build(rng: random.Random):
    customers = []
    for i in range(1, N_CUSTOMERS + 1):
        seg = rng.choice(SEGMENTS)
        customers.append(
            {
                "customer_id": f"C{i:03d}",
                "customer_name": f"{rng.choice(['Nord', 'Iber', 'Alpin', 'Mare', 'Volt', 'Rota', 'Terra'])}"
                f"{rng.choice(['Fleet', 'Mobility', 'Logistics', 'Rent', 'Group'])} "
                f"{rng.choice(['SA', 'GmbH', 'BV', 'SL', 'AB'])}",
                "segment": seg,
                "country": rng.choice(MARKETS),
                "contract_start": _rand_date(rng, date(2022, 1, 1), date(2025, 6, 30)).isoformat(),
                "fleet_size_contracted": rng.choice([25, 50, 75, 120, 200, 350]),
            }
        )

    vehicles = []
    for i in range(1, N_VEHICLES + 1):
        model, powertrain, price = rng.choice(MODELS)
        my = rng.choice(MODEL_YEARS)
        cust = rng.choice(customers)
        delivery = _rand_date(rng, date(max(2023, my), 1, 1), date(2026, 3, 31))
        vehicles.append(
            {
                "vin": f"WVW{my}{i:06d}X",
                "model": model,
                "model_year": my,
                "powertrain": powertrain,
                "list_price_eur": price,
                "customer_id": cust["customer_id"],
                "market": cust["country"],
                "delivery_date": delivery.isoformat(),
                "odometer_km": rng.randint(4_000, 145_000),
            }
        )

    # --- the planted signal -------------------------------------------------
    # MY2023 BEVs carry an elevated high-voltage battery failure rate. It is
    # real in the data and recoverable by aggregation, so the agent has an
    # actual finding to report rather than only arithmetic to perform.
    def defect_bias(v) -> float:
        if v["powertrain"] == "BEV" and v["model_year"] == 2023:
            return 3.4
        if v["powertrain"] == "BEV":
            return 1.15
        return 1.0

    sessions = []
    sid = 0
    for v in vehicles:
        if v["powertrain"] == "ICE":
            continue
        delivered = date.fromisoformat(v["delivery_date"])
        n = rng.randint(40, 260)
        for _ in range(n):
            sid += 1
            start = _rand_date(rng, max(delivered, HORIZON_START), HORIZON_END)
            loc = rng.choices(LOCATION_TYPES, weights=[45, 20, 25, 10])[0]
            kwh = round(rng.uniform(6, 78) if loc != "public_dc" else rng.uniform(18, 92), 2)
            dur = int(kwh * (rng.uniform(1.4, 2.6) if loc != "public_dc" else rng.uniform(0.5, 0.9)))
            rate = {"depot": 0.14, "public_ac": 0.39, "public_dc": 0.61, "home": 0.22}[loc]
            sessions.append(
                {
                    "session_id": f"S{sid:07d}",
                    "vin": v["vin"],
                    "started_at": datetime.combine(
                        start, datetime.min.time()
                    ).replace(hour=rng.randint(0, 23), minute=rng.randint(0, 59)).isoformat(sep=" "),
                    "location_type": loc,
                    "energy_kwh": kwh,
                    "duration_min": dur,
                    "cost_eur": round(kwh * rate, 2),
                }
            )

    faults = []
    fid = 0
    for v in vehicles:
        delivered = date.fromisoformat(v["delivery_date"])
        bias = defect_bias(v)
        n = rng.choices([0, 1, 2, 3, 4, 5], weights=[30, 26, 18, 12, 8, 6])[0]
        n = int(round(n * (bias if bias > 1 else 1)))
        eligible = [d for d in DTC if v["powertrain"] in d[3]]
        hv_codes = [d for d in eligible if d[1] == "battery_hv"]
        for _ in range(n):
            fid += 1
            code, comp, sev, _pt = rng.choice(eligible)
            if bias > 2 and hv_codes and rng.random() < 0.55:
                code, comp, sev, _pt = rng.choice(hv_codes)  # the MY2023 campaign
            occurred = _rand_date(rng, max(delivered, HORIZON_START), HORIZON_END)
            faults.append(
                {
                    "event_id": f"F{fid:07d}",
                    "vin": v["vin"],
                    "dtc_code": code,
                    "component": comp,
                    "severity": sev,
                    "occurred_at": occurred.isoformat(),
                    "odometer_km": rng.randint(3_000, v["odometer_km"]),
                }
            )

    claims = []
    cid = 0
    by_vin = {v["vin"]: v for v in vehicles}
    for f in faults:
        v = by_vin[f["vin"]]
        # A fault becomes a warranty claim more often when it is severe.
        p = {"high": 0.46, "medium": 0.19, "low": 0.07}[f["severity"]]
        if rng.random() > p:
            continue
        cid += 1
        base = dict(COMPONENTS)[f["component"]]
        opened = date.fromisoformat(f["occurred_at"]) + timedelta(days=rng.randint(1, 90))
        if opened > HORIZON_END:
            opened = HORIZON_END
        claims.append(
            {
                "claim_id": f"W{cid:06d}",
                "vin": f["vin"],
                "event_id": f["event_id"],
                "component": f["component"],
                "opened_at": opened.isoformat(),
                "cost_eur": round(base * rng.uniform(0.55, 1.7), 2),
                "status": rng.choices(
                    ["closed_paid", "closed_rejected", "open"], weights=[72, 13, 15]
                )[0],
                "market": v["market"],
            }
        )

    return {
        "fleet_customers": customers,
        "vehicles": vehicles,
        "charging_sessions": sessions,
        "fault_events": faults,
        "warranty_claims": claims,
    }


def main() -> None:
    rng = random.Random(SEED)
    tables = build(rng)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        path = OUT / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"{name:20s} {len(rows):>8,} rows  ->  {path.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
