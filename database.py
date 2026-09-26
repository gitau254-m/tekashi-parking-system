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

It also holds the WRITE helpers (save_vehicle, delete_vehicle, ...). They
never commit by themselves - the caller wraps them in "with connection:"
so it decides which writes succeed or fail together (one transaction).

Run this file directly to create and inspect the database:
    python database.py
"""

import sqlite3

from clock import to_text
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

-- Every M-Pesa STK Push request (Module 4). Kept only in the database: it is
-- a payment log, not one of the in-memory data structures.
-- request_id is OUR id, created BEFORE calling Safaricom, so a double-tap can
-- never send two prompts for one bill (idempotency lives in our system).
CREATE TABLE IF NOT EXISTS mpesa_requests (
    request_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plate               TEXT NOT NULL,
    phone_masked        TEXT NOT NULL,                 -- e.g. 2547****4149, never the full number
    amount_kes          INTEGER NOT NULL CHECK (amount_kes > 0),
    checkout_request_id TEXT UNIQUE,                   -- Safaricom's id, known after the call
    status              TEXT NOT NULL
                        CHECK (status IN ('SENDING', 'PENDING', 'SUCCESS', 'FAILED')),
    result_code         TEXT,
    result_desc         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
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
# Write helpers (used by the modules - always inside "with connection:")
# --------------------------------------------------------------------------
# These take plain values/objects and only know SQL. They do NOT commit:
# the calling module decides the transaction boundary.

def save_vehicle(connection: sqlite3.Connection, plate: str, record) -> None:
    """
    Insert a vehicle record, or update it if the plate already exists.

    "INSERT ... ON CONFLICT(plate) DO UPDATE" is called an UPSERT
    (update + insert). 'excluded' means "the values we just tried to insert".
    """
    connection.execute(
        """
        INSERT INTO active_vehicles (
            plate, slot_index, entry_time, checkout_time, duration_minutes,
            fee_kes, amount_paid_kes, paid, payment_method, payment_reference, paid_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(plate) DO UPDATE SET
            slot_index        = excluded.slot_index,
            entry_time        = excluded.entry_time,
            checkout_time     = excluded.checkout_time,
            duration_minutes  = excluded.duration_minutes,
            fee_kes           = excluded.fee_kes,
            amount_paid_kes   = excluded.amount_paid_kes,
            paid              = excluded.paid,
            payment_method    = excluded.payment_method,
            payment_reference = excluded.payment_reference,
            paid_at           = excluded.paid_at
        """,
        (
            plate, record.slot, to_text(record.entry_time), to_text(record.checkout_time),
            record.duration_minutes, record.fee, record.amount_paid, int(record.paid),
            record.payment_method, record.payment_reference, to_text(record.paid_at),
        ),
    )


def delete_vehicle(connection: sqlite3.Connection, plate: str) -> None:
    """Remove a vehicle from active_vehicles (it has left the lot)."""
    connection.execute("DELETE FROM active_vehicles WHERE plate = ?", (plate,))


def add_to_queue(connection: sqlite3.Connection, plate: str, queued_at) -> None:
    """Add a vehicle to the back of the waiting queue."""
    connection.execute(
        "INSERT INTO waiting_queue (plate, queued_at) VALUES (?, ?)",
        (plate, to_text(queued_at)),
    )


def remove_from_queue(connection: sqlite3.Connection, plate: str) -> None:
    """Remove a vehicle from the waiting queue (promoted, or drove away)."""
    connection.execute("DELETE FROM waiting_queue WHERE plate = ?", (plate,))


def insert_transaction(connection: sqlite3.Connection, txn) -> None:
    """Append one completed stay to the audit log. There is no update/delete."""
    connection.execute(
        """
        INSERT INTO transactions (
            plate, slot_index, entry_time, checkout_time, barrier_time,
            duration_minutes, fee_kes, amount_paid_kes, payment_method, payment_reference
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            txn.plate, txn.slot, to_text(txn.entry_time), to_text(txn.checkout_time),
            to_text(txn.barrier_time), txn.duration_minutes, txn.fee, txn.amount_paid,
            txn.payment_method, txn.payment_reference,
        ),
    )


def replace_rate_tiers(connection: sqlite3.Connection, tiers: list[dict]) -> None:
    """Replace the whole rate table with an already-validated list of tiers."""
    connection.execute("DELETE FROM rate_tiers")
    connection.executemany(
        "INSERT INTO rate_tiers (max_minutes, fee_kes) VALUES (?, ?)",
        [(tier["max_minutes"], tier["fee_kes"]) for tier in tiers],
    )


def create_mpesa_request(connection: sqlite3.Connection, plate: str, phone_masked: str,
                         amount: int, created_at) -> int:
    """Record an M-Pesa request as SENDING before calling Safaricom. Returns our request_id."""
    cursor = connection.execute(
        "INSERT INTO mpesa_requests (plate, phone_masked, amount_kes, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'SENDING', ?, ?)",
        (plate, phone_masked, amount, to_text(created_at), to_text(created_at)),
    )
    return cursor.lastrowid          # the AUTOINCREMENT id SQLite just assigned


def update_mpesa_request(connection: sqlite3.Connection, request_id: int, status: str,
                         updated_at, checkout_request_id: str | None = None,
                         result_code: str | None = None, result_desc: str | None = None) -> None:
    """Move a request to a new status. COALESCE(new, old) keeps the old value when new is NULL."""
    connection.execute(
        """
        UPDATE mpesa_requests
        SET status = ?, updated_at = ?,
            checkout_request_id = COALESCE(?, checkout_request_id),
            result_code = COALESCE(?, result_code),
            result_desc = COALESCE(?, result_desc)
        WHERE request_id = ?
        """,
        (status, to_text(updated_at), checkout_request_id, result_code, result_desc, request_id),
    )


def get_open_mpesa_request(connection: sqlite3.Connection, plate: str) -> sqlite3.Row | None:
    """The newest request for this plate that is still SENDING or PENDING, or None."""
    return connection.execute(
        "SELECT * FROM mpesa_requests WHERE plate = ? AND status IN ('SENDING', 'PENDING') "
        "ORDER BY request_id DESC LIMIT 1",
        (plate,),
    ).fetchone()


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
