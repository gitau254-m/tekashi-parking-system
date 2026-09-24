"""
main.py - Entry point of the Smart Parking Management System web application.

Running this file with uvicorn starts the web server. For now it only
proves the setup works; later steps connect the six modules and the pages.

Run with:
    uvicorn main:app --reload
"""

from fastapi import FastAPI

# 'app' is the web application object. Every page and API route is attached to it.
# The title appears on the automatic documentation page at /docs.
app = FastAPI(title="Smart Parking Management System")


@app.get("/")
def home():
    """
    Health check route.

    The @app.get("/") line above is a decorator: it tells FastAPI
    "when a browser sends a GET request to '/', run this function".
    Whatever the function returns (a dict here) is sent back as JSON.
    """
    return {"status": "running", "system": "Smart Parking Management System"}