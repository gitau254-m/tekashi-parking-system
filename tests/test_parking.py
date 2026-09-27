"""
tests/test_parking.py - Automated tests for all six modules.

Run from the project root:
    python -m pytest -v

Each test gets a brand-new temporary database (never your real parking.db)
with a 2-slot lot, and a FROZEN clock we can move forward by hand.
"""

from datetime import datetime, timedelta

import pytest

import clock
import daraja
from config import DEFAULT_RATE_TIERS, TIMEZONE
from database import get_connection, initialise_database
from models import ParkingError
from modules import audit, barrier, entry, fees, payment, slots
from modules.fees import calculate_duration, calculate_fee, load_rate_table
from state import check_invariants, rebuild_from_database


# ---------------------------------------------------------------- fixtures
# A fixture is setup code pytest runs for any test that names it as a parameter.

@pytest.fixture
def advance(monkeypatch):
    """Freeze time at 08:00; call advance(minutes=..) to move it forward."""
    current = {"time": datetime(2026, 9, 25, 8, 0, tzinfo=TIMEZONE)}
    # monkeypatch temporarily swaps clock.now for our fake (undone after the test).
    monkeypatch.setattr(clock, "now", lambda: current["time"])

    def move(minutes: int = 0, seconds: int = 0) -> None:
        current["time"] += timedelta(minutes=minutes, seconds=seconds)

    return move


@pytest.fixture
def state(tmp_path, advance):
    """A fresh 2-slot parking in a temporary database. tmp_path is a pytest temp folder."""
    connection = get_connection(tmp_path / "test.db")
    initialise_database(connection)
    with connection:
        connection.execute("UPDATE settings SET value = '2' WHERE key = 'capacity'")
    parking = rebuild_from_database(connection)
    yield parking                       # the test runs here
    check_invariants(parking)           # after EVERY test, the rules must still hold
    connection.close()


@pytest.fixture(autouse=True)
def no_real_mpesa(monkeypatch):
    """autouse = applies to EVERY test. Tests must never call the real Safaricom
    servers, even if your .env has keys: MPESA is simulated unless a test says otherwise."""
    monkeypatch.setattr(daraja, "is_configured", lambda: False)


def rates():
    return load_rate_table([{"max_minutes": m, "fee_kes": f} for m, f in DEFAULT_RATE_TIERS])


# ---------------------------------------------------------------- Module 1: slots

def test_display_counts_and_privacy(state):
    entry.handle_arrival(state, "AAA111")
    display = slots.get_display(state)
    assert (display["free_slots"], display["occupied_slots"]) == (1, 1)
    assert "plate" not in display["slots"][0]                 # public view hides plates
    assert slots.get_display(state, show_plates=True)["slots"][0]["plate"] == "AAA111"


def test_slot_lookup_out_of_range(state):
    with pytest.raises(ParkingError):
        slots.who_is_in(state, 99)


# ---------------------------------------------------------------- Module 2: entry

def test_plate_is_normalized_and_duplicates_rejected(state):
    assert entry.handle_arrival(state, "kda 123-x")["plate"] == "KDA123X"
    with pytest.raises(ParkingError):
        entry.handle_arrival(state, "KDA123X")


def test_invalid_plate_rejected(state):
    with pytest.raises(ParkingError):
        entry.handle_arrival(state, "   ")


def test_lowest_free_slot_is_allocated(state):
    assert entry.handle_arrival(state, "AAA111")["slot_number"] == 1
    assert entry.handle_arrival(state, "BBB222")["slot_number"] == 2


def test_full_lot_queues_fifo(state):
    entry.handle_arrival(state, "AAA111")
    entry.handle_arrival(state, "BBB222")
    assert entry.handle_arrival(state, "CCC333")["position"] == 1
    assert entry.handle_arrival(state, "DDD444")["position"] == 2
    with pytest.raises(ParkingError):                        # cannot queue twice
        entry.handle_arrival(state, "CCC333")


def test_leave_queue(state):
    entry.handle_arrival(state, "AAA111")
    entry.handle_arrival(state, "BBB222")
    entry.handle_arrival(state, "CCC333")
    entry.leave_queue(state, "CCC333")
    assert len(state.waiting_queue) == 0 and "CCC333" not in state.queued_set


# ---------------------------------------------------------------- Module 3: pure fee logic

# parametrize runs the same test once per (minutes, fee) pair - the boundary
# table from design doc Section 4.3.
@pytest.mark.parametrize("minutes, fee", [
    (0, 0), (30, 0), (31, 50), (120, 50), (121, 100), (240, 100),
    (241, 300), (360, 300), (361, 500), (1500, 500),
])
def test_fee_boundaries(minutes, fee):
    assert calculate_fee(minutes, rates()) == fee


def test_duration_rounds_up():
    start = datetime(2026, 9, 25, 8, 0, tzinfo=TIMEZONE)
    assert calculate_duration(start, start + timedelta(minutes=30, seconds=1)) == 31


def test_duration_across_midnight():
    start = datetime(2026, 9, 25, 23, 0, tzinfo=TIMEZONE)
    assert calculate_duration(start, datetime(2026, 9, 26, 1, 0, tzinfo=TIMEZONE)) == 120


def test_exit_before_entry_is_rejected():
    start = datetime(2026, 9, 25, 8, 0, tzinfo=TIMEZONE)
    with pytest.raises(ParkingError):          # the test PASSES only if this raises
        calculate_duration(start, start - timedelta(minutes=1))


@pytest.mark.parametrize("bad_table", [
    [{"max_minutes": 30, "fee_kes": 0}],                                          # no final tier
    [{"max_minutes": None, "fee_kes": 0}, {"max_minutes": None, "fee_kes": 5}],   # two final tiers
    [{"max_minutes": 30, "fee_kes": 0}, {"max_minutes": 30, "fee_kes": 10},
     {"max_minutes": None, "fee_kes": 20}],                                       # duplicate limit
    [{"max_minutes": 30, "fee_kes": -5}, {"max_minutes": None, "fee_kes": 20}],   # negative fee
    [{"max_minutes": 30, "fee_kes": 100}, {"max_minutes": None, "fee_kes": 50}],  # fee decreases
])
def test_invalid_rate_tables_are_refused(bad_table):
    with pytest.raises(ParkingError):
        load_rate_table(bad_table)


def test_unsorted_rate_table_is_sorted():
    table = load_rate_table([{"max_minutes": None, "fee_kes": 500},
                             {"max_minutes": 120, "fee_kes": 50},
                             {"max_minutes": 30, "fee_kes": 0}])
    assert [t["max_minutes"] for t in table] == [30, 120, None]


# ---------------------------------------------------------------- Module 3: exit quote

def test_short_stay_is_free_and_auto_paid(state, advance):
    entry.handle_arrival(state, "KDA123X")
    advance(minutes=20)
    quote = fees.request_exit_quote(state, "KDA123X")
    assert (quote["fee"], quote["paid"]) == (0, True)
    assert state.vehicle_records["KDA123X"].payment_method == "FREE"


def test_paid_stay_quote(state, advance):
    entry.handle_arrival(state, "KDA123X")
    advance(minutes=45)
    quote = fees.request_exit_quote(state, "KDA123X")
    assert (quote["duration_minutes"], quote["balance_due"], quote["paid"]) == (45, 50, False)


def test_quote_for_unknown_plate(state):
    with pytest.raises(ParkingError):
        fees.request_exit_quote(state, "NOPE1")


def test_bad_rate_update_keeps_old_rates(state):
    before = fees.get_rates(state)
    with pytest.raises(ParkingError):
        fees.update_rates(state, [{"max_minutes": 30, "fee_kes": 0}])   # no final tier
    assert fees.get_rates(state) == before


# ---------------------------------------------------------------- Module 4: payment

def quoted(state, advance, plate="KDA123X", minutes=45):
    """Helper: park a car and get a paid-stay quote (45 min -> KES 50)."""
    entry.handle_arrival(state, plate)
    advance(minutes=minutes)
    fees.request_exit_quote(state, plate)


def test_simulated_payment(state, advance):
    quoted(state, advance)
    receipt = payment.pay(state, "KDA123X", "card")
    assert (receipt["status"], receipt["amount"]) == ("PAID", 50)
    assert state.vehicle_records["KDA123X"].paid is True


def test_payment_needs_a_quote_first(state):
    entry.handle_arrival(state, "KDA123X")
    with pytest.raises(ParkingError):
        payment.pay(state, "KDA123X", "CASH")


def test_failed_payment_changes_nothing(state, advance):
    quoted(state, advance)
    with pytest.raises(ParkingError):
        payment.pay(state, "KDA123X", "CARD", simulate_failure=True)
    assert state.vehicle_records["KDA123X"].paid is False


def test_cannot_pay_twice(state, advance):
    quoted(state, advance)
    payment.pay(state, "KDA123X", "CASH")
    with pytest.raises(ParkingError):
        payment.pay(state, "KDA123X", "CASH")


@pytest.mark.parametrize("raw, expected", [
    ("0712345678", "254712345678"), ("+254 712 345 678", "254712345678"),
    ("712345678", "254712345678"), ("0110345678", "254110345678"),
])
def test_phone_normalization(raw, expected):
    assert payment.normalize_phone(raw) == expected


def fake_mpesa(monkeypatch, query_answers):
    """Pretend Safaricom: stk_push succeeds; stk_query returns the given answers in order."""
    monkeypatch.setattr(daraja, "is_configured", lambda: True)
    monkeypatch.setattr(daraja, "stk_push", lambda phone, amount, ref: "ws_CO_TEST123")
    answers = iter(query_answers)
    monkeypatch.setattr(daraja, "stk_query", lambda checkout_id: next(answers))


def test_mpesa_prompt_then_success(state, advance, monkeypatch):
    quoted(state, advance)
    fake_mpesa(monkeypatch, [("PENDING", "waiting"), ("0", "processed successfully")])
    assert payment.pay(state, "KDA123X", "MPESA", phone="0712345678")["status"] == "PENDING"
    assert payment.check_mpesa_payment(state, "KDA123X")["status"] == "PENDING"
    assert payment.check_mpesa_payment(state, "KDA123X")["status"] == "PAID"
    record = state.vehicle_records["KDA123X"]
    assert (record.paid, record.payment_method, record.amount_paid) == (True, "MPESA", 50)


def test_mpesa_cancelled_leaves_car_unpaid(state, advance, monkeypatch):
    quoted(state, advance)
    fake_mpesa(monkeypatch, [("1032", "Request cancelled by user")])
    payment.pay(state, "KDA123X", "MPESA", phone="0712345678")
    assert payment.check_mpesa_payment(state, "KDA123X")["status"] == "FAILED"
    assert state.vehicle_records["KDA123X"].paid is False


def test_mpesa_double_tap_sends_one_prompt(state, advance, monkeypatch):
    quoted(state, advance)
    fake_mpesa(monkeypatch, [])
    payment.pay(state, "KDA123X", "MPESA", phone="0712345678")
    with pytest.raises(ParkingError):                      # second prompt refused
        payment.pay(state, "KDA123X", "MPESA", phone="0712345678")


# ---------------------------------------------------------------- Module 5: barrier

def test_paid_stay_full_journey(state, advance):
    quoted(state, advance)
    with pytest.raises(ParkingError):                    # barrier closed before payment
        barrier.process_exit(state, "KDA123X")
    payment.pay(state, "KDA123X", "MPESA")               # simulated (autouse fixture)
    result = barrier.process_exit(state, "KDA123X")
    assert result["barrier"] == "OPEN"
    assert len(state.audit_log) == 1 and state.audit_log[0].amount_paid == 50
    assert state.free_heap == [0, 1]                     # slot is free again


def test_free_stay_exits_without_payment(state, advance):
    entry.handle_arrival(state, "KDA123X")
    advance(minutes=20)
    fees.request_exit_quote(state, "KDA123X")
    barrier.process_exit(state, "KDA123X")
    assert state.audit_log[0].payment_method == "FREE"


def test_unknown_vehicle_cannot_exit(state):
    with pytest.raises(ParkingError):
        barrier.process_exit(state, "NOPE1")


def test_exit_promotes_first_queued_vehicle(state, advance):
    entry.handle_arrival(state, "AAA111")                # slot 1
    entry.handle_arrival(state, "BBB222")                # slot 2
    entry.handle_arrival(state, "CCC333")                # queued first
    entry.handle_arrival(state, "DDD444")                # queued second
    advance(minutes=10)
    fees.request_exit_quote(state, "AAA111")             # 10 min -> free
    result = barrier.process_exit(state, "AAA111")
    assert result["promoted"] == {"plate": "CCC333", "slot_number": 1,
                                  "message": "CCC333 - proceed to Slot 1."}
    assert state.vehicle_records["CCC333"].entry_time == clock.now()   # charged from promotion
    assert [e.plate for e in state.waiting_queue] == ["DDD444"]


def test_grace_period_expiry_charges_only_the_difference(state, advance):
    quoted(state, advance, minutes=100)                  # 100 min -> KES 50
    payment.pay(state, "KDA123X", "CASH")
    advance(minutes=25)                                  # lingers past 15-min grace
    with pytest.raises(ParkingError):
        barrier.process_exit(state, "KDA123X")
    quote = fees.request_exit_quote(state, "KDA123X")    # 125 min -> KES 100
    assert (quote["fee"], quote["balance_due"]) == (100, 50)
    payment.pay(state, "KDA123X", "CASH")
    barrier.process_exit(state, "KDA123X")
    assert state.audit_log[0].amount_paid == 100


# ---------------------------------------------------------------- the same car, more than once

def test_same_car_can_park_again_after_leaving(state, advance):
    """"Never charged twice" is per STAY, not per plate: a returning car starts a new stay."""
    quoted(state, advance)                               # stay 1: 45 min -> KES 50
    payment.pay(state, "KDA123X", "CASH")
    barrier.process_exit(state, "KDA123X")               # stay 1 is over and archived

    advance(minutes=5)
    assert entry.handle_arrival(state, "KDA123X")["status"] == "ENTERED"   # stay 2 starts fresh
    advance(minutes=45)
    quote = fees.request_exit_quote(state, "KDA123X")
    assert (quote["amount_paid"], quote["balance_due"]) == (0, 50)        # pays again, as it should
    assert len(state.audit_log) == 1                     # stay 1 is kept in history


def test_paid_car_that_is_called_back_pays_only_the_extra_time(state, advance):
    """Paid, then stays longer (e.g. called back inside): the grace period catches it."""
    quoted(state, advance, minutes=100)                  # 100 min -> KES 50, paid
    payment.pay(state, "KDA123X", "MPESA")
    advance(minutes=40)                                  # back inside, past the 15-minute window
    with pytest.raises(ParkingError):
        barrier.process_exit(state, "KDA123X")           # barrier stays closed
    quote = fees.request_exit_quote(state, "KDA123X")    # 140 min -> KES 100
    assert (quote["amount_paid"], quote["balance_due"]) == (50, 50)       # only the difference

# ---------------------------------------------------------------- Module 6: reports

def test_reports_and_vat(state, advance):
    quoted(state, advance, plate="AAA111")               # KES 50
    payment.pay(state, "AAA111", "CASH")
    barrier.process_exit(state, "AAA111")
    quoted(state, advance, plate="BBB222", minutes=130)  # KES 100
    payment.pay(state, "BBB222", "CARD")
    barrier.process_exit(state, "BBB222")

    summary = audit.get_summary(state, clock.now().date())
    assert (summary["total_transactions"], summary["total_revenue"]) == (2, 150)
    assert summary["day"]["vehicles"] == 2
    assert summary["total_vat"] == 20.69                 # 150 x 16 / 116
    newest = audit.recent_transactions(state, limit=1)[0]
    assert newest["plate"] == "BBB222"


def test_stays_can_be_listed_by_day_and_month(state, advance):
    """The admin can pull up one day's stays, or a whole month's (e.g. last month)."""
    def one_stay(plate):
        quoted(state, advance, plate=plate)
        payment.pay(state, plate, "CASH")
        barrier.process_exit(state, plate)

    one_stay("AAA111")                                   # leaves 25 Sep
    advance(minutes=3 * 24 * 60)
    one_stay("BBB222")                                   # leaves 28 Sep
    advance(minutes=10 * 24 * 60)
    one_stay("CCC333")                                   # leaves 8 Oct

    today = clock.now().date()
    assert [t["plate"] for t in audit.recent_transactions(state, day=today)] == ["CCC333"]
    assert [t["plate"] for t in audit.recent_transactions(state, month="2026-09")] == ["BBB222", "AAA111"]
    assert len(audit.recent_transactions(state, limit=2)) == 2


def test_audit_log_survives_restart(state, advance):
    quoted(state, advance)
    payment.pay(state, "KDA123X", "CASH")
    barrier.process_exit(state, "KDA123X")
    restarted = rebuild_from_database(state.connection)
    assert restarted.audit_log == state.audit_log


# ---------------------------------------------------------------- persistence

def test_restart_restores_everything(state, advance):
    entry.handle_arrival(state, "AAA111")
    entry.handle_arrival(state, "BBB222")
    entry.handle_arrival(state, "CCC333")                # queued
    advance(minutes=45)
    fees.request_exit_quote(state, "AAA111")

    restarted = rebuild_from_database(state.connection)  # simulate a server restart
    assert restarted.slots == state.slots
    assert restarted.vehicle_records == state.vehicle_records
    assert list(restarted.waiting_queue) == list(state.waiting_queue)
    assert restarted.free_heap == state.free_heap
