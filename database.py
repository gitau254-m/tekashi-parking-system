"""
database.py - The persistent layer of the system (design doc, Section 8).

This file:
  1. opens the connection to the SQLite database file,
  2. creates the five tables from the design doc (Section 8.4) if they do
     not exist yet,
  3. copies the default settings and rates into the database on first run,
  4. offers small helper functions to read settings and rate tiers.

The in-memory data structures (array, heap, hash table, queue...) are built
FROM this database on start-up - that part comes in the next step.

Run this file directly to create and inspect the database:
    python database.py
"""

import sqlite3

from config import DATABASE_PATH, DEFAULT_RATE_TIERS, DEFAULT_SETTINGS

# --------------------------------------------------------------------------
# Schema (design doc, Section 8.4)
# --------------------------------------------------------------------------
# "IF NOT EXISTS" makes this safe to run on every start-up: the first time it
# creates the tables, every time after that it does nothing, and existing data
# is never touched.
#
# Storage choices:
#   - Timestamps are ISO 8601 TEXT with timezone, e.g. 2026-09-24T14:05:00+03:00
#     (SQLite has no date type).
#   - Money is INTEGER whole shillings - never floating-point.
#   - Booleans are INTEGER 0 / 1 (SQLite has no boolean type).
SCHEMA = """
-- Configuration values (capacity, grace period, VAT rate)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Parking rates, editable by management.
-- Note: SQLite allows several NULLs in a UNIQUE column, so "exactly one
-- open-ended tier" is enforced in code (load_rate_table, Module 3).
CREATE TABLE IF NOT EXISTS rate_tiers (
    tier_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    max_minutes INTEGER UNIQUE,                    -- NULL = open-ended final tier
    fee_kes     INTEGER NOT NULL CHECK (fee_kes >= 0)
);

-- Vehicles currently inside the lot (mirrors the hash table vehicle_records)
CREATE TABLE IF NOT EXISTS active_vehicles (
    plate             TEXT PRIMARY KEY,
    slot_index        INTEGER NOT NULL UNIQUE CHECK (slot_index >= 0),
    entry_time        TEXT NOT NULL,
    checkout_time     TEXT,
    duration_minutes  INTEGER,
    fee_kes           INTEGER,
    amount_paid_kes   INTEGER NOT NULL DEFAULT 0,
    paid              INTEGER NOT NULL DEFAULT 0 CHECK (paid IN (0, 1)),
    payment_method    TEXT,
    payment_reference TEXT,
    paid_at           TEXT
);

-- Vehicles waiting at the gate while the lot is full (mirrors the queue)
CREATE TABLE IF NOT EXISTS waiting_queue (
    queue_id  INTEGER PRIMARY KEY AUTOINCREMENT,   -- increasing id keeps arrival order
    plate     TEXT NOT NULL UNIQUE,
    queued_at TEXT NOT NULL
);

-- Append-only audit log of completed stays (mirrors the audit_log list).
-- The application only ever INSERTs here - never UPDATE or DELETE.
CREATE TABLE IF NOT EXISTS transactions (
    txn_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plate             TEXT NOT NULL,
    slot_index        INTEGER NOT NULL,
    entry_time        TEXT NOT NULL,
    checkout_time     TEXT NOT NULL,
    barrier_time      TEXT NOT NULL,
    duration_minutes  INTEGER NOT NULL,
    fee_kes           INTEGER NOT NULL,
    amount_paid_kes   INTEGER NOT NULL,
    payment_method    TEXT NOT NULL,
    payment_reference TEXT
);

-- Index so daily / date-range reports do not scan the whole table
CREATE INDEX IF NOT EXISTS idx_transactions_barrier_time
    ON transactions (barrier_time);
"""


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

def get_connection(db_path=DATABASE_PATH) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database file and return a connection.

    - If the file does not exist yet, SQLite creates an empty one.
    - row_factory = sqlite3.Row lets us read a column by NAME
      (row["plate"]) instead of by position (row[0]), which is far easier
      to read and does not break if column order changes.
    - check_same_thread=False: FastAPI handles requests on several threads.
      By default SQLite refuses to share one connection between threads.
      We allow it because, from the next step on, every database change
      happens behind a single lock - only one thread uses it at a time.
    """
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


# --------------------------------------------------------------------------
# Creating tables and default data
# --------------------------------------------------------------------------

def initialise_database(connection: sqlite3.Connection) -> None:
    """
    Create all tables (if missing) and insert the default data (if missing).

    Safe to call on every start-up: it never overwrites data that already
    exists, so a rate the manager changed yesterday is not reset to the
    default today.
    """
    # executescript runs several SQL statements in one go (our whole schema).
    connection.executescript(SCHEMA)

    # "with connection:" is a transaction. Everything inside is saved
    # together when the block ends. If any line raises an error, ALL of it
    # is undone (rolled back) - the all-or-nothing rule from Section 8.3.
    with connection:
        # INSERT OR IGNORE: add the setting only if that key is not already
        # there. Existing values (e.g. a capacity management changed) stay.
        for key, value in DEFAULT_SETTINGS.items():
            connection.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )

        # Rates are copied in only when the table is completely empty -
        # i.e. the very first run. After that, management owns the rates.
        tier_count = connection.execute(
            "SELECT COUNT(*) FROM rate_tiers"
        ).fetchone()[0]

        if tier_count == 0:
            connection.executemany(
                "INSERT INTO rate_tiers (max_minutes, fee_kes) VALUES (?, ?)",
                DEFAULT_RATE_TIERS,
            )


# --------------------------------------------------------------------------
# Read helpers
# --------------------------------------------------------------------------

def get_settings(connection: sqlite3.Connection) -> dict[str, str]:
    """
    Return every setting as a dictionary, e.g. {"capacity": "20", ...}.

    Values come back as strings (the table stores TEXT); callers convert
    them to numbers where needed.
    """
    rows = connection.execute("SELECT key, value FROM settings").fetchall()
    # Dictionary comprehension: builds {key: value} from every row in one line.
    return {row["key"]: row["value"] for row in rows}


def get_rate_tiers(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """
    Return the rate tiers exactly as stored.

    No sorting or validation happens here - that is Module 3's job
    (load_rate_table), so the rules live in one place only.
    """
    return connection.execute(
        "SELECT tier_id, max_minutes, fee_kes FROM rate_tiers"
    ).fetchall()


# --------------------------------------------------------------------------
# Manual check: python database.py
# --------------------------------------------------------------------------

# This block runs ONLY when the file is run directly (python database.py),
# NOT when another file imports it. Python sets the special variable
# __name__ to "__main__" for the file you ran, and to the module's name
# ("database") when it is imported.
if __name__ == "__main__":
    conn = get_connection()
    initialise_database(conn)

    print(f"Database ready at: {DATABASE_PATH}\n")

    table_names = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    print("Tables:", ", ".join(row["name"] for row in table_names))

    print("\nSettings:")
    for key, value in get_settings(conn).items():
        print(f"  {key} = {value}")

    print("\nRate tiers:")
    for tier in get_rate_tiers(conn):
        limit = "no limit" if tier["max_minutes"] is None else f"up to {tier['max_minutes']} min"
        print(f"  {limit:<16} KES {tier['fee_kes']}")

    conn.close()