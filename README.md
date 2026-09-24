# Smart Parking Management System

A web-based parking management system built for the MMU Data Structures and
Algorithms course (Task One, Phase 2). Drivers see live slot availability
before entry, vehicles are recorded on arrival, fees are calculated
automatically at exit, and the barrier opens only after payment.

The full design (modules, algorithms, data structures and database) is in
[docs/design.md](docs/design.md).

## Tech stack

Python · FastAPI · Jinja2 · SQLite

## Running locally

    python -m venv .venv
    .venv\Scripts\Activate.ps1        # Windows PowerShell
    pip install -r requirements.txt
    uvicorn main:app --reload

Then open http://127.0.0.1:8000