/* ==========================================================================
   Tekashi Parking - effects.js
   Every animation lives here, built with Motion (motion.dev - the vanilla
   JavaScript version of Framer Motion, saved in static/vendor/motion.js).

   Rules:
   - Animations only DECORATE. Every page works without them.
   - If the visitor's device asks for reduced motion, nothing moves.
   - app.js calls Effects.* when something changes (a bay fills, a payment
     succeeds) so motion always answers something real.
   ========================================================================== */

"use strict";

const Effects = (() => {
  const M = window.Motion;                     // set by static/vendor/motion.js
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const on = Boolean(M) && !reduced;           // animate only if both are true
  const smooth = [0.22, 1, 0.36, 1];           // an "ease out" curve: fast start, soft landing
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // ---------------------------------------------------------------- page load

  /** The one orchestrated entrance: flag stripe draws, headline rises, rest fades in. */
  function intro() {
    if (!on) return;
    const { animate, stagger } = M;
    const stripe = document.querySelector("[data-flag-stripe]");
    if (stripe) animate(stripe, { scaleX: [0, 1] }, { duration: 0.9, ease: smooth });

    const lines = document.querySelectorAll("[data-hero-title] .line > span");
    if (lines.length) {
      animate(lines, { y: ["110%", "0%"] },
              { duration: 0.8, ease: smooth, delay: stagger(0.12, { startDelay: 0.2 }) });
    }
    const fades = document.querySelectorAll("[data-hero-fade]");
    if (fades.length) {
      animate(fades, { opacity: [0, 1], y: [18, 0] },
              { duration: 0.6, ease: smooth, delay: stagger(0.1, { startDelay: 0.6 }) });
    }
  }

  /** Sections slide up the first time they scroll into view. */
  function reveals() {
    if (!on) return;
    const { animate, inView, stagger } = M;
    document.querySelectorAll("[data-reveal]").forEach((el) => {
      el.style.opacity = 0;
      inView(el, () => {
        animate(el, { opacity: [0, 1], y: [34, 0] }, { duration: 0.75, ease: smooth });
      }, { amount: 0.2 });
    });
    document.querySelectorAll("[data-reveal-group]").forEach((group) => {
      const items = [...group.children];
      items.forEach((item) => { item.style.opacity = 0; });
      inView(group, () => {
        animate(items, { opacity: [0, 1], y: [44, 0] },
                { duration: 0.7, ease: smooth, delay: stagger(0.15) });
      }, { amount: 0.2 });
    });
  }

  // ---------------------------------------------------------------- the hero car

  /** The striped car drives down the lane and parks in bay 3 - on repeat. */
  async function heroScene() {
    const car = document.querySelector("[data-hero-car]");
    if (!car) return;
    const glow = document.querySelector("[data-target-glow]");
    const tag = document.querySelector("[data-scene-tag] rect");
    const tagText = document.querySelector("[data-scene-tag-text]");
    const beams = car.querySelector("[data-beams]");
    const brakes = car.querySelectorAll("[data-brake]");

    function showParked() {
      glow.style.opacity = 0;
      tag.setAttribute("fill", "#E0162B");
      tagText.textContent = "Parked in bay 3";
    }
    if (!on) { showParked(); return; }           // reduced motion: just show the result

    const { animate } = M;
    while (true) {
      // Reset: bay 3 is free, the car waits off to the right with lights on.
      tag.setAttribute("fill", "#00A859");
      tagText.textContent = "Bay 3 is free";
      animate(glow, { opacity: 0.3 }, { duration: 0.4 });
      animate(car, { x: 700, y: 280, rotate: -90, opacity: 1 }, { duration: 0 });
      animate(beams, { opacity: 1 }, { duration: 0.3 });
      await sleep(900);

      // Drive left along the lane, then swing up into the bay.
      await animate(car, {
        x: [700, 440, 366, 326, 320],
        y: [280, 280, 246, 166, 120],
        rotate: [-90, -90, -48, -8, 0],
      }, { duration: 3.6, times: [0, 0.42, 0.62, 0.84, 1], ease: ["easeIn", "linear", "linear", "easeOut"] });

      // Brake lights blink, headlights off, the sign turns red.
      animate(brakes, { opacity: [1, 0.25, 1, 0.25, 1] }, { duration: 0.9 });
      animate(beams, { opacity: 0 }, { duration: 0.6 });
      animate(glow, { opacity: 0 }, { duration: 0.6 });
      showParked();
      animate(tag.parentNode, { scale: [1, 1.12, 1] }, { duration: 0.45 });
      await sleep(3200);

      await animate(car, { opacity: 0 }, { duration: 0.6 });
      await sleep(500);
    }
  }

  // ---------------------------------------------------------------- live updates

  /** Roll a number from its current value to a new one (e.g. free bays 11 -> 10). */
  function countTo(el, value) {
    const from = Number(el.textContent) || 0;
    if (!on || from === value) { el.textContent = value; return; }
    M.animate(from, value, {
      duration: 0.6, ease: smooth,
      onUpdate: (v) => { el.textContent = Math.round(v); },
    });
  }

  /** A bay just changed: a car drives in (taken) or the bay flashes green (free). */
  function bayChanged(bay, nowTaken) {
    if (!on) return;
    if (nowTaken) {
      const car = bay.querySelector(".bay-car");
      const fromBelow = bay.closest(".lot-row--bottom") ? -40 : 40;
      M.animate(car, { y: [fromBelow, 0], opacity: [0, 1] }, { duration: 0.7, ease: smooth });
    } else {
      M.animate(bay, { backgroundColor: ["rgba(0,168,89,0.9)", "rgba(0,168,89,0.3)"] }, { duration: 0.9 });
    }
    M.animate(bay, { scale: [1, 1.06, 1] }, { duration: 0.45 });
  }

  /** A springy "pop" for big news, like the bay number after check-in. */
  function pop(el) {
    if (!on || !el) return;
    M.animate(el, { scale: [0.4, 1], opacity: [0, 1] }, { type: "spring", bounce: 0.5, duration: 0.7 });
  }

  /** Confetti in Kenyan flag colours - for the barrier opening. */
  function confetti(count = 90) {
    if (!on) return;
    const colours = ["#FFFFFF", "#E0162B", "#00A859", "#F5C400"];
    const layer = document.createElement("div");
    layer.className = "confetti";
    document.body.append(layer);
    const height = window.innerHeight;
    const pieces = [];
    for (let i = 0; i < count; i++) {
      const piece = document.createElement("i");
      piece.style.left = `${Math.random() * 100}%`;
      piece.style.background = colours[i % colours.length];
      layer.append(piece);
      pieces.push(M.animate(piece, {
        y: [0, height + 60],
        x: [0, (Math.random() - 0.5) * 220],
        rotate: [0, (Math.random() - 0.5) * 900],
      }, { duration: 1.8 + Math.random() * 1.4, delay: Math.random() * 0.4, ease: "easeIn" }));
    }
    Promise.all(pieces).then(() => layer.remove());
  }

  // Start the page-load effects straight away (this script loads at the end of <body>).
  intro();
  reveals();
  heroScene();

  // The functions app.js may call:
  return { countTo, bayChanged, pop, confetti };
})();
