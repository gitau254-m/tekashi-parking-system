"""
daraja.py - Talks to Safaricom's M-Pesa Daraja API (sandbox).

Only two calls are needed:
  1. stk_push  - sends the "Enter your M-Pesa PIN" prompt to a phone.
  2. stk_query - asks Safaricom whether that prompt was paid, cancelled or ignored.

We CHECK the result by asking (polling) instead of waiting for Safaricom to
call us back, because a callback needs a public HTTPS address and your
laptop's localhost is not reachable from the internet.

Secrets (consumer key, secret, passkey) are read from the .env file, which
is in .gitignore and never pushed to GitHub. See .env.example.
"""

import base64
import os
import time

import httpx
from dotenv import load_dotenv

import clock
from config import BASE_DIR

# Read KEY=value lines from .env into environment variables (if the file exists).
load_dotenv(BASE_DIR / ".env")

BASE_URL = os.environ.get("MPESA_BASE_URL", "https://sandbox.safaricom.co.ke")
CONSUMER_KEY = os.environ.get("MPESA_CONSUMER_KEY", "")
CONSUMER_SECRET = os.environ.get("MPESA_CONSUMER_SECRET", "")
SHORTCODE = os.environ.get("MPESA_SHORTCODE", "174379")          # Safaricom's sandbox paybill
PASSKEY = os.environ.get("MPESA_PASSKEY", "")
CALLBACK_URL = os.environ.get("MPESA_CALLBACK_URL", "https://example.com/mpesa/callback")

# Sandbox safety: if set (default 1), the phone prompt asks for this amount
# instead of the real fee, while our system still records the real fee as paid.
# Leave it empty only when you are sure you want the full amount on the prompt.
SANDBOX_AMOUNT = os.environ.get("MPESA_SANDBOX_AMOUNT", "1")

TIMEOUT_SECONDS = 30

# ResultCodes that mean "not finished yet - ask again later". Branch on the
# CODE, never on the description text: Safaricom changes the wording.
PENDING_CODES = {"4999", "500.001.1001"}


class DarajaError(Exception):
    """Safaricom could not be reached, or refused the request."""


def is_configured() -> bool:
    """True when all credentials are present in .env - otherwise MPESA is simulated."""
    return bool(CONSUMER_KEY and CONSUMER_SECRET and PASSKEY)


# ---------------------------------------------------------------- access token
# Every Daraja call needs a token that lasts about 1 hour. We cache it in a
# module-level dict so we ask for a new one only when the old one expires.
_token_cache = {"token": None, "expires_at": 0.0}


def _get_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]
    try:
        # auth=(key, secret) makes httpx send "Authorization: Basic base64(key:secret)".
        response = httpx.get(
            f"{BASE_URL}/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(CONSUMER_KEY, CONSUMER_SECRET),
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()                  # turns 4xx/5xx into an exception
        data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DarajaError("Could not get an M-Pesa access token - check the consumer key "
                          "and secret in .env.") from error
    _token_cache["token"] = data["access_token"]
    # Refresh one minute early so a token never expires mid-request.
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 3599)) - 60
    return _token_cache["token"]


def _password_and_timestamp() -> tuple[str, str]:
    """
    Password = base64(shortcode + passkey + timestamp).
    Base64 is ENCODING, not encryption: anyone who reads the request body can
    recover the passkey. So request bodies must never be printed or logged.
    """
    timestamp = clock.now().strftime("%Y%m%d%H%M%S")      # e.g. 20260926143005 (Kenyan time)
    raw = f"{SHORTCODE}{PASSKEY}{timestamp}".encode()
    return base64.b64encode(raw).decode(), timestamp


def _post(path: str, body: dict) -> dict:
    """POST to Daraja and return the JSON answer (even for error status codes)."""
    try:
        response = httpx.post(
            f"{BASE_URL}{path}",
            json=body,
            headers={"Authorization": f"Bearer {_get_token()}"},
            timeout=TIMEOUT_SECONDS,
        )
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DarajaError("Could not reach M-Pesa. Check your internet connection.") from error


# ---------------------------------------------------------------- public calls

def stk_push(phone: str, amount: int, account_reference: str) -> str:
    """Send the PIN prompt. Returns Safaricom's CheckoutRequestID."""
    if SANDBOX_AMOUNT:
        amount = int(SANDBOX_AMOUNT)
    password, timestamp = _password_and_timestamp()
    data = _post("/mpesa/stkpush/v1/processrequest", {
        "BusinessShortCode": SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone,                    # the phone paying
        "PartyB": SHORTCODE,                # the paybill receiving
        "PhoneNumber": phone,               # the phone that gets the prompt
        "CallBackURL": CALLBACK_URL,
        "AccountReference": account_reference[:12],   # Daraja allows 12 characters
        "TransactionDesc": "Parking fee",
    })
    if str(data.get("ResponseCode")) != "0":
        message = data.get("errorMessage") or data.get("ResponseDescription") or "request refused"
        raise DarajaError(f"M-Pesa refused the request: {message}")
    return data["CheckoutRequestID"]


def stk_query(checkout_request_id: str) -> tuple[str, str]:
    """
    Ask how a prompt ended. Returns (result_code, description):
      "0" = paid, "1032" = cancelled, "1037" = no response, "1" = insufficient funds,
      "PENDING" = not finished yet (ask again in a few seconds).
    """
    password, timestamp = _password_and_timestamp()
    data = _post("/mpesa/stkpushquery/v1/query", {
        "BusinessShortCode": SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id,
    })
    code = str(data.get("ResultCode", data.get("errorCode", "")))
    description = data.get("ResultDesc") or data.get("errorMessage") or ""
    if code in PENDING_CODES or code == "":
        return "PENDING", "Waiting for the customer to enter their PIN."
    return code, description
