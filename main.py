"""
main.py - Entry point of the Tekashi Parking System web application.

On start-up: open the database, create tables if needed, rebuild the
in-memory state. Then serve:
  - the web pages (pages.py, Jinja2 templates in templates/),
  - the static files (static/: CSS, JavaScript, images),
  - the JSON API below (used by the pages' JavaScript, testable at /docs).

Routes only translate HTTP <-> Python. All the rules live in modules/.
Admin-only routes carry dependencies=[Depends(auth.require_admin)].

Run with:
    uvicorn main:app --reload
Test every route at http://127.0.0.1:8000/docs
"""

from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
import clock
import pages
from config import BASE_DIR
from database import get_connection, initialise_database
from models import ParkingError
from modules import audit, barrier, entry, fees, payment, slots
from state import rebuild_from_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Before 'yield' = start-up. After 'yield' = shut-down."""
    connection = get_connection()
    initialise_database(connection)
    app.state.parking = rebuild_from_database(connection)
    yield
    connection.close()


app = FastAPI(title="Tekashi Parking System", lifespan=lifespan)

# Serve everything in static/ at /static/... (e.g. /static/css/style.css).
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Plug in the HTML pages from pages.py.
app.include_router(pages.router)

# Shorthand for "only a logged-in admin may call this route".
ADMIN_ONLY = [Depends(auth.require_admin)]


@app.exception_handler(ParkingError)
def handle_parking_error(request: Request, error: ParkingError):
    """Any ParkingError raised in a module becomes a clean JSON error response."""
    return JSONResponse(status_code=error.status_code, content={"detail": error.message})


def get_state(request: Request):
    """The ParkingState built at start-up."""
    return request.app.state.parking



# --------------------------------------------------------------------------
# Request bodies (Pydantic models)
# --------------------------------------------------------------------------
# A Pydantic model describes the JSON a route expects. FastAPI checks every
# request against it automatically: missing or wrong-type fields are
# rejected with a 422 error before our code even runs.

class PlateIn(BaseModel):
    plate: str = Field(examples=["KDA 123X"])


class PaymentIn(BaseModel):
    plate: str = Field(examples=["KDA 123X"])
    method: str = Field(examples=["MPESA"])
    phone: str | None = Field(default=None, examples=["0712345678"])   # needed for real M-Pesa
    simulate_failure: bool = False                                      # demo the failure path


class RateTierIn(BaseModel):
    max_minutes: int | None = None      # None = open-ended final tier
    fee_kes: int


class RatesIn(BaseModel):
    tiers: list[RateTierIn]


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
# Plain "def" (not "async def"): FastAPI runs these on a thread pool, which
# is what our threading.Lock is designed for.

@app.get("/api/health")
def health():
    """Health check (the home page "/" is now a web page)."""
    return {"status": "running", "system": "Tekashi Parking System"}


# ---- Module 1: Slot Management ----
@app.get("/api/display", tags=["1. Slots"])
def display(request: Request):
    """UC1: public availability (no plates shown)."""
    return slots.get_display(get_state(request))


@app.get("/api/admin/slots", tags=["1. Slots"], dependencies=ADMIN_ONLY)
def admin_slots(request: Request):
    """Staff view of every slot, with plates."""
    return slots.get_display(get_state(request), show_plates=True)


@app.get("/api/slots/{slot_number}", tags=["1. Slots"], dependencies=ADMIN_ONLY)
def slot_lookup(slot_number: int, request: Request):
    """UC9: who is in this slot?"""
    return slots.who_is_in(get_state(request), slot_number)


# ---- Module 2: Vehicle Entry ----
@app.post("/api/entry", tags=["2. Entry"])
def vehicle_entry(body: PlateIn, request: Request):
    """UC2 / UC3: enter, or join the queue if full."""
    return entry.handle_arrival(get_state(request), body.plate)


@app.post("/api/queue/leave", tags=["2. Entry"])
def queue_leave(body: PlateIn, request: Request):
    """UC4: leave the waiting queue."""
    return entry.leave_queue(get_state(request), body.plate)


@app.get("/api/queue", tags=["2. Entry"], dependencies=ADMIN_ONLY)
def queue_list(request: Request):
    """The waiting queue, front first."""
    return entry.get_queue(get_state(request))


@app.get("/api/vehicles/{plate}", tags=["2. Entry"], dependencies=ADMIN_ONLY)
def vehicle_lookup(plate: str, request: Request):
    """UC9: where is this vehicle?"""
    return entry.find_vehicle(get_state(request), plate)


# ---- Module 3: Duration & Fee ----
@app.post("/api/exit/quote", tags=["3. Fees"])
def exit_quote(body: PlateIn, request: Request):
    """UC5: duration and fee."""
    return fees.request_exit_quote(get_state(request), body.plate)


@app.get("/api/rates", tags=["3. Fees"])
def rates_get(request: Request):
    """Current rate table."""
    return fees.get_rates(get_state(request))


@app.put("/api/rates", tags=["3. Fees"], dependencies=ADMIN_ONLY)
def rates_update(body: RatesIn, request: Request):
    """UC8: replace the rate table (validated first)."""
    # model_dump() turns each Pydantic object into a plain dict.
    return fees.update_rates(get_state(request), [tier.model_dump() for tier in body.tiers])


# ---- Module 4: Payment ----
@app.post("/api/payment", tags=["4. Payment"])
def pay(body: PaymentIn, request: Request):
    """UC6: MPESA sends a real sandbox prompt (if .env has Daraja keys); CARD/CASH are simulated."""
    return payment.pay(get_state(request), body.plate, body.method, body.phone,
                       body.simulate_failure)


@app.get("/api/payment/status/{plate}", tags=["4. Payment"])
def payment_status(plate: str, request: Request):
    """Check an M-Pesa prompt: PENDING, PAID or FAILED. Poll every ~5 seconds."""
    return payment.check_mpesa_payment(get_state(request), plate)


# ---- Module 5: Barrier Control ----
@app.post("/api/exit", tags=["5. Barrier"])
def vehicle_exit(body: PlateIn, request: Request):
    """UC7: open the barrier if known, paid and within the grace period."""
    return barrier.process_exit(get_state(request), body.plate)


# ---- Module 6: Reporting / Audit ----
@app.get("/api/reports/summary", tags=["6. Reports"], dependencies=ADMIN_ONLY)
def report_summary(request: Request, day: date | None = None):
    """UC10: totals, VAT, and one day's figures (default: today). day format: YYYY-MM-DD."""
    return audit.get_summary(get_state(request), day or clock.now().date())


@app.get("/api/reports/transactions", tags=["6. Reports"], dependencies=ADMIN_ONLY)
def report_transactions(request: Request, limit: int = 20):
    """UC10: latest completed stays, newest first."""
    return audit.recent_transactions(get_state(request), limit)
