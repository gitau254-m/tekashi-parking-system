"""
models.py - The shapes of the data, plus the system's error type.

Why a separate file? The six modules (Steps 4-9) need these shapes, and
state.py will later need one of the modules. If modules imported state.py
AND state.py imported a module, Python would hit a "circular import" and
crash. This file imports nothing from the project, so anything can safely
import it.
"""

from dataclasses import dataclass
from datetime import datetime


class ParkingError(Exception):
    """
    A business-rule failure the user should see, e.g. "Lot full" or
    "Payment required". Module functions RAISE it; main.py turns it into
    an HTTP response with the right status code.

    status_code follows HTTP conventions:
        400 bad request, 402 payment required, 404 not found, 409 conflict.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)       # let the normal Exception set itself up
        self.message = message
        self.status_code = status_code


# frozen=True: read-only after creation. Change = make a copy with
# dataclasses.replace(record, field=new_value). Enforces write-through.

@dataclass(frozen=True)
class VehicleRecord:
    """One vehicle currently inside the lot. The plate is the dict KEY, not a field."""
    slot: int                                 # slot index (0-based)
    entry_time: datetime
    checkout_time: datetime | None = None     # Module 3
    duration_minutes: int | None = None       # Module 3
    fee: int | None = None                    # Module 3, whole KES
    amount_paid: int = 0                      # Module 4, whole KES
    paid: bool = False
    payment_method: str | None = None         # MPESA, CARD, CASH or FREE
    payment_reference: str | None = None
    paid_at: datetime | None = None


@dataclass(frozen=True)
class QueueEntry:
    """One vehicle waiting at the gate while the lot is full."""
    plate: str
    queued_at: datetime


@dataclass(frozen=True)
class Transaction:
    """One completed stay in the append-only audit log."""
    plate: str
    slot: int
    entry_time: datetime
    checkout_time: datetime
    barrier_time: datetime
    duration_minutes: int
    fee: int
    amount_paid: int
    payment_method: str
    payment_reference: str | None