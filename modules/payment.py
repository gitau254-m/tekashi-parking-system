"""
Module 4 - Payment (design doc, Section 4.4).

Data structure: the same hash table record from Module 2, extended with
payment fields. Two ways to pay:

  - CARD / CASH (and MPESA when .env has no Daraja keys): SIMULATED,
    confirmed instantly.
  - MPESA with Daraja keys in .env: REAL sandbox STK Push. Payment becomes
    a 3-state flow:  PENDING (prompt sent) -> SUCCESS or FAILED,
    checked with check_mpesa_payment().

Key rule: the network call to Safaricom happens OUTSIDE state.lock. A call
can take several seconds - holding the lock that long would freeze every
other gate and screen in the system.
"""

import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import clock
import daraja
from config import PAYMENT_METHODS
from database import (create_mpesa_request, get_open_mpesa_request, save_vehicle,
                      update_mpesa_request)
from models import ParkingError, VehicleRecord
from modules.entry import clean_plate

if TYPE_CHECKING:
    from state import ParkingState

# Kenyan Safaricom numbers in international form: 2547XXXXXXXX or 2541XXXXXXXX.
VALID_PHONE = re.compile(r"254[17]\d{8}")

# A prompt times out on the phone after about a minute; after this long an
# unfinished request is treated as dead so the driver can try again.
MPESA_REQUEST_EXPIRY = timedelta(minutes=5)

# Friendly messages for the ResultCodes drivers are most likely to hit.
MPESA_RESULT_MESSAGES = {
    "1032": "You cancelled the M-Pesa prompt.",
    "1037": "No response - the M-Pesa prompt timed out.",
    "1": "Insufficient M-Pesa balance.",
    "2001": "Wrong M-Pesa PIN.",
}


# ---------------------------------------------------------------- helpers

def normalize_phone(raw_phone: str | None) -> str:
    """0712345678 / +254712345678 / 712345678  ->  254712345678. Raises if invalid."""
    digits = re.sub(r"\D", "", raw_phone or "")        # \D = any NON-digit, removed
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "17":
        digits = "254" + digits
    if not VALID_PHONE.fullmatch(digits):
        raise ParkingError("Enter a valid Safaricom number, e.g. 0712345678.")
    return digits


def mask_phone(phone: str) -> str:
    """254712345678 -> 2547****5678. Full numbers are never stored."""
    return phone[:4] + "****" + phone[-4:]


def _payable(state: "ParkingState", plate: str) -> tuple[VehicleRecord, int]:
    """Guard clauses shared by every payment path. Returns (record, balance). Caller holds lock."""
    record = state.vehicle_records.get(plate)
    if record is None:
        raise ParkingError(f"Unrecognized vehicle: {plate}", 404)
    if record.fee is None:
        raise ParkingError("Request an exit quote first.", 409)
    if record.paid:
        raise ParkingError("Nothing to pay - already settled.", 409)
    return record, record.fee - record.amount_paid


def _paid_record(record: VehicleRecord, amount: int, method: str, reference: str,
                 paid_at: datetime) -> VehicleRecord:
    """A copy of the record with a payment added. Paid only if the whole fee is covered."""
    amount_paid = record.amount_paid + amount
    return replace(record, amount_paid=amount_paid, paid=amount_paid >= record.fee,
                   payment_method=method, payment_reference=reference, paid_at=paid_at)


def _expire_stale_requests(state: "ParkingState", plate: str) -> None:
    """Mark a forgotten SENDING/PENDING request as FAILED so it cannot block new ones."""
    request = get_open_mpesa_request(state.connection, plate)
    if request is None:
        return
    now = clock.now()
    if now - datetime.fromisoformat(request["created_at"]) > MPESA_REQUEST_EXPIRY:
        with state.connection:
            update_mpesa_request(state.connection, request["request_id"], "FAILED", now,
                                 result_desc="Expired - no result received.")


# ---------------------------------------------------------------- simulated gateway

@dataclass(frozen=True)
class GatewayResult:
    """What a payment gateway answers: success, and a reference or a reason."""
    success: bool
    reference: str | None = None
    reason: str | None = None


def simulate_gateway(method: str, amount: int, simulate_failure: bool = False) -> GatewayResult:
    """Pretend provider. secrets.token_hex(3) = 6 random hex characters, e.g. '7F3A2C'."""
    if simulate_failure:
        return GatewayResult(success=False, reason="payment declined (simulated)")
    return GatewayResult(success=True, reference=f"SIM-{secrets.token_hex(3).upper()}")


# ---------------------------------------------------------------- public

def pay(state: "ParkingState", raw_plate: str, method: str, phone: str | None = None,
        simulate_failure: bool = False) -> dict:
    """UC6 entry point: routes MPESA to Daraja when configured, everything else to the simulator."""
    method = (method or "").strip().upper()
    if method not in PAYMENT_METHODS:
        raise ParkingError(f"Unsupported payment method. Use one of: {', '.join(PAYMENT_METHODS)}.")
    if method == "MPESA" and daraja.is_configured():
        return start_mpesa_payment(state, raw_plate, phone)
    return confirm_payment(state, raw_plate, method, simulate_failure)


def confirm_payment(state: "ParkingState", raw_plate: str, method: str,
                    simulate_failure: bool = False) -> dict:
    """Simulated payment: confirmed instantly. On failure nothing changes."""
    with state.lock:
        plate = clean_plate(raw_plate)
        record, balance = _payable(state, plate)
        result = simulate_gateway(method, balance, simulate_failure)
        if not result.success:
            raise ParkingError(f"Payment failed: {result.reason}. Barrier stays closed - please retry.", 402)

        updated = _paid_record(record, balance, method, result.reference, clock.now())
        with state.connection:
            save_vehicle(state.connection, plate, updated)
        state.vehicle_records[plate] = updated
        return {"status": "PAID", "plate": plate, "amount": balance, "method": method,
                "reference": result.reference,
                "message": f"Payment of KES {balance} received. Proceed to the exit "
                           f"within {state.grace_minutes} minutes of your quote."}


def start_mpesa_payment(state: "ParkingState", raw_plate: str, raw_phone: str | None) -> dict:
    """Send a real STK Push prompt. Three phases: reserve (locked), call (unlocked), record (locked)."""
    phone = normalize_phone(raw_phone)

    # Phase 1 - locked: validate, then RESERVE a request row BEFORE calling Safaricom,
    # so a double-tap finds it and cannot send a second prompt.
    with state.lock:
        plate = clean_plate(raw_plate)
        record, balance = _payable(state, plate)
        _expire_stale_requests(state, plate)
        if get_open_mpesa_request(state.connection, plate) is not None:
            raise ParkingError("An M-Pesa prompt is already waiting on the phone - "
                               "complete it, or wait a few minutes and try again.", 409)
        with state.connection:
            request_id = create_mpesa_request(state.connection, plate, mask_phone(phone),
                                              balance, clock.now())

    # Phase 2 - unlocked: the slow network call. Other requests keep working meanwhile.
    try:
        checkout_request_id = daraja.stk_push(phone, balance, plate)
    except daraja.DarajaError as error:
        with state.lock, state.connection:          # two context managers on one line
            update_mpesa_request(state.connection, request_id, "FAILED", clock.now(),
                                 result_desc=str(error))
        raise ParkingError(str(error), 502) from error

    # Phase 3 - locked: remember Safaricom's id so we can ask about it later.
    with state.lock, state.connection:
        update_mpesa_request(state.connection, request_id, "PENDING", clock.now(),
                             checkout_request_id=checkout_request_id)

    return {"status": "PENDING", "plate": plate, "amount": balance, "method": "MPESA",
            "message": f"Prompt sent to {mask_phone(phone)}. Enter your M-Pesa PIN, "
                       f"then check the payment status."}


def check_mpesa_payment(state: "ParkingState", raw_plate: str) -> dict:
    """Ask Safaricom how the latest prompt ended, and apply the result (poll this every ~5 s)."""
    with state.lock:
        plate = clean_plate(raw_plate)
        request = get_open_mpesa_request(state.connection, plate)
        if request is None:
            record = state.vehicle_records.get(plate)
            if record is not None and record.paid:
                return {"status": "PAID", "plate": plate, "message": "Payment complete."}
            raise ParkingError("No M-Pesa payment is waiting for this vehicle.", 404)
        if request["checkout_request_id"] is None:
            return {"status": "PENDING", "plate": plate, "message": "Sending the prompt..."}
        request_id = request["request_id"]
        checkout_request_id = request["checkout_request_id"]
        amount = request["amount_kes"]

    try:
        code, description = daraja.stk_query(checkout_request_id)     # unlocked network call
    except daraja.DarajaError as error:
        raise ParkingError(str(error), 502) from error

    with state.lock:
        # Re-check: another poll may have finished this request while we were waiting.
        status = state.connection.execute(
            "SELECT status FROM mpesa_requests WHERE request_id = ?", (request_id,)
        ).fetchone()["status"]
        if status != "PENDING":
            return {"status": status, "plate": plate, "message": "Already processed."}
        if code == "PENDING":
            return {"status": "PENDING", "plate": plate, "message": description}

        now = clock.now()
        if code != "0":
            with state.connection:
                update_mpesa_request(state.connection, request_id, "FAILED", now,
                                     result_code=code, result_desc=description)
            return {"status": "FAILED", "plate": plate, "result_code": code,
                    "message": MPESA_RESULT_MESSAGES.get(code, description or "Payment failed.")
                               + " Barrier stays closed - please try again."}

        # Success: mark the request AND the vehicle paid in ONE transaction.
        record = state.vehicle_records.get(plate)
        updated = None
        with state.connection:
            update_mpesa_request(state.connection, request_id, "SUCCESS", now,
                                 result_code=code, result_desc=description)
            if record is not None:
                updated = _paid_record(record, amount, "MPESA", checkout_request_id, now)
                save_vehicle(state.connection, plate, updated)
        if updated is not None:
            state.vehicle_records[plate] = updated
        return {"status": "PAID", "plate": plate, "amount": amount, "method": "MPESA",
                "reference": checkout_request_id,
                "message": f"M-Pesa payment of KES {amount} received. Proceed to the exit "
                           f"within {state.grace_minutes} minutes of your quote."}
