"""
config.py - Fixed settings and default values for the whole system.

Two kinds of values live in this project:

1. Values in THIS file - they never change while the system is running
   (where the database file is, which timezone we use, which payment
   methods exist). Changing them means editing code and restarting.

2. Values in the DATABASE (settings and rate_tiers tables) - management
   can change them while the system runs, without touching code. This
   file only holds their DEFAULTS, which are copied into the database
   the very first time the system starts (see database.py).
"""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# File locations
# --------------------------------------------------------------------------

# BASE_DIR is the folder this file sits in (the project root).
# Path(__file__) is "the path of this file"; .resolve() makes it absolute;
# .parent goes up one level to the folder. Building paths from BASE_DIR
# means the app finds its files no matter which folder you run it from.
BASE_DIR = Path(__file__).resolve().parent

# The SQLite database is a single file in the project root.
# os.environ.get("PARKING_DB_PATH", default) lets tests point the app at a
# separate test database later, without editing this file. If the
# environment variable is not set, the normal parking.db is used.
DATABASE_PATH = Path(os.environ.get("PARKING_DB_PATH", BASE_DIR / "parking.db"))

# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

# Every timestamp in the system is Kenyan time (EAT, UTC+03:00).
# Design doc, Section 1.4: full date-and-time values, never clock-time alone.
# On Windows this needs the 'tzdata' package (installed in Step 1).
TIMEZONE = ZoneInfo("Africa/Nairobi")

# --------------------------------------------------------------------------
# Defaults copied into the database on first start-up
# --------------------------------------------------------------------------

# Stored in the 'settings' table as key/value TEXT pairs, so the values are
# strings here. They are converted to numbers when the system loads them.
DEFAULT_SETTINGS = {
    "capacity": "20",          # number of parking slots (N)
    "grace_minutes": "15",     # minutes allowed between quote and barrier
    "vat_rate_percent": "16",  # Kenya standard VAT rate; fees are VAT-inclusive
}

# The client's fee structure (design doc, Section 1.4).
# Each tuple is (max_minutes, fee_kes). None means "no upper limit" -
# the final tier that catches every stay longer than 6 hours.
DEFAULT_RATE_TIERS = [
    (30, 0),      # up to 30 minutes: free
    (120, 50),    # up to 2 hours:    KES 50
    (240, 100),   # up to 4 hours:    KES 100
    (360, 300),   # up to 6 hours:    KES 300
    (None, 500),  # over 6 hours:     KES 500
]

# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------

# Accepted payment methods (design doc, Module 4). "FREE" is not listed here
# because nobody chooses it - the system sets it for stays of 30 minutes or less.
# A tuple (round brackets) is used instead of a list because it cannot be
# changed by accident while the program runs.
PAYMENT_METHODS = ("MPESA", "CARD", "CASH")