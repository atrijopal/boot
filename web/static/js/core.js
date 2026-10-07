"use strict";
/* Shared helpers. The page only DISPLAYS meeting state; the backend owns the pipeline, the stages and the artifacts. */
(function () {
  const App = (window.App = { views: {}, data: {}, id: null, tab: "overview", meeting: null, sig: "", busyUntil: 0, timer: null });

  App.$ = (s, r = document) => r.querySelector(s);
  App.$$ = (s, r = document) => [...r.querySelectorAll(s)];
  App.esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  App.ts = sec => { sec = Math.max(0, Math.floor(sec || 0)); const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60; return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s).padStart(2, "0"); };
  App.size = b => (b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
  App.when = iso => { try { return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); } catch (e) { return ""; } };
  App.plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

  App.STAGES = [["audio", "Audio"], ["transcription", "Transcription"], ["speakers", "Speakers"], ["refinement", "Refinement"], ["report", "Meeting report"]];
  App.STAGE_TAB = { audio: "overview", transcription: "transcript", speakers: "speakers", refinement: "refinement", report: "report" };
  App.STATE_TEXT = { waiting: "Waiting", processing: "Processing", completed: "Completed", needs_review: "Needs review", failed: "Failed" };
  App.TABS = [["overview", "Overview"], ["speakers", "Speakers"], ["transcript", "Transcript"], ["refinement", "Refinement"], ["report", "Meeting report"], ["pipeline", "Pipeline"]];

  const ico = d => `<svg class="ico" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  App.icons = {
    error: ico('<path d="M5.2 1.8h5.6l3.4 3.4v5.6l-3.4 3.4H5.2l-3.4-3.4V5.2z"/><path d="M8 4.8v3.8M8 10.9v.1"/>'),
    warning: ico('<path d="M8 2 14.5 13.5h-13z"/><path d="M8 6.5v3.2M8 11.7v.1"/>'),
    review: ico('<path d="M8 2.2 13.8 8 8 13.8 2.2 8z"/><path d="M8 5.6v2.7M8 10.2v.1"/>'),
    info: ico('<circle cx="8" cy="8" r="6"/><path d="M8 7.2v3.6M8 5v.1"/>'),
    play: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 1.2v9.6L10.5 6z" fill="currentColor"/></svg>',
    pause: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 1.5h2.6v9H2.5zM6.9 1.5h2.6v9H6.9z" fill="currentColor"/></svg>',
  };
  App.SEV = { error: "Error", warning: "Warning", review: "Review", info: "Info" };

  // ---- network
  App.api = async (url, opts = {}) => {
    let r;
    try { r = await fetch(url, opts); } catch (e) { throw new Error("The server could not be reached. Check your connection and try again."); }
    let body = null;
    try { body = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error((body && (body.detail || (body.error && body.error.message))) || `The request failed (${r.status}).`);
    return body;
  };
  App.post = (url, body) => App.api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  App.toast = (msg, kind) => {
    let box = App.$(".toasts");
    if (!box) { box = document.createElement("div"); box.className = "toasts"; box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
    const t = document.createElement("div");
    t.className = "toast" + (kind === "err" ? " err" : "");
    t.textContent = msg;
    box.appendChild(t);
    setTimeout(() => t.remove(), kind === "err" ? 7000 : 3200);
  };
  // Run a change, then show the new state. Failures are shown in plain words.
  App.act = async (fn, okMsg) => {
    try { const r = await fn(); if (okMsg) App.toast(okMsg); return r; }
    catch (e) { App.toast(e.message, "err"); return null; }
  };

  // ---- meeting state and polling
  App.stages = () => (App.meeting ? App.meeting.session.stages : {});
  App.stageState = k => (App.stages()[k] || {}).state || "waiting";
  App.busy = () => App.id && (Object.values(App.stages()).some(s => s.state === "processing") || Date.now() < App.busyUntil);
  App.signature = () => (App.meeting ? Object.entries(App.stages()).map(([k, v]) => k + ":" + v.state).join("|") + "|" + App.meeting.report_stale : "");

  App.refresh = async () => {
    if (!App.id) return;
    const id = App.id;
    let m;
    try { m = await App.api(`/meetings/${id}`); } catch (e) { if (App.id === id) { App.meeting = null; App.onMissing && App.onMissing(e); } return; }
    if (App.id !== id) return;
    const before = App.sig;
    App.meeting = m;
    App.sig = App.signature();
    if (App.sig !== before) { App.data = {}; App.onChange && App.onChange(true); } else { App.onChange && App.onChange(false); }
  };
  App.poll = () => {
    clearTimeout(App.timer);
    if (!App.id) return;
    App.timer = setTimeout(async () => { await App.refresh(); App.poll(); }, App.busy() ? 1100 : 4000);
  };
  App.kick = (seconds = 25) => { App.busyUntil = Date.now() + seconds * 1000; App.refresh(); App.poll(); };

  // ---- the recording: one audio element for the open meeting; any timestamp can seek it
  const P = (App.player = { el: null, onTime: [], ready: false });
  P.attach = id => {
    P.detach();
    P.el = new Audio();
    P.el.preload = "metadata";
    P.el.src = `/meetings/${id}/audio`;
    P.el.addEventListener("loadedmetadata", () => { P.ready = true; P.update(); });
    P.el.addEventListener("timeupdate", () => { P.update(); P.onTime.forEach(f => f(P.el.currentTime)); });
    P.el.addEventListener("play", () => P.update());
    P.el.addEventListener("pause", () => P.update());
    P.el.addEventListener("error", () => { P.ready = false; P.update(); });
  };
  P.detach = () => { if (P.el) { P.el.pause(); P.el.removeAttribute("src"); P.el.load(); } P.el = null; P.ready = false; P.onTime = []; };
  P.seek = sec => { if (!P.el) return; P.el.currentTime = Math.max(0, sec); P.el.play().catch(() => App.toast("The browser could not play this recording.", "err")); };
  P.toggle = () => { if (!P.el) return; P.el.paused ? P.el.play().catch(() => {}) : P.el.pause(); };
  P.update = () => {
    const bar = App.$(".player");
    if (!bar || !P.el) return;
    const d = P.el.duration || 0, t = P.el.currentTime || 0;
    App.$(".player .time", bar).textContent = `${App.ts(t)} / ${App.ts(d)}`;
    const r = App.$(".player input[type=range]", bar);
    if (r && document.activeElement !== r) { r.max = d || 0; r.value = t; }
    App.$(".pp", bar).innerHTML = P.el.paused ? App.icons.play : App.icons.pause;
    bar.classList.toggle("hide", !P.ready);
  };
  App.playerHtml = () => `<div class="player hide" role="region" aria-label="Recording"><div class="in">
      <button class="pp" aria-label="Play or pause">${App.icons.play}</button><span class="time">0:00 / 0:00</span>
      <input type="range" min="0" max="0" step="0.1" value="0" aria-label="Position in the recording">
      <span class="row"><button class="btn sm quiet" data-rate="1">1×</button><button class="btn sm quiet" data-rate="1.5">1.5×</button><button class="btn sm quiet" data-rate="2">2×</button></span></div></div>`;
  App.bindPlayer = () => {
    const bar = App.$(".player");
    if (!bar) return;
    App.$(".pp", bar).onclick = P.toggle;
    const r = App.$("input[type=range]", bar);
    r.oninput = () => { if (P.el) P.el.currentTime = +r.value; };
    App.$$("[data-rate]", bar).forEach(b => { b.onclick = () => { if (P.el) P.el.playbackRate = +b.dataset.rate; App.$$("[data-rate]", bar).forEach(x => x.classList.toggle("on", x === b)); }; });
    P.update();
  };
  // any element with data-seek="12.5" jumps the recording there
  document.addEventListener("click", e => {
    const t = e.target.closest("[data-seek]");
    if (t && App.id) { e.preventDefault(); P.seek(parseFloat(t.dataset.seek)); }
  });
})();
