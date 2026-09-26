"""
Module 3 - Duration & Fee Calculation (design doc, Section 4.3).

Data structure: sorted list of rate tiers (state.rate_table), editable by
management. Calculates duration (rounded UP to whole minutes) and the fee,
and fixes both onto the vehicle record as the "exit quote".
"""

import math
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

import clock
from database import replace_rate_tiers, save_vehicle
from models import ParkingError
from modules.entry import clean_plate

if TYPE_CHECKING:
    from state import ParkingState


# ---------------------------------------------------------------- pure functions
# "Pure" = output depends only on the inputs, no side effects. Easiest to test.

def calculate_duration(entry_time: datetime, exit_time: datetime) -> int:
    """Minutes parked, rounded UP: 30 min 1 s -> 31. Works across midnight."""
    seconds = (exit_time - entry_time).total_seconds()   # datetime - datetime = timedelta
    if seconds < 0:
        raise ParkingError("Clock error - exit time is before entry time.", 500)
    return math.ceil(seconds / 60)


def calculate_fee(duration_minutes: int, rate_table: list[dict]) -> int:
    """First tier that covers the duration. Precondition: table sorted ascending. O(m)."""
    for tier in rate_table:
        if tier["max_minutes"] is None or duration_minutes <= tier["max_minutes"]:
            return tier["fee_kes"]
    raise ParkingError("Rate table has no open-ended final tier.", 500)


def load_rate_table(rows: list[dict]) -> list[dict]:
    """
    Sort and validate rate tiers. Raises ParkingError if the table is unusable,
    so a bad table is refused and never replaces a good one. O(m log m).
    """
    tiers = [{"max_minutes": r["max_minutes"], "fee_kes": r["fee_kes"]} for r in rows]

    # sorted(..., key=...) sorts by whatever the key function returns.
    # lambda t: t["max_minutes"] is a tiny unnamed function: "given t, return its max_minutes".
    # Python's sort is Timsort - a merge sort / insertion sort hybrid.
    bounded = sorted((t for t in tiers if t["max_minutes"] is not None),
                     key=lambda t: t["max_minutes"])
    open_ended = [t for t in tiers if t["max_minutes"] is None]

    if len(open_ended) != 1:
        raise ParkingError("There must be exactly one open-ended final tier.")
    limits = [t["max_minutes"] for t in bounded]
    if len(limits) != len(set(limits)):          # a set drops duplicates
        raise ParkingError("Two tiers have the same time limit.")
    if any(t["max_minutes"] <= 0 for t in bounded) or any(t["fee_kes"] < 0 for t in tiers):
        raise ParkingError("Time limits must be positive and fees cannot be negative.")

    table = bounded + open_ended
    # zip(table, table[1:]) pairs each tier with the next one: (t0,t1), (t1,t2)...
    for previous, current in zip(table, table[1:]):
        if current["fee_kes"] < previous["fee_kes"]:
            raise ParkingError("Fees must not decrease as parking time increases.")
    return table


# ---------------------------------------------------------------- public

def request_exit_quote(state: "ParkingState", raw_plate: str) -> dict:
    """UC5: fix checkout time, duration and fee on the record; re-quotes charge the difference."""
    with state.lock:
        plate = clean_plate(raw_plate)
        record = state.vehicle_records.get(plate)
        if record is None:
            raise ParkingError(f"Unrecognized vehicle: {plate}", 404)

        checkout_time = clock.now()
        minutes = calculate_duration(record.entry_time, checkout_time)
        fee = calculate_fee(minutes, state.rate_table)
        balance = fee - record.amount_paid

        updated = replace(record, checkout_time=checkout_time, duration_minutes=minutes,
                          fee=fee, paid=balance <= 0)
        if balance <= 0 and record.amount_paid == 0:      # stayed 30 minutes or less
            updated = replace(updated, payment_method="FREE", paid_at=checkout_time)

        with state.connection:
            save_vehicle(state.connection, plate, updated)
        state.vehicle_records[plate] = updated

        return {"plate": plate, "slot_number": record.slot + 1,
                "entry_time": record.entry_time, "checkout_time": checkout_time,
                "duration_minutes": minutes, "fee": fee,
                "amount_paid": record.amount_paid, "balance_due": max(balance, 0),
                "paid": updated.paid, "grace_minutes": state.grace_minutes}


def get_rates(state: "ParkingState") -> list[dict]:
    """Current rate table (copies, so callers cannot change the real one)."""
    with state.lock:
        return [dict(tier) for tier in state.rate_table]


def update_rates(state: "ParkingState", new_tiers: list[dict]) -> list[dict]:
    """UC8: validate FIRST, then persist, then use. A bad table changes nothing."""
    with state.lock:
        table = load_rate_table(new_tiers)
        with state.connection:
            replace_rate_tiers(state.connection, table)
        state.rate_table = table
        return [dict(tier) for tier in table]
