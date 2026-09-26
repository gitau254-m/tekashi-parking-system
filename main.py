"""
main.py - Entry point of the Tekashi Parking System web application.

On start-up this file:
  1. opens the SQLite database and creates the tables if needed (database.py),
  2. rebuilds every in-memory data structure from it (state.py),
  3. keeps that ParkingState on the app so every route can reach it.

Run with:
    uvicorn main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from database import get_connection, initialise_database
from state import rebuild_from_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Code that runs once when the server starts, and once when it stops.

    Everything BEFORE 'yield' runs at start-up.
    At 'yield' the server starts serving requests and waits there.
    Everything AFTER 'yield' runs at shut-down (Ctrl+C).
    """
    # --- start-up ---
    connection = get_connection()
    initialise_database(connection)                      # tables + defaults
    app.state.parking = rebuild_from_database(connection)  # memory from database

    yield  # the app runs here, serving requests

    # --- shut-down ---
    connection.close()


# 'app' is the web application object. Every route is attached to it.
# lifespan=lifespan tells FastAPI to run the start-up/shut-down code above.
app = FastAPI(title="Tekashi Parking System", lifespan=lifespan)


@app.get("/")
def home():
    """Health check: proves the server is running."""
    return {"status": "running", "system": "Tekashi Parking System"}


@app.get("/api/status")
def status(request: Request):
    """
    A summary of the in-memory state - used to check that start-up worked.

    request.app.state.parking is the ParkingState built at start-up.
    The lock is taken even for reading, so the numbers come from one
    consistent moment and never from the middle of another request's change.
    """
    state = request.app.state.parking
    with state.lock:
        return {
            "capacity": state.capacity,
            "free_slots": len(state.free_heap),
            "occupied_slots": len(state.vehicle_records),
            "vehicles_waiting": len(state.waiting_queue),
            "rate_tiers_loaded": len(state.rate_table),
            "transactions_logged": len(state.audit_log),
            "grace_minutes": state.grace_minutes,
            "vat_rate_percent": state.vat_rate_percent,
        }