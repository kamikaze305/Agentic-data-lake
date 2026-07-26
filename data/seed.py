"""Generates the operational half of the data lake: 14 months of shipment records.

Synthetic but shaped like the real thing — seasonal ocean delays, one carrier that
under-performs its on-time target, a customs-hold cluster on a particular HS code,
and `document_amendment_cycles`, the column that makes the Part 2 problem visible
in Part 1's own analytics.

Deterministic (fixed seed) so the demo tells the same story every time it runs.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

SEED = 42
N_SHIPMENTS = 420
START = date(2025, 7, 1)
END = date(2026, 7, 20)

CARRIERS = [
    # code, name, type, on-time target %
    ("MAEU", "Maersk Line", "Ocean", 92),
    ("MSCU", "MSC", "Ocean", 90),
    ("CMDU", "CMA CGM", "Ocean", 89),
    ("HLCU", "Hapag-Lloyd", "Ocean", 91),
    ("ONEY", "Ocean Network Express", "Ocean", 88),
    ("EKAF", "Emirates SkyCargo", "Air", 95),
    ("SQCF", "Singapore Airlines Cargo", "Air", 96),
]

CUSTOMERS = [
    ("ACME", "Acme Industrial Pvt Ltd", "India", "Enterprise", "Machinery"),
    ("NRTX", "Nortex Textiles Ltd", "India", "Mid-Market", "Textiles"),
    ("VLCH", "Valcom Chemicals GmbH", "Germany", "Enterprise", "Chemicals"),
    ("BRGH", "Brightgear Electronics", "Singapore", "Mid-Market", "Electronics"),
    ("SUNP", "Sunpeak Foods BV", "Netherlands", "Mid-Market", "Food & Bev"),
    ("HRZN", "Horizon Auto Parts Inc", "USA", "Enterprise", "Automotive"),
    ("KAVR", "Kaveri Pharma Exports", "India", "SMB", "Pharma"),
]

PORTS = [
    ("INNSA", "Nhava Sheva", "India"),
    ("INMAA", "Chennai", "India"),
    ("INMUN", "Mundra", "India"),
    ("CNSHA", "Shanghai", "China"),
    ("CNNGB", "Ningbo", "China"),
    ("SGSIN", "Singapore", "Singapore"),
    ("AEJEA", "Jebel Ali", "UAE"),
    ("NLRTM", "Rotterdam", "Netherlands"),
    ("DEHAM", "Hamburg", "Germany"),
    ("USLAX", "Los Angeles", "USA"),
    ("USNYC", "New York", "USA"),
    ("GBFXT", "Felixstowe", "UK"),
]

# commodity, HS code, base value per container (USD)
COMMODITIES = [
    ("Cotton knitted fabric", "6006.22", 34000),
    ("Industrial ball bearings", "8482.10", 61000),
    ("Polypropylene granules", "3902.10", 28000),
    ("LED lighting modules", "9405.42", 47000),
    ("Automotive brake pads", "8708.30", 52000),
    ("Basmati rice", "1006.30", 22000),
    ("Pharmaceutical formulations", "3004.90", 88000),
    ("Stainless steel flanges", "7307.21", 39000),
]

INCOTERMS = ["FOB", "CIF", "CFR", "EXW", "DAP"]
CONTAINER_TYPES = ["20GP", "40GP", "40HC", "40RH"]

CREATE_SQL = """
DROP TABLE IF EXISTS shipments;
DROP TABLE IF EXISTS carriers;
DROP TABLE IF EXISTS customers;

CREATE TABLE carriers (
    carrier_code      TEXT PRIMARY KEY,
    carrier_name      TEXT NOT NULL,
    carrier_type      TEXT NOT NULL,
    on_time_target_pct INTEGER NOT NULL
);

CREATE TABLE customers (
    customer_code TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    country       TEXT NOT NULL,
    tier          TEXT NOT NULL,
    industry      TEXT NOT NULL
);

CREATE TABLE shipments (
    shipment_id       TEXT PRIMARY KEY,
    booking_date      TEXT NOT NULL,
    etd               TEXT NOT NULL,
    eta               TEXT NOT NULL,
    actual_arrival    TEXT,
    delay_days        INTEGER,
    mode              TEXT NOT NULL,
    origin_port       TEXT NOT NULL,
    origin_country    TEXT NOT NULL,
    destination_port  TEXT NOT NULL,
    destination_country TEXT NOT NULL,
    carrier_code      TEXT NOT NULL REFERENCES carriers(carrier_code),
    customer_code     TEXT NOT NULL REFERENCES customers(customer_code),
    container_type    TEXT,
    container_count   INTEGER,
    gross_weight_kg   REAL,
    freight_cost_usd  REAL,
    declared_value_usd REAL,
    commodity         TEXT,
    hs_code           TEXT,
    incoterm          TEXT,
    status            TEXT NOT NULL,
    customs_hold      INTEGER NOT NULL DEFAULT 0,
    document_amendment_cycles INTEGER NOT NULL DEFAULT 0,
    bl_number         TEXT,
    invoice_number    TEXT
);

CREATE INDEX idx_shipments_eta ON shipments(eta);
CREATE INDEX idx_shipments_customer ON shipments(customer_code);
"""

# The shipment that the two sample documents in /sample_docs belong to.
# Its gross weight deliberately disagrees with the Commercial Invoice by 240 kg —
# that single mismatch is the thread Part 2 pulls on.
ANCHOR = {
    "shipment_id": "GC-2026-9001",
    "booking_date": "2026-06-18",
    "etd": "2026-06-27",
    "eta": "2026-07-19",
    "actual_arrival": "2026-07-21",
    "delay_days": 2,
    "mode": "Ocean",
    "origin_port": "INNSA",
    "origin_country": "India",
    "destination_port": "NLRTM",
    "destination_country": "Netherlands",
    "carrier_code": "MAEU",
    "customer_code": "SUNP",
    "container_type": "40HC",
    "container_count": 2,
    "gross_weight_kg": 18_960.0,  # invoice says 18,720 kg
    "freight_cost_usd": 4180.0,
    "declared_value_usd": 44_000.0,
    "commodity": "Basmati rice",
    "hs_code": "1006.30",
    "incoterm": "CIF",
    "status": "Delivered",
    "customs_hold": 0,
    "document_amendment_cycles": 3,
    "bl_number": "MAEU778213",
    "invoice_number": "INV-2026-0847",
}


def _random_date(rng: random.Random) -> date:
    return START + timedelta(days=rng.randint(0, (END - START).days))


def _build_rows() -> list[tuple]:
    rng = random.Random(SEED)
    rows: list[tuple] = []

    for i in range(N_SHIPMENTS):
        booking = _random_date(rng)
        carrier_code, _, carrier_type, target = rng.choice(CARRIERS)
        mode = carrier_type
        origin = rng.choice(PORTS[:6])          # exports leave Asia
        dest = rng.choice(PORTS[6:])            # into EU / US / ME
        cust = rng.choice(CUSTOMERS)
        commodity, hs_code, unit_value = rng.choice(COMMODITIES)

        transit = rng.randint(18, 34) if mode == "Ocean" else rng.randint(2, 6)
        etd = booking + timedelta(days=rng.randint(4, 12))
        eta = etd + timedelta(days=transit)

        # Baseline lateness, then the deliberate patterns the demo questions find.
        late_chance = 0.22 if mode == "Ocean" else 0.10
        if carrier_code == "ONEY":                       # the under-performer
            late_chance += 0.18
        if eta.month in (11, 12, 1):                     # peak-season congestion
            late_chance += 0.12
        if dest[0] in ("NLRTM", "DEHAM"):                # EU port congestion
            late_chance += 0.06

        if rng.random() < late_chance:
            delay = rng.randint(1, 14 if mode == "Ocean" else 4)
        else:
            delay = rng.choice([0, 0, 0, -1, -2])

        actual = eta + timedelta(days=delay)
        today = date(2026, 7, 24)
        if actual > today:
            status = "In Transit" if etd <= today else "Booked"
            actual_str, delay_val = None, None
        else:
            status = "Delivered"
            actual_str, delay_val = actual.isoformat(), delay

        # Customs holds cluster on pharma and on incomplete paperwork.
        hold_chance = 0.16 if hs_code == "3004.90" else 0.05
        customs_hold = int(rng.random() < hold_chance)
        if customs_hold and status == "Delivered":
            status = "Customs Hold"

        # Document amendment cycles: the Part 2 pain, measurable in Part 1.
        # Enterprise customers with stricter doc requirements churn more.
        base = {"Enterprise": 1.9, "Mid-Market": 1.3, "SMB": 0.8}[cust[3]]
        cycles = max(0, int(rng.gauss(base + (1.4 if customs_hold else 0), 0.9)))

        containers = rng.randint(1, 4) if mode == "Ocean" else 1
        ctype = rng.choice(CONTAINER_TYPES) if mode == "Ocean" else None
        weight = round(rng.uniform(6_500, 12_500) * containers, 1)
        freight = round(
            (rng.uniform(1400, 2600) if mode == "Ocean" else rng.uniform(5200, 9800))
            * containers,
            2,
        )

        rows.append(
            (
                f"GC-{booking.year}-{i:04d}",
                booking.isoformat(),
                etd.isoformat(),
                eta.isoformat(),
                actual_str,
                delay_val,
                mode,
                origin[0],
                origin[2],
                dest[0],
                dest[2],
                carrier_code,
                cust[0],
                ctype,
                containers,
                weight,
                freight,
                round(unit_value * containers * rng.uniform(0.85, 1.2), 2),
                commodity,
                hs_code,
                rng.choice(INCOTERMS),
                status,
                customs_hold,
                cycles,
                f"{carrier_code}{rng.randint(100000, 999999)}",
                f"INV-{booking.year}-{rng.randint(1000, 9999)}",
            )
        )

    rows.append(tuple(ANCHOR[k] for k in ANCHOR))
    return rows


def seed_operational_data() -> int:
    """(Re)build shipments / carriers / customers. Leaves extracted documents alone."""
    from agents.db import get_conn

    conn = get_conn()
    try:
        conn.executescript(CREATE_SQL)
        conn.executemany("INSERT INTO carriers VALUES (?,?,?,?)", CARRIERS)
        conn.executemany(
            "INSERT INTO customers VALUES (?,?,?,?,?)", CUSTOMERS
        )
        rows = _build_rows()
        conn.executemany(
            "INSERT INTO shipments VALUES (" + ",".join(["?"] * 26) + ")", rows
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agents.db import ensure_db

    ensure_db()
    print(f"Seeded {seed_operational_data()} shipments into data/gocomet.db")
