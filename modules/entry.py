"""
Module 2 - Vehicle Entry (design doc, Section 4.2).

Data structures: Hash Table (state.vehicle_records) + Queue
(state.waiting_queue) + Hash Set (state.queued_set).
Records arriving vehicles, or queues them fairly (FIFO) when the lot is full.
"""

import re
from datetime import datetime
from typing import TYPE_CHECKING

import clock
from database import add_to_queue, remove_from_queue, save_vehicle
from models import ParkingError, QueueEntry, VehicleRecord
from modules.slots import peek_free_slot, take_free_slot

if TYPE_CHECKING:
    from state import ParkingState

# A regular expression (regex) is a pattern for text.
# [A-Z0-9]{1,10} = "1 to 10 characters, each a capital letter or a digit".
# re.compile prepares the pattern once so it is fast to reuse.
VALID_PLATE = re.compile(r"[A-Z0-9]{1,10}")


# ---------------------------------------------------------------- helpers

def normalize_plate(raw_plate: str | None) -> str:
    """'kda 123-x' -> 'KDA123X'. \\s = any whitespace; [\\s-] = whitespace or dash."""
    return re.sub(r"[\s-]", "", raw_plate or "").upper()


def is_valid_plate(plate: str) -> bool:
    """fullmatch = the WHOLE string must match the pattern, not just part of it."""
    return VALID_PLATE.fullmatch(plate) is not None


def clean_plate(raw_plate: str | None) -> str:
    """Normalize and validate in one step; raises ParkingError if invalid."""
    plate = normalize_plate(raw_plate)
    if not is_valid_plate(plate):
        raise ParkingError("Invalid number plate - use 1 to 10 letters and digits.")
    return plate


def new_record(slot_index: int, time: datetime) -> VehicleRecord:
    """A fresh vehicle record: only slot and entry time are known at arrival."""
    return VehicleRecord(slot=slot_index, entry_time=time)


# ---------------------------------------------------------------- public

def handle_arrival(state: "ParkingState", raw_plate: str) -> dict:
    """UC2 / UC3: give the vehicle the lowest free slot, or queue it."""
    with state.lock:
        if state.capacity == 0:
            raise ParkingError("No parking capacity configured.", 503)

        plate = clean_plate(raw_plate)
        if plate in state.vehicle_records:                     # O(1) hash lookup
            raise ParkingError(f"{plate} is already parked inside.", 409)
        if plate in state.queued_set:                          # O(1) set lookup
            raise ParkingError(f"{plate} is already in the waiting queue.", 409)

        time = clock.now()
        index = peek_free_slot(state)

        if index != -1:
            record = new_record(index, time)
            with state.connection:                  # 1. persist first
                save_vehicle(state.connection, plate, record)
            take_free_slot(state)                   # 2. then mirror in memory
            state.slots[index] = plate
            state.vehicle_records[plate] = record
            return {"status": "ENTERED", "plate": plate, "slot_number": index + 1,
                    "entry_time": time,
                    "message": f"Welcome - proceed to Slot {index + 1}."}

        entry = QueueEntry(plate=plate, queued_at=time)
        with state.connection:
            add_to_queue(state.connection, plate, time)
        state.waiting_queue.append(entry)           # join the BACK of the queue, O(1)
        state.queued_set.add(plate)
        position = len(state.waiting_queue)
        return {"status": "QUEUED", "plate": plate, "position": position,
                "message": f"Lot full - you are number {position} in the queue."}


def leave_queue(state: "ParkingState", raw_plate: str) -> dict:
    """UC4: a waiting vehicle drives away. O(q) - removing from the middle."""
    with state.lock:
        plate = clean_plate(raw_plate)
        if plate not in state.queued_set:
            raise ParkingError(f"{plate} is not in the waiting queue.", 404)

        with state.connection:
            remove_from_queue(state.connection, plate)
        # next(...) returns the FIRST item the generator produces - here, the
        # queue entry whose plate matches. deque.remove then deletes it.
        entry = next(e for e in state.waiting_queue if e.plate == plate)
        state.waiting_queue.remove(entry)
        state.queued_set.discard(plate)
        return {"status": "LEFT_QUEUE", "plate": plate,
                "message": f"{plate} removed from the queue - no charge."}


def find_vehicle(state: "ParkingState", raw_plate: str) -> dict:
    """UC9: occupant -> slot lookup for staff. O(1) average."""
    with state.lock:
        plate = clean_plate(raw_plate)
        record = state.vehicle_records.get(plate)       # .get returns None if missing
        if record is not None:
            return {"plate": plate, "status": "PARKED", "slot_number": record.slot + 1,
                    "entry_time": record.entry_time, "fee": record.fee,
                    "amount_paid": record.amount_paid, "paid": record.paid}
        if plate in state.queued_set:
            position = [e.plate for e in state.waiting_queue].index(plate) + 1
            return {"plate": plate, "status": "QUEUED", "position": position}
        raise ParkingError(f"{plate} is not in the parking.", 404)


def get_queue(state: "ParkingState") -> list[dict]:
    """The waiting queue in order, front first (for staff)."""
    with state.lock:
        return [{"position": i + 1, "plate": e.plate, "queued_at": e.queued_at}
                for i, e in enumerate(state.waiting_queue)]
