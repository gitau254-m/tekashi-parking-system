"""
Module 6 - Reporting / Audit (design doc, Section 4.6).

Data structure: append-only list (state.audit_log), mirrored in the
'transactions' table. History is only ever added to - never edited.
"""

from dataclasses import asdict
from datetime import date, datetime
from typing import TYPE_CHECKING

from models import Transaction, VehicleRecord

if TYPE_CHECKING:
    from state import ParkingState


# ---------------------------------------------------------------- pure functions

def build_transaction(plate: str, record: VehicleRecord, barrier_time: datetime) -> Transaction:
    """Copy the STORED quote values, so duration and fee always match."""
    return Transaction(
        plate=plate, slot=record.slot, entry_time=record.entry_time,
        checkout_time=record.checkout_time, barrier_time=barrier_time,
        duration_minutes=record.duration_minutes, fee=record.fee,
        amount_paid=record.amount_paid, payment_method=record.payment_method,
        payment_reference=record.payment_reference,
    )


def total_collected(audit_log: list[Transaction]) -> int:
    """Every shilling ever collected. O(t) - each transaction visited once."""
    return sum(txn.amount_paid for txn in audit_log)


def daily_report(audit_log: list[Transaction], day: date) -> dict:
    """Vehicles and revenue for one day (by barrier time). O(t)."""
    todays = [txn for txn in audit_log if txn.barrier_time.date() == day]
    return {"date": day, "vehicles": len(todays),
            "revenue": sum(txn.amount_paid for txn in todays)}


def vat_portion(total: int, vat_rate_percent: int) -> float:
    """Fees are VAT-inclusive, so VAT inside a total = total x rate / (100 + rate)."""
    return round(total * vat_rate_percent / (100 + vat_rate_percent), 2)


# ---------------------------------------------------------------- public

def get_summary(state: "ParkingState", day: date) -> dict:
    """UC10: headline figures for the admin page."""
    with state.lock:
        total = total_collected(state.audit_log)
        report = daily_report(state.audit_log, day)
        return {"total_transactions": len(state.audit_log),
                "total_revenue": total,
                "total_vat": vat_portion(total, state.vat_rate_percent),
                "day": report,
                "day_vat": vat_portion(report["revenue"], state.vat_rate_percent),
                "vat_rate_percent": state.vat_rate_percent}


def recent_transactions(state: "ParkingState", limit: int = 20) -> list[dict]:
    """Newest first. [-limit:] = last 'limit' items; reversed() walks them backwards.
    {**a, "k": v} copies dict a and adds one more key."""
    with state.lock:
        return [{**asdict(txn), "slot_number": txn.slot + 1}
                for txn in reversed(state.audit_log[-limit:])]
