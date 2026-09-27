"""
pages.py - The web pages (Jinja2 templates) of the Tekashi Parking System.

The pages are rendered on the SERVER with real data (Jinja2 fills the
{{ ... }} and {% ... %} parts of templates/*.html), then small JavaScript in
static/js/app.js keeps them live by calling the JSON API in main.py.

An APIRouter is a group of routes kept in its own file; main.py plugs it in
with app.include_router(router). This keeps main.py short.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates

import auth
import clock
from config import BASE_DIR
from modules import fees, slots

# include_in_schema=False hides these HTML pages from the /docs API list.
router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def describe_duration(minutes: int) -> str:
    """120 -> '2 hours', 30 -> '30 minutes', 90 -> '1.5 hours'."""
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes / 60
    text = f"{hours:g}"                     # :g drops a trailing .0  (2.0 -> "2")
    return f"{text} hour" if hours == 1 else f"{text} hours"


def describe_rates(rate_table: list[dict]) -> list[dict]:
    """Turn the rate table into plain-language rows for the pages."""
    rows = []
    previous_limit = 0
    for tier in rate_table:
        if tier["max_minutes"] is None:
            label = f"Over {describe_duration(previous_limit)}"
        else:
            label = f"Up to {describe_duration(tier['max_minutes'])}"
            previous_limit = tier["max_minutes"]
        rows.append({"label": label, "fee": tier["fee_kes"]})
    return rows


def render(request: Request, template: str, page: str, **context) -> HTMLResponse:
    """Render a template with the values every page needs (current page, admin flag)."""
    state = request.app.state.parking
    context.update(page=page, is_admin=auth.is_admin(request),
                   rates=describe_rates(fees.get_rates(state)))
    return templates.TemplateResponse(request, template, context)


# ---------------------------------------------------------------- public pages
@router.get("/favicon.ico")
def favicon():
       """
       Browsers ask for /favicon.ico on their own, even without a <link> tag.
       Answering it (instead of a 404) keeps the server log clean and gives
       older browsers the icon too.
       """
       return FileResponse(BASE_DIR / "static" / "img" / "favicon.ico")
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Landing page: hero, live availability, how it works, prices."""
    return render(request, "home.html", "home",
                  display=slots.get_display(request.app.state.parking))


@router.get("/display", response_class=HTMLResponse)
def display(request: Request):
    """UC1: the screen at the gate - live bays, auto-refreshing."""
    return render(request, "display.html", "display",
                  display=slots.get_display(request.app.state.parking))


@router.get("/entry", response_class=HTMLResponse)
def entry_page(request: Request):
    """UC2-UC4: check in at the gate."""
    return render(request, "entry.html", "entry",
                  display=slots.get_display(request.app.state.parking))


@router.get("/exit", response_class=HTMLResponse)
def exit_page(request: Request):
    """UC5-UC7: see your fee, pay, open the barrier."""
    return render(request, "exit.html", "exit")


# ---------------------------------------------------------------- admin

@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    """UC8-UC10: the control room. Not logged in -> sent to the login page."""
    if not auth.is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    return render(request, "admin.html", "admin",
                  display=slots.get_display(request.app.state.parking, show_plates=True),
                  today=clock.now().date().isoformat())       # Kenyan date, e.g. 2026-09-27


@router.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    if auth.is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return render(request, "login.html", "admin", error=None,
                  password_is_set=auth.password_is_set())


@router.post("/admin/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form("")):
    """Form(...) reads a field from an HTML <form> post (needs python-multipart)."""
    if not auth.check_password(password):
        response = render(request, "login.html", "admin",
                          error="That password is not correct.",
                          password_is_set=auth.password_is_set())
        response.status_code = 401
        return response
    # 303 See Other: after a POST, tell the browser to GET the next page
    # (so refreshing does not re-send the password).
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        auth.SESSION_COOKIE, auth.create_session(),
        max_age=auth.SESSION_SECONDS,
        httponly=True,        # JavaScript cannot read it - protects against stolen cookies
        samesite="lax",       # not sent on other sites' form posts - protects against CSRF
    )
    return response


@router.post("/admin/logout")
def logout(request: Request):
    auth.end_session(request.cookies.get(auth.SESSION_COOKIE))
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE)
    return response
