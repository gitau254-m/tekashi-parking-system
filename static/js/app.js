/* ==========================================================================
   Tekashi Parking - app.js
   The pages arrive already filled in by Jinja2 (server side). This file
   keeps them live: it calls the JSON API with fetch() and updates the page.
   Each page runs only its own part, chosen by <body data-page="...">.
   ========================================================================== */

"use strict";

// ---------------------------------------------------------------- helpers

/** Call the API. Always resolves to { ok, status, data } - never throws. */
async function api(method, url, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  try {
    const response = await fetch(url, options);
    let data = null;
    try { data = await response.json(); } catch { /* empty body */ }
    return { ok: response.ok, status: response.status, data };
  } catch {
    return { ok: false, status: 0, data: { detail: "Can't reach the server. Check the connection and try again." } };
  }
}

/** A readable message from an API error (FastAPI sends a string, or a list for 422). */
function errorText(result) {
  const detail = result.data && result.data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return "Please check the details you entered.";
  return "Something went wrong. Please try again.";
}

const $ = (selector, root = document) => root.querySelector(selector);

function show(el, visible = true) { if (el) el.hidden = !visible; }

function kes(amount) { return "KES " + Number(amount).toLocaleString("en-KE"); }

function formatMinutes(total) {
  if (total < 60) return `${total} min`;
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
}

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString("en-KE", { hour: "2-digit", minute: "2-digit" });
}

function showAlert(el, message) { el.textContent = message; show(el, Boolean(message)); }

/** Repaint the bays drawn by the Jinja macro, using fresh data from the API. */
function updateLot(lot, slots, showPlates = false, yours = null) {
  if (!lot) return;
  for (const slot of slots) {
    const bay = lot.querySelector(`[data-bay="${slot.slot_number}"]`);
    if (!bay) continue;
    const free = slot.status === "FREE";
    const wasFree = bay.classList.contains("is-free");
    bay.classList.toggle("is-free", free);
    bay.classList.toggle("is-taken", !free);
    bay.classList.toggle("is-yours", slot.slot_number === yours);
    $(".bay-state", bay).textContent = free ? "Free" : (showPlates && slot.plate) ? slot.plate : "Taken";
    if (wasFree !== free) Effects.bayChanged(bay, !free);    // animate only real changes
  }
}

/** Run fn now and then every `seconds`, but never while the tab is hidden. */
function every(seconds, fn) {
  fn();
  setInterval(() => { if (!document.hidden) fn(); }, seconds * 1000);
}

// ---------------------------------------------------------------- home

function initHome() {
  every(5, async () => {
    const result = await api("GET", "/api/display");
    if (!result.ok) return;
    Effects.countTo($("[data-live-free]"), result.data.free_slots);
    Effects.countTo($("[data-live-queue]"), result.data.vehicles_waiting);
  });
}

// ---------------------------------------------------------------- display (gate screen)

function initDisplay() {
  const clock = $("[data-clock]");
  const date = $("[data-date]");
  every(1, () => {
    const now = new Date();
    clock.textContent = now.toLocaleTimeString("en-KE", { hour: "2-digit", minute: "2-digit" });
    date.textContent = now.toLocaleDateString("en-KE", { weekday: "long", day: "numeric", month: "long" });
  });

  every(3, async () => {
    const result = await api("GET", "/api/display");
    if (!result.ok) return;
    const d = result.data;
    Effects.countTo($("[data-free]"), d.free_slots);
    $("[data-board-status]").classList.toggle("is-full", d.lot_full);
    $("[data-caption]").textContent = d.lot_full ? "Lot full" : (d.free_slots === 1 ? "bay free" : "bays free");
    const cars = d.vehicles_waiting === 1 ? "car" : "cars";
    $("[data-queue]").textContent = d.vehicles_waiting
      ? `${d.vehicles_waiting} ${cars} waiting. Check in to join the line.`
      : (d.lot_full ? "Check in to be first in line." : "Drive in and check in at the gate.");
    updateLot($("[data-lot]"), d.slots);
  });
}

// ---------------------------------------------------------------- entry (check in)

function initEntry() {
  const form = $("[data-entry-form]");
  const result = $("[data-entry-result]");
  const error = $("[data-entry-error]");
  const lot = $("[data-lot]");
  let yourBay = null;

  async function refreshLot() {
    const r = await api("GET", "/api/display");
    if (!r.ok) return;
    updateLot(lot, r.data.slots, false, yourBay);
    $("[data-entry-summary]").textContent =
      `${r.data.free_slots} of ${r.data.capacity} free` + (r.data.vehicles_waiting ? `, ${r.data.vehicles_waiting} waiting` : "");
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();                       // stay on the page; we send it with fetch
    const plate = form.plate.value.trim();
    showAlert(error, "");
    if (!plate) { showAlert(error, "Type your number plate first."); return; }

    const button = $("button", form);
    button.disabled = true;
    const r = await api("POST", "/api/entry", { plate });
    button.disabled = false;
    if (!r.ok) { showAlert(error, errorText(r)); show(result, false); return; }

    result.replaceChildren();                     // clear the previous result
    if (r.data.status === "ENTERED") {
      yourBay = r.data.slot_number;
      result.innerHTML = `
        <p class="muted">Go to bay</p>
        <p class="result-big is-blue"></p>
        <p class="muted"></p>`;
      result.children[1].textContent = r.data.slot_number;
      result.children[2].textContent = `${r.data.plate} checked in at ${formatTime(r.data.entry_time)}. Your time starts now.`;
      show(result);
      Effects.pop(result.children[1]);           // the bay number springs in
    } else {
      yourBay = null;
      result.innerHTML = `
        <p class="muted">The lot is full. You're number</p>
        <p class="result-big"></p>
        <p class="muted">in line. When a car leaves, the first car in line gets its bay.</p>
        <button class="btn btn-ghost" type="button">Leave the line</button>`;
      result.children[1].textContent = r.data.position;
      result.querySelector("button").addEventListener("click", async () => {
        const left = await api("POST", "/api/queue/leave", { plate: r.data.plate });
        if (!left.ok) { showAlert(error, errorText(left)); return; }
        result.innerHTML = "<p></p>";
        result.firstElementChild.textContent = `${r.data.plate} has left the line. There's no charge.`;
        refreshLot();
      });
    }
    show(result);
    form.reset();
    refreshLot();
  });

  setInterval(() => { if (!document.hidden) refreshLot(); }, 5000);
}

// ---------------------------------------------------------------- exit (fee, pay, barrier)

function initExit() {
  const error = $("[data-exit-error]");
  const quoteForm = $("[data-quote-form]");
  const quotePanel = $("[data-quote]");
  const payForm = $("[data-pay-form]");
  const waiting = $("[data-waiting]");
  const startOver = $("[data-start-over]");
  let plate = null;

  function setStep(n) {
    document.querySelectorAll("[data-step]").forEach((el) => show(el, Number(el.dataset.step) === n));
    document.querySelectorAll("[data-step-marker]").forEach((el) => {
      const step = Number(el.dataset.stepMarker);
      el.classList.toggle("is-current", step === n);
      el.classList.toggle("is-done", step < n);
    });
    show(startOver, n > 1);
  }

  function showQuote(q) {
    $("[data-quote-plate]").textContent = q.plate;
    $("[data-quote-time]").textContent = formatMinutes(q.duration_minutes);
    $("[data-quote-bay]").textContent = q.slot_number;
    $("[data-quote-fee]").textContent = q.fee === 0 ? "Free" : kes(q.fee);
    $("[data-quote-due]").textContent = q.balance_due === 0 ? "Nothing" : kes(q.balance_due);
    $("[data-quote-grace]").textContent =
      `Leave within ${q.grace_minutes} minutes of seeing this fee. After that it is worked out again.`;
    $("[data-pay-button]").textContent = `Pay ${kes(q.balance_due)}`;
    show(quotePanel);
  }

  function goToLeave(message) {
    $("[data-quote-due]").textContent = "Nothing";   // at this point the bill is settled
    $("[data-leave-title]").textContent = message;
    $("[data-leave-text]").textContent = "Drive up to the exit and open the barrier.";
    show($("[data-open-barrier]"));
    show($("[data-promoted]"), false);
    $("[data-barrier]").classList.remove("is-open");
    setStep(3);
  }

  // Step 1: plate -> quote
  quoteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAlert(error, "");
    const value = quoteForm.plate.value.trim();
    if (!value) { showAlert(error, "Type your number plate first."); return; }
    const r = await api("POST", "/api/exit/quote", { plate: value });
    if (!r.ok) { showAlert(error, errorText(r)); return; }
    plate = r.data.plate;
    showQuote(r.data);
    if (r.data.paid) {
      goToLeave(r.data.fee === 0 ? "This stay is free." : "You're all paid.");
    } else {
      show(payForm); show(waiting, false);
      setStep(2);
    }
  });

  // Step 2: choose a method and pay
  function methodChanged() {
    const mpesa = payForm.method.value === "MPESA";
    show($("[data-phone-row]"), mpesa);
    show($("[data-decline-row]"), !mpesa);
  }
  payForm.addEventListener("change", methodChanged);
  methodChanged();

  async function pollMpesa(triesLeft = 25) {
    const r = await api("GET", `/api/payment/status/${encodeURIComponent(plate)}`);
    if (r.ok && r.data.status === "PAID") { goToLeave("Payment received."); return; }
    if (r.ok && r.data.status === "FAILED") {
      show(waiting, false); show(payForm);
      showAlert(error, r.data.message);
      return;
    }
    if (!r.ok && r.status !== 502) {           // 502 = M-Pesa busy for a moment: keep trying
      show(waiting, false); show(payForm);
      showAlert(error, errorText(r));
      return;
    }
    if (triesLeft <= 1) {
      $("[data-waiting-text]").textContent =
        "No reply from M-Pesa yet. If you've entered your PIN, wait a moment and press Pay again to check.";
      show(waiting, false); show(payForm);
      return;
    }
    setTimeout(() => pollMpesa(triesLeft - 1), 4000);
  }

  payForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAlert(error, "");
    const body = {
      plate,
      method: payForm.method.value,
      phone: payForm.phone.value.trim() || null,
      simulate_failure: payForm.simulate_failure.checked,
    };
    if (body.method === "MPESA" && !body.phone) { showAlert(error, "Enter the M-Pesa phone number that will pay."); return; }

    const button = $("[data-pay-button]");
    button.disabled = true;
    const r = await api("POST", "/api/payment", body);
    button.disabled = false;

    if (r.ok && r.data.status === "PAID") { goToLeave("Payment received."); return; }
    if (r.ok && r.data.status === "PENDING") {
      $("[data-waiting-text]").textContent = `Enter your M-Pesa PIN to pay. The prompt was sent to your phone.`;
      show(payForm, false); show(waiting);
      setTimeout(() => pollMpesa(), 4000);
      return;
    }
    // 409 "already waiting" means a prompt is out: go and check it instead of failing.
    if (r.status === 409 && body.method === "MPESA" && errorText(r).includes("already waiting")) {
      show(payForm, false); show(waiting);
      pollMpesa();
      return;
    }
    showAlert(error, errorText(r));
  });

  // Step 3: open the barrier
  $("[data-open-barrier]").addEventListener("click", async (event) => {
    // Save the button now: event.currentTarget becomes null after an "await".
    const button = event.currentTarget;
    showAlert(error, "");
    button.disabled = true;
    const r = await api("POST", "/api/exit", { plate });
    button.disabled = false;
    if (!r.ok) { showAlert(error, errorText(r)); return; }

    $("[data-barrier]").classList.add("is-open");
    Effects.confetti();                          // a small celebration in flag colours
    $("[data-leave-title]").textContent = "Barrier open. Safe journey!";
    $("[data-leave-text]").textContent =
      `${formatMinutes(r.data.duration_minutes)} parked, ${r.data.amount_paid ? kes(r.data.amount_paid) + " paid" : "no charge"}.`;
    show(button, false);
    if (r.data.promoted) {
      const note = $("[data-promoted]");
      note.textContent = `${r.data.promoted.plate} from the waiting line now has bay ${r.data.promoted.slot_number}.`;
      show(note);
    }
  });

  startOver.addEventListener("click", () => {
    quoteForm.reset();
    payForm.reset();                              // back to M-Pesa, no phone, no demo decline
    methodChanged();
    if (plate) quoteForm.plate.value = plate;     // handy after "fee worked out again"
    plate = null;
    showAlert(error, "");
    show(quotePanel, false);
    setStep(1);
    quoteForm.plate.focus();
  });

  setStep(1);
}

// ---------------------------------------------------------------- admin (control room)

function initAdmin() {
  // The server fills in today's date in Kenyan time (see pages.py), so the
  // report never shows "yesterday" on a computer set to another time zone.
  const dayInput = $("[data-report-day]");

  const fig = (name) => $(`[data-fig="${name}"]`);

  /** If the session expired, go back to the sign-in page. */
  function needsLogin(r) {
    if (r.status === 401) { window.location.href = "/admin/login"; return true; }
    return false;
  }

  async function loadSummary() {
    const r = await api("GET", `/api/reports/summary?day=${dayInput.value}`);
    if (needsLogin(r) || !r.ok) return;
    const s = r.data;
    fig("day-revenue").textContent = kes(s.day.revenue);
    fig("day-label").textContent = new Date(s.day.date + "T00:00").toLocaleDateString("en-KE", { day: "numeric", month: "short" });
    fig("day-vat").textContent = kes(s.day_vat);
    fig("vat-rate").textContent = `at ${s.vat_rate_percent}%`;
    fig("day-vehicles").textContent = s.day.vehicles;
    fig("total-revenue").textContent = kes(s.total_revenue);
    fig("total-count").textContent = `${s.total_transactions} stays in total`;
  }

  async function loadSlots() {
    const r = await api("GET", "/api/admin/slots");
    if (needsLogin(r) || !r.ok) return;
    updateLot($("[data-lot]"), r.data.slots, true);
    fig("inside").textContent = r.data.occupied_slots;
    fig("waiting").textContent = r.data.vehicles_waiting;
  }

  async function loadQueue() {
    const r = await api("GET", "/api/queue");
    if (needsLogin(r) || !r.ok) return;
    const list = $("[data-queue-list]");
    list.replaceChildren();
    if (!r.data.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "Nobody is waiting.";
      list.append(li);
      return;
    }
    for (const car of r.data) {
      const li = document.createElement("li");
      li.textContent = `${car.plate}, waiting since ${formatTime(car.queued_at)}`;
      list.append(li);
    }
  }

  async function loadTransactions() {
    const r = await api("GET", "/api/reports/transactions?limit=15");
    if (needsLogin(r) || !r.ok) return;
    const body = $("[data-transactions]");
    body.replaceChildren();
    if (!r.data.length) {
      body.innerHTML = '<tr><td colspan="7" class="muted">No completed stays yet.</td></tr>';
      return;
    }
    for (const t of r.data) {
      const row = document.createElement("tr");
      const cells = [t.plate, t.slot_number, formatTime(t.entry_time), formatTime(t.barrier_time),
                     formatMinutes(t.duration_minutes), kes(t.amount_paid),
                     t.payment_method === "MPESA" ? "M-Pesa" : t.payment_method.charAt(0) + t.payment_method.slice(1).toLowerCase()];
      for (const value of cells) {
        const td = document.createElement("td");
        td.textContent = value;                    // textContent: data is never run as HTML
        row.append(td);
      }
      body.append(row);
    }
  }

  // ---- prices editor ----
  const ratesBody = $("[data-rates-body]");
  const ratesMessage = $("[data-rates-message]");

  function addRateRow(maxMinutes = "", fee = "") {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><input class="text-input" type="number" min="1" step="1" aria-label="Up to minutes" placeholder="No limit"></td>
      <td><input class="text-input" type="number" min="0" step="1" aria-label="Fee in KES" required></td>
      <td><button class="btn btn-link" type="button">Remove</button></td>`;
    const [limitInput, feeInput] = row.querySelectorAll("input");
    limitInput.value = maxMinutes ?? "";
    feeInput.value = fee;
    row.querySelector("button").addEventListener("click", () => row.remove());
    ratesBody.append(row);
  }

  async function loadRates() {
    const r = await api("GET", "/api/rates");
    if (!r.ok) return;
    ratesBody.replaceChildren();
    for (const tier of r.data) addRateRow(tier.max_minutes, tier.fee_kes);
  }

  $("[data-add-rate]").addEventListener("click", () => addRateRow());

  $("[data-rates-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const tiers = [...ratesBody.querySelectorAll("tr")].map((row) => {
      const [limitInput, feeInput] = row.querySelectorAll("input");
      return {
        max_minutes: limitInput.value === "" ? null : Number(limitInput.value),
        fee_kes: Number(feeInput.value),
      };
    });
    const r = await api("PUT", "/api/rates", { tiers });
    if (needsLogin(r)) return;
    ratesMessage.className = "form-message " + (r.ok ? "is-ok" : "is-error");
    ratesMessage.textContent = r.ok ? "Prices saved. New fees apply from the next exit." : errorText(r);
    if (r.ok) loadRates();
  });

  // ---- find a car or bay ----
  $("[data-lookup-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = $("#lookup").value.trim();
    const out = $("[data-lookup-result]");
    if (!query) return;
    const isBay = /^\d+$/.test(query);
    const r = await api("GET", isBay ? `/api/slots/${query}` : `/api/vehicles/${encodeURIComponent(query)}`);
    if (needsLogin(r)) return;
    if (!r.ok) { out.textContent = errorText(r); return; }
    const d = r.data;
    if (isBay) {
      out.textContent = d.plate ? `Bay ${d.slot_number}: ${d.plate}` : `Bay ${d.slot_number} is free.`;
    } else if (d.status === "PARKED") {
      out.textContent = `${d.plate} is in bay ${d.slot_number}, since ${formatTime(d.entry_time)}.` +
        (d.paid ? " Paid." : "");
    } else {
      out.textContent = `${d.plate} is number ${d.position} in the waiting line.`;
    }
  });

  dayInput.addEventListener("change", loadSummary);
  loadRates();
  every(10, () => { loadSummary(); loadSlots(); loadQueue(); loadTransactions(); });
}

// ---------------------------------------------------------------- start

const pages = { home: initHome, display: initDisplay, entry: initEntry, exit: initExit, admin: initAdmin };
const start = pages[document.body.dataset.page];
// The login page also says data-page="admin", but has no control-room parts.
if (start && !(document.body.dataset.page === "admin" && !$("[data-report-day]"))) start();
