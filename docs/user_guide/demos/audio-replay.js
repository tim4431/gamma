// Gamma user guide: the audio replay demo (audio-replay.html).
// A simulated lecture recording, two segments with one pause between them,
// replays the page's handwriting and the notes. The page and the notes are
// plain markup, so with scripts blocked the page shows the finished picture;
// this file only adds the player. All code lives here (script-src 'self').
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const page = $("page"), player = $("player");
  if (!page || !player) return;

  const LEAD_IN = 3000;   // a tap plays from this long before the moment
  const parseT = (s) => { const [h, m, sec] = s.split(":").map(Number); return ((h * 60 + m) * 60 + sec) * 1000; };
  const pad = (n) => String(n).padStart(2, "0");
  const fmtT = (ms) => { const s = Math.floor(Math.max(0, ms) / 1000); return `${Math.floor(s / 60)}:${pad(s % 60)}`; };
  const fmtWall = (w) => { const s = Math.floor(w / 1000); return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`; };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.2v13.6L18.6 12z"/></svg>';
  const PAUSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.8 5h3.6v14H6.8zM13.6 5h3.6v14h-3.6z"/></svg>';

  // ---- The recording's clock ----
  // Each segment is audio that began at a wall-clock time and ran for `dur`.
  // The recording's own time (the playhead) is the segments end to end, so the
  // pause takes no time; anything written during it maps to where it resumed.
  const SEGMENTS = [{ started: parseT("10:03:00"), dur: 40000 }, { started: parseT("10:04:10"), dur: 35000 }];
  let total = 0;
  for (const s of SEGMENTS) { s.at = total; total += s.dur; }
  const clock = {
    duration: total,
    toWall(t) {
      let seg = SEGMENTS[0];
      for (const s of SEGMENTS) if (t >= s.at) seg = s;
      return seg.started + Math.min(t - seg.at, seg.dur);
    },
    toT(wall) {
      for (const s of SEGMENTS) {
        if (wall < s.started) return s.at;
        if (wall <= s.started + s.dur) return s.at + (wall - s.started);
      }
      return null;
    },
  };
  const JOIN = SEGMENTS[1].at, PAUSED = SEGMENTS[1].started - (SEGMENTS[0].started + SEGMENTS[0].dur);
  const inPause = (wall) => wall > SEGMENTS[0].started + SEGMENTS[0].dur && wall < SEGMENTS[1].started;

  // ---- What was written, and when ----
  // Every stroke carries the time it began (data-t0) and how long the pen took;
  // every note carries the time it was typed. One event per word, shape or note.
  const strokes = [], events = [];
  for (const g of page.querySelectorAll("#ink g[data-what]")) {
    const ev = { kind: "ink", what: g.dataset.what, color: g.parentNode.getAttribute("stroke"), strokes: [], wall: Infinity };
    for (const live of g.querySelectorAll("path[data-t0]")) {
      const len = live.getTotalLength();
      const s = { t0: parseT(live.dataset.t0), dur: Number(live.dataset.dur), len, live, ghost: live.cloneNode(false), hit: live.cloneNode(false), state: "" };
      s.ghost.setAttribute("class", "ghost");
      s.hit.setAttribute("class", "hit");
      live.setAttribute("class", "live");
      live.style.strokeDasharray = `${len} ${len}`;
      live.before(s.ghost);
      live.after(s.hit);
      s.hit.addEventListener("click", () => jumpTo(ev));
      ev.wall = Math.min(ev.wall, s.t0);
      ev.strokes.push(s);
      strokes.push(s);
    }
    events.push(ev);
  }
  const notes = [...document.querySelectorAll("#notes li[data-t0]")].map((li) => {
    const ev = { kind: "note", what: li.dataset.what, li, wall: parseT(li.dataset.t0), state: "" };
    const glyph = document.createElement("button");
    glyph.type = "button";
    glyph.className = "glyph";
    glyph.tabIndex = -1;
    glyph.innerHTML = '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2 1v8l7-4z"/></svg><span></span>';
    li.prepend(glyph);
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    li.addEventListener("click", () => jumpTo(ev));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); jumpTo(ev); } });
    events.push(ev);
    return ev;
  });
  for (const ev of events) { ev.t = clock.toT(ev.wall); ev.paused = inPause(ev.wall); }
  events.sort((a, b) => a.t - b.t || a.wall - b.wall);
  for (const n of notes) {
    n.li.querySelector(".glyph span").textContent = fmtT(n.t);
    n.li.setAttribute("aria-label", `${n.li.textContent.replace(/^\d+:\d+/, "").trim()}. Play from ${fmtT(Math.max(0, n.t - LEAD_IN))}`);
  }

  // ---- The player: one playhead, one wall-clock instant, everything drawn as of it ----
  let t = 0, playing = false, rate = 1, mode = "spotlight", raf = 0, last = 0, message = null, nowNote = null;
  const RATES = [1, 1.25, 1.5, 2, 0.7];
  function frame(now) {
    t = Math.min(clock.duration, t + Math.min(250, now - last) * rate);
    last = now;
    if (t >= clock.duration) { playing = false; raf = 0; }
    draw();
    if (playing) raf = requestAnimationFrame(frame);
  }
  function play() {
    if (playing) return;
    if (t >= clock.duration) t = 0;
    playing = true;
    last = performance.now();
    raf = requestAnimationFrame(frame);
    draw();
  }
  function pause() { playing = false; cancelAnimationFrame(raf); raf = 0; draw(); }
  function seek(to) { t = clamp(to, 0, clock.duration); last = performance.now(); draw(); }
  function jumpTo(ev) {
    seek(Math.max(0, ev.t - LEAD_IN));
    message = { text: `Jumped to ${fmtT(t)}, 3 s before ${ev.what}${ev.paused ? " (while paused, so it plays where the recording resumed)" : ""}`, until: performance.now() + 2800 };
    flash(ev);
    play();
  }
  function flash(ev) {
    if (ev.kind === "note") {
      ev.li.classList.remove("flashing");
      void ev.li.offsetWidth;
      ev.li.classList.add("flashing");
      return;
    }
    const layer = $("flash");
    for (const s of ev.strokes) {
      const p = s.hit.cloneNode(false);
      p.removeAttribute("class");
      p.setAttribute("stroke", ev.color);
      layer.append(p);
      setTimeout(() => p.remove(), 1200);
    }
  }

  // The caption reads the moment as a sentence.
  function sentence(wall) {
    if (message && performance.now() < message.until) return message.text;
    if (!playing && t === 0) return "Press play, or tap anything you wrote to hear that moment.";
    if (t >= clock.duration) return "End of the recording. Tap anything you wrote to hear it again.";
    const time = fmtWall(wall);
    let ev = null;
    for (const e of events) { if (e.t <= t) ev = e; else break; }
    if (ev && t - ev.t < 6000) return ev.paused ? `${time} · ${ev.what} while paused; it plays here, where the recording resumed` : `${time} · ${ev.what} here`;
    if (t >= JOIN && t - JOIN < 6000) return `${time} · the recording resumed after a ${PAUSED / 1000} s pause`;
    return `${time} · recording`;
  }

  const scrub = $("scrub"), head = $("head"), caption = $("caption"), playBtn = $("play"), playedRect = $("playedRect");
  let lastPlaying = null, lastCaption = "";
  function draw() {
    const wall = clock.toWall(t);
    // The page: strokes reached are drawn, the one being written up to the pen,
    // the rest faded (Spotlight) or hidden (Reveal); Static changes nothing.
    for (const s of strokes) {
      let state = "done", f = 1;
      if (mode !== "static") {
        if (wall < s.t0) state = "ahead";
        else if (wall < s.t0 + s.dur) { state = "writing"; const u = (wall - s.t0) / s.dur; f = u - Math.sin(2 * Math.PI * u) / (2 * Math.PI); }
      }
      if (state !== s.state) {
        for (const el of [s.ghost, s.live, s.hit]) el.setAttribute("data-s", state);
        s.state = state;
        if (state === "done") s.live.style.strokeDashoffset = "0";
      }
      if (state === "writing") s.live.style.strokeDashoffset = (s.len * (1 - f)).toFixed(2);
    }
    // The notes: shaded until the playhead reaches the moment they were typed.
    let now = null;
    for (const n of notes) {
      const state = mode === "static" ? "plain" : wall >= n.wall ? "reached" : "pending";
      if (state !== n.state) { n.li.setAttribute("data-s", state); n.state = state; }
      if (state === "reached") now = n;
    }
    if (now !== nowNote) {
      if (nowNote) nowNote.li.classList.remove("now");
      if (now) now.li.classList.add("now");
      nowNote = now;
    }
    // The bar.
    const pct = (t / clock.duration) * 100;
    head.style.left = pct.toFixed(2) + "%";
    playedRect.setAttribute("width", ((pct / 100) * WAVE_BINS).toFixed(2));
    $("now").textContent = fmtT(t);
    scrub.setAttribute("aria-valuenow", String(Math.floor(t / 1000)));
    scrub.setAttribute("aria-valuetext", `${fmtT(t)} of ${fmtT(clock.duration)}`);
    if (playing !== lastPlaying) {
      for (const b of [playBtn, recPlay]) {
        b.innerHTML = playing ? PAUSE : PLAY;
        b.setAttribute("aria-label", playing ? "Pause" : "Play");
      }
      lastPlaying = playing;
    }
    const text = sentence(wall);
    if (text !== lastCaption) { caption.textContent = text; lastCaption = text; }
  }

  // ---- Build the bar: a pretend waveform, the pause join, a tick per thing written ----
  const WAVE_BINS = Math.ceil(clock.duration / 500);
  let seed = 48000, d = "";
  const rnd = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
  for (let i = 0; i < WAVE_BINS; i++) {
    const at = i * 500;
    let a = (0.3 + 0.7 * rnd()) * (0.75 + 0.25 * Math.sin(at / 2300));
    for (const s of SEGMENTS) { const edge = Math.min(at - s.at, s.at + s.dur - at); if (edge >= 0 && edge < 800) a *= 0.25 + 0.75 * edge / 800; }
    const h = clamp(a, 0.06, 1) * 13;
    d += `M${(i + 0.2).toFixed(1)} ${(14 - h).toFixed(2)}h0.6v${(2 * h).toFixed(2)}h-0.6z`;
  }
  $("wave").setAttribute("viewBox", `0 0 ${WAVE_BINS} 28`);
  $("waveBase").setAttribute("d", d);
  $("waveDone").setAttribute("d", d);
  $("join").style.left = ((JOIN / clock.duration) * 100).toFixed(2) + "%";
  $("joinLabel").textContent = `paused ${PAUSED / 1000} s`;
  for (const ev of events) {
    const i = document.createElement("i");
    i.className = ev.kind === "note" ? "tick note" : "tick";
    i.style.left = ((ev.t / clock.duration) * 100).toFixed(2) + "%";
    if (ev.kind === "ink") i.style.background = ev.color;
    $("ticks").append(i);
  }
  $("total").textContent = fmtT(clock.duration);
  scrub.setAttribute("aria-valuemax", String(Math.floor(clock.duration / 1000)));

  // The recording's row in the notes gets a play button.
  const recPlay = document.createElement("button");
  recPlay.type = "button";
  recPlay.className = "recplay";
  $("recdot").replaceWith(recPlay);

  // ---- Controls ----
  const toggle = () => (playing ? pause() : play());
  playBtn.addEventListener("click", toggle);
  recPlay.addEventListener("click", toggle);
  $("back").addEventListener("click", () => seek(t - 10000));
  $("fwd").addEventListener("click", () => seek(t + 10000));
  $("speed").addEventListener("click", (e) => {
    rate = RATES[(RATES.indexOf(rate) + 1) % RATES.length];
    e.currentTarget.textContent = `Speed ${rate}×`;
  });
  $("modes").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-mode]");
    if (!b) return;
    mode = b.dataset.mode;
    page.classList.remove("m-spotlight", "m-reveal", "m-static");
    page.classList.add("m-" + mode);
    for (const x of $("modes").querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
    draw();
  });
  let dragging = false;
  const seekX = (x) => { const r = scrub.getBoundingClientRect(); seek(clamp((x - r.left) / r.width, 0, 1) * clock.duration); };
  scrub.addEventListener("pointerdown", (e) => { dragging = true; try { scrub.setPointerCapture(e.pointerId); } catch { /* not capturable */ } message = null; seekX(e.clientX); });
  scrub.addEventListener("pointermove", (e) => { if (dragging) seekX(e.clientX); });
  scrub.addEventListener("pointerup", () => { dragging = false; });
  scrub.addEventListener("pointercancel", () => { dragging = false; });
  scrub.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 10000 : 1000;
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") seek(t - step);
    else if (e.key === "ArrowRight" || e.key === "ArrowUp") seek(t + step);
    else if (e.key === "Home") seek(0);
    else if (e.key === "End") seek(clock.duration);
    else if (e.key === " ") toggle();
    else return;
    e.preventDefault();
  });
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = e.target.tagName, role = e.target.getAttribute && e.target.getAttribute("role");
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON" || role === "button" || role === "slider") return;
    if (e.key === " ") { e.preventDefault(); toggle(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); seek(t - 10000); }
    else if (e.key === "ArrowRight") { e.preventDefault(); seek(t + 10000); }
  });

  document.documentElement.classList.add("js");
  page.classList.add("m-spotlight");
  player.hidden = false;
  draw();
})();
