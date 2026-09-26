"""
Module 1 - Slot Management (design doc, Section 4.1).

Data structures: Array (state.slots) + Min-Heap (state.free_heap).
Tracks every slot, allocates the lowest-numbered free slot, and powers the
live availability display.
"""

import heapq
from typing import TYPE_CHECKING

from models import ParkingError

# TYPE_CHECKING is False when the program runs and True only for code editors.
# So this import is used for type hints (autocomplete, warnings) but never
# actually executed - which avoids a circular import with state.py.
if TYPE_CHECKING:
    from state import ParkingState


# ---------------------------------------------------------------- helpers
# (caller must already hold state.lock)

def peek_free_slot(state: "ParkingState") -> int:
    """Lowest free slot index WITHOUT removing it, or -1 if the lot is full. O(1)."""
    return state.free_heap[0] if state.free_heap else -1


def take_free_slot(state: "ParkingState") -> int:
    """Remove and return the lowest free slot index. O(log n)."""
    return heapq.heappop(state.free_heap)


def release_slot(state: "ParkingState", index: int) -> None:
    """Mark a slot empty and put its index back on the heap. O(log n)."""
    state.slots[index] = None
    heapq.heappush(state.free_heap, index)


def count_free_slots(state: "ParkingState") -> int:
    """Number of free slots = size of the heap. O(1)."""
    return len(state.free_heap)


def get_slot_map(state: "ParkingState", show_plates: bool = False) -> list[dict]:
    """
    Status of every slot, for drawing the map. O(n) - every slot is visited.
    Plates are only included for staff (show_plates=True) - privacy rule.
    """
    slot_map = []
    for index, plate in enumerate(state.slots):
        entry = {"slot_number": index + 1, "status": "FREE" if plate is None else "OCCUPIED"}
        if show_plates:
            entry["plate"] = plate
        slot_map.append(entry)
    return slot_map


def find_free_slot_linear(state: "ParkingState") -> int:
    """Baseline from the design doc (compared against the heap, not used). O(n)."""
    for index, plate in enumerate(state.slots):
        if plate is None:
            return index
    return -1


# ---------------------------------------------------------------- public
# (take the lock themselves)

def get_display(state: "ParkingState", show_plates: bool = False) -> dict:
    """Everything the entrance display needs, from one consistent moment (UC1)."""
    with state.lock:
        free = count_free_slots(state)
        return {
            "capacity": state.capacity,
            "free_slots": free,
            "occupied_slots": state.capacity - free,
            "vehicles_waiting": len(state.waiting_queue),
            "lot_full": free == 0,
            "slots": get_slot_map(state, show_plates),
        }


def who_is_in(state: "ParkingState", slot_number: int) -> dict:
    """Slot -> occupant lookup for staff (UC9). O(1)."""
    with state.lock:
        if not 1 <= slot_number <= state.capacity:
            raise ParkingError(f"No such slot: {slot_number}", 404)
        plate = state.slots[slot_number - 1]
        return {"slot_number": slot_number, "status": "FREE" if plate is None else "OCCUPIED",
                "plate": plate}
