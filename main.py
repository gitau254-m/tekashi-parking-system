"""
main.py - Entry point of the Tekashi Parking System web application.

On start-up: open the database, create tables if needed, rebuild the
in-memory state. Then serve the API routes below.

Routes only translate HTTP <-> Python. All the rules live in modules/.

Run with:
    uvicorn main:app --reload
Test every route at http://127.0.0.1:8000/docs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database import get_connection, initialise_database
from models import ParkingError
from modules import  slots
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

@app.get("/")
def home():
    """Health check."""
    return {"status": "running", "system": "Tekashi Parking System"}


# ---- Module 1: Slot Management ----
@app.get("/api/display", tags=["1. Slots"])
def display(request: Request):
    """UC1: public availability (no plates shown)."""
    return slots.get_display(get_state(request))


@app.get("/api/admin/slots", tags=["1. Slots"])
def admin_slots(request: Request):
    """Staff view of every slot, with plates."""
    return slots.get_display(get_state(request), show_plates=True)


@app.get("/api/slots/{slot_number}", tags=["1. Slots"])
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


@app.get("/api/queue", tags=["2. Entry"])
def queue_list(request: Request):
    """The waiting queue, front first."""
    return entry.get_queue(get_state(request))


@app.get("/api/vehicles/{plate}", tags=["2. Entry"])
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


@app.put("/api/rates", tags=["3. Fees"])
def rates_update(body: RatesIn, request: Request):
    """UC8: replace the rate table (validated first)."""
    # model_dump() turns each Pydantic object into a plain dict.
    return fees.update_rates(get_state(request), [tier.model_dump() for tier in body.tiers])
