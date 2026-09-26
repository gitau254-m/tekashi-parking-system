"""
Module 5 - Barrier Control (design doc, Section 4.5).

No new data structure: coordinates Module 1's array + heap, Module 2's
hash table + queue + set, and Module 6's audit list. Opens the barrier only
for a known, paid vehicle inside its grace period.
"""

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import clock
from database import delete_vehicle, insert_transaction, remove_from_queue, save_vehicle
from models import ParkingError
from modules.audit import build_transaction
from modules.entry import clean_plate, new_record
from modules.slots import release_slot

if TYPE_CHECKING:
    from state import ParkingState


def process_exit(state: "ParkingState", raw_plate: str) -> dict:
    """UC7: guard clauses first, then one all-or-nothing exit."""
    with state.lock:
        plate = clean_plate(raw_plate)

        # ---- Guard clauses: each check exits early if it fails ----
        record = state.vehicle_records.get(plate)
        if record is None:
            raise ParkingError("Unrecognized vehicle - barrier remains closed.", 404)
        if not record.paid:
            raise ParkingError("Payment required - barrier remains closed.", 402)

        now = clock.now()
        if now - record.checkout_time > timedelta(minutes=state.grace_minutes):
            # The reset IS saved; then we tell the driver why the barrier stayed shut.
            updated = replace(record, paid=False)
            with state.connection:
                save_vehicle(state.connection, plate, updated)
            state.vehicle_records[plate] = updated
            raise ParkingError("Exit window expired - please request a new quote.", 409)

        # ---- Prepare everything before touching storage ----
        transaction = build_transaction(plate, record, now)      # Module 6
        freed_index = record.slot
        next_entry = state.waiting_queue[0] if state.waiting_queue else None  # look, don't remove
        next_record = new_record(freed_index, now) if next_entry else None

        # ---- Step A: persist first, all-or-nothing ----
        # If any line raises, "with" rolls back EVERYTHING and memory is untouched.
        # Order matters: delete the leaving car BEFORE inserting the promoted car,
        # otherwise UNIQUE(slot_index) would reject two cars in one slot.
        with state.connection:
            insert_transaction(state.connection, transaction)   # log BEFORE delete
            delete_vehicle(state.connection, plate)
            if next_entry:
                remove_from_queue(state.connection, next_entry.plate)
                save_vehicle(state.connection, next_entry.plate, next_record)

        # ---- Step B: mirror in memory ----
        state.audit_log.append(transaction)
        del state.vehicle_records[plate]
        promoted = None
        if next_entry:
            state.waiting_queue.popleft()                    # serve the FRONT, O(1)
            state.queued_set.discard(next_entry.plate)
            state.slots[freed_index] = next_entry.plate
            state.vehicle_records[next_entry.plate] = next_record
            promoted = {"plate": next_entry.plate, "slot_number": freed_index + 1,
                        "message": f"{next_entry.plate} - proceed to Slot {freed_index + 1}."}
        else:
            release_slot(state, freed_index)                 # back onto the heap, O(log n)

        return {"status": "EXITED", "plate": plate, "barrier": "OPEN",
                "duration_minutes": record.duration_minutes, "amount_paid": record.amount_paid,
                "promoted": promoted, "message": "Exit successful - barrier opened. Safe journey!"}
