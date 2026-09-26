"""
state.py - The working layer of the system (design doc, Sections 8.2, 8.6, 8.7).

This file holds:
  1. the SHAPES of the data (VehicleRecord, QueueEntry, Transaction),
  2. ParkingState - one object holding every in-memory data structure
     that the six modules share,
  3. rebuild_from_database - fills ParkingState from SQLite on start-up,
  4. check_invariants - verifies the rules that must always be true.

Why rebuild_from_database lives here and not in database.py:
database.py only knows about SQL. This function builds ParkingState, so it
belongs next to ParkingState. If database.py imported state.py AND state.py
imported database.py, Python would hit a "circular import" and fail.
"""

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import sqlite3

from clock import from_text
from database import get_rate_tiers, get_settings


# ==========================================================================
# 1. Data shapes
# ==========================================================================
# A dataclass is a class whose main job is to hold data. Python writes the
# __init__ (constructor) for us from the field list.
#
# frozen=True makes each object READ-ONLY after it is created. To "change"
# a record you must build a new copy:
#     updated = dataclasses.replace(record, paid=True)
# This enforces the write-through rule (Section 8.3): build the new version,
# save it to the database, and only then put it into memory. Nobody can
# change a record in memory by accident halfway through.

@dataclass(frozen=True)
class VehicleRecord:
    """One vehicle currently inside the lot (design doc, Module 2 table).

    The number plate is NOT a field here - it is the KEY of the
    vehicle_records hash table that stores these records.
    """
    slot: int                                 # index of the allocated slot (0-based)
    entry_time: datetime                      # when the vehicle was given its slot
    checkout_time: datetime | None = None     # set by Module 3 (exit quote)
    duration_minutes: int | None = None       # set by Module 3
    fee: int | None = None                    # set by Module 3, whole KES
    amount_paid: int = 0                      # set by Module 4, whole KES
    paid: bool = False                        # True when nothing is owed for the quote
    payment_method: str | None = None         # MPESA, CARD, CASH or FREE
    payment_reference: str | None = None      # receipt / transaction reference
    paid_at: datetime | None = None           # when payment was confirmed


@dataclass(frozen=True)
class QueueEntry:
    """One vehicle waiting at the gate while the lot is full (Module 2)."""
    plate: str
    queued_at: datetime


@dataclass(frozen=True)
class Transaction:
    """One completed stay in the append-only audit log (Module 6)."""
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


# ==========================================================================
# 2. ParkingState - every in-memory structure in one place
# ==========================================================================

class ParkingState:
    """
    The shared working layer. One ParkingState object exists while the app
    runs; every module function receives it as its 'state' parameter
    (exactly like the pseudocode: handle_arrival(raw_plate, state)).
    """

    def __init__(self, connection: sqlite3.Connection, capacity: int,
                 grace_minutes: int, vat_rate_percent: int) -> None:
        # The open database connection (persistent layer).
        self.connection = connection

        # One lock for the whole state. Every operation that changes data
        # runs inside "with state.lock:", so two requests arriving at the
        # same moment are handled one after the other, never together.
        # (Design doc, Section 8.3 and exception #20.)
        self.lock = threading.Lock()

        # Settings loaded from the database.
        self.capacity = capacity
        self.grace_minutes = grace_minutes
        self.vat_rate_percent = vat_rate_percent

        # Module 1 - Array: index = slot number - 1, value = plate or None.
        # [None] * capacity creates the whole fixed-size array at once.
        self.slots: list[str | None] = [None] * capacity

        # Module 1 - Min-Heap of free slot indices (managed with heapq).
        self.free_heap: list[int] = []

        # Module 2 - Hash Table: plate -> VehicleRecord.
        self.vehicle_records: dict[str, VehicleRecord] = {}

        # Module 2 - Queue (FIFO) of vehicles waiting, plus a Hash Set of
        # the same plates for O(1) "is this plate waiting?" checks.
        self.waiting_queue: deque[QueueEntry] = deque()
        self.queued_set: set[str] = set()

        # Module 3 - rate table. For now these are the raw database rows;
        # in Step 6 Module 3's load_rate_table will sort and validate them.
        self.rate_table: list[dict] = []

        # Module 6 - append-only audit log.
        self.audit_log: list[Transaction] = []


# ==========================================================================
# 3. Rebuilding memory from the database (design doc, Section 8.6)
# ==========================================================================

class StartupError(Exception):
    """Raised when the database contents cannot form a valid state."""


def _vehicle_from_row(row: sqlite3.Row) -> VehicleRecord:
    """Convert one active_vehicles database row into a VehicleRecord.

    The leading underscore is a Python convention meaning "private helper -
    only used inside this file".
    """
    return VehicleRecord(
        slot=row["slot_index"],
        entry_time=from_text(row["entry_time"]),
        checkout_time=from_text(row["checkout_time"]),
        duration_minutes=row["duration_minutes"],
        fee=row["fee_kes"],
        amount_paid=row["amount_paid_kes"],
        paid=bool(row["paid"]),                  # database 0/1 -> Python False/True
        payment_method=row["payment_method"],
        payment_reference=row["payment_reference"],
        paid_at=from_text(row["paid_at"]),
    )


def _transaction_from_row(row: sqlite3.Row) -> Transaction:
    """Convert one transactions database row into a Transaction."""
    return Transaction(
        plate=row["plate"],
        slot=row["slot_index"],
        entry_time=from_text(row["entry_time"]),
        checkout_time=from_text(row["checkout_time"]),
        barrier_time=from_text(row["barrier_time"]),
        duration_minutes=row["duration_minutes"],
        fee=row["fee_kes"],
        amount_paid=row["amount_paid_kes"],
        payment_method=row["payment_method"],
        payment_reference=row["payment_reference"],
    )


def rebuild_from_database(connection: sqlite3.Connection) -> ParkingState:
    """
    Build every in-memory structure from the database.

    Called once on every start-up, so a restart or power cut loses nothing.
    Complexity: O(n + v + q + t), each row is read once.
    """
    # --- Settings (stored as text, converted to whole numbers) ---
    settings = get_settings(connection)
    capacity = int(settings["capacity"])
    grace_minutes = int(settings["grace_minutes"])
    vat_rate_percent = int(settings["vat_rate_percent"])

    if capacity < 0:
        raise StartupError(f"Capacity cannot be negative (found {capacity}).")

    state = ParkingState(connection, capacity, grace_minutes, vat_rate_percent)

    # --- Hash table + array: every vehicle currently inside ---
    for row in connection.execute("SELECT * FROM active_vehicles"):
        plate = row["plate"]
        index = row["slot_index"]
        if index >= capacity:
            # Only possible if capacity was lowered while cars were parked,
            # which the design forbids (Section 1.4). Stop with a clear message.
            raise StartupError(
                f"{plate} is recorded in slot {index + 1}, "
                f"but capacity is only {capacity}."
            )
        state.vehicle_records[plate] = _vehicle_from_row(row)
        state.slots[index] = plate

    # --- Min-heap: every index whose slot is still empty ---
    # This list comprehension walks 0, 1, 2 ... in ASCENDING order, and a
    # sorted list already satisfies the heap rule (every parent <= its
    # children), so no extra work is needed. check_invariants confirms it.
    state.free_heap = [i for i in range(capacity) if state.slots[i] is None]

    # --- Queue + set: vehicles waiting, in arrival order ---
    # ORDER BY queue_id matters: the ids increase with arrival time, so this
    # restores the exact first-come, first-served order.
    for row in connection.execute(
        "SELECT plate, queued_at FROM waiting_queue ORDER BY queue_id"
    ):
        state.waiting_queue.append(
            QueueEntry(plate=row["plate"], queued_at=from_text(row["queued_at"]))
        )
        state.queued_set.add(row["plate"])

    # --- Rate table (validated by Module 3 from Step 6 onwards) ---
    state.rate_table = [dict(row) for row in get_rate_tiers(connection)]

    # --- Audit log: every completed stay, oldest first ---
    state.audit_log = [
        _transaction_from_row(row)
        for row in connection.execute("SELECT * FROM transactions ORDER BY txn_id")
    ]

    # Refuse to start with broken data - better to fail loudly now than to
    # hand out a slot that is already occupied later ("fail fast").
    check_invariants(state)
    return state


# ==========================================================================
# 4. Invariants (design doc, Section 8.7)
# ==========================================================================

class InvariantError(Exception):
    """Raised when the in-memory structures disagree with each other."""


def check_invariants(state: ParkingState) -> None:
    """
    Check every rule that must ALWAYS be true. Collects all problems first,
    then raises one error listing them, so you see everything at once.
    Called after start-up now, and inside tests later.
    """
    problems: list[str] = []

    # 1. The array and the hash table always agree (both directions).
    for index, plate in enumerate(state.slots):
        if plate is None:
            continue
        record = state.vehicle_records.get(plate)
        if record is None:
            problems.append(f"Slot {index + 1} holds {plate}, but {plate} has no record.")
        elif record.slot != index:
            problems.append(
                f"Slot {index + 1} holds {plate}, but its record says slot {record.slot + 1}."
            )

    for plate, record in state.vehicle_records.items():
        if not 0 <= record.slot < state.capacity:
            problems.append(f"{plate} has an impossible slot index {record.slot}.")
        elif state.slots[record.slot] != plate:
            problems.append(f"{plate}'s record says slot {record.slot + 1}, but that slot disagrees.")

    # 2. An index is in the heap exactly when its slot is empty (no duplicates).
    empty_indices = {i for i, plate in enumerate(state.slots) if plate is None}
    if len(state.free_heap) != len(set(state.free_heap)):
        problems.append("free_heap contains the same slot more than once.")
    if set(state.free_heap) != empty_indices:
        problems.append("free_heap does not match the empty slots in the array.")

    # 3. Every slot is either free or occupied - never both, never neither.
    if len(state.free_heap) + len(state.vehicle_records) != state.capacity:
        problems.append(
            f"free ({len(state.free_heap)}) + occupied ({len(state.vehicle_records)}) "
            f"!= capacity ({state.capacity})."
        )

    # 4. Nobody waits while a slot is free.
    if state.free_heap and state.waiting_queue:
        problems.append("Vehicles are waiting even though a slot is free.")

    # 5. The queue and the set hold the same plates; no plate is parked AND queued.
    queued_plates = [entry.plate for entry in state.waiting_queue]
    if len(queued_plates) != len(set(queued_plates)):
        problems.append("A plate appears in the waiting queue more than once.")
    if set(queued_plates) != state.queued_set:
        problems.append("queued_set does not match the waiting queue.")
    parked_and_queued = state.queued_set & state.vehicle_records.keys()
    if parked_and_queued:
        problems.append(f"Plates both parked and queued: {sorted(parked_and_queued)}.")

    # 6. free_heap obeys the heap rule. A heap is stored in a plain list:
    #    the children of position i are at 2i + 1 and 2i + 2, so the parent
    #    of position c is (c - 1) // 2. Every parent must be <= its child.
    heap = state.free_heap
    for child in range(1, len(heap)):
        parent = (child - 1) // 2
        if heap[parent] > heap[child]:
            problems.append(
                f"free_heap breaks the heap rule at position {child} "
                f"(parent {heap[parent]} > child {heap[child]})."
            )
            break

    if problems:
        raise InvariantError("State is inconsistent:\n- " + "\n- ".join(problems))