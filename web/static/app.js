"use strict";
/* Boot, routing and the shell. Hash routes: #/ (new meeting) and #/m/<id>[/<section>][?seg=<line>]. */
(function () {
  const { $, esc, api } = App;
  const main = () => $("#main");

  function topbar() {
    document.body.insertAdjacentHTML("afterbegin", `<header class="topbar"><a class="brand" href="#/"><span class="mark"></span>Meeting <i>Record</i></a><span class="grow"></span>
      <select class="switcher" id="switcher" aria-label="Open a meeting"><option value="">Open a meeting…</option></select><a class="btn primary" href="#/">New meeting</a></header><main id="main"></main>`);
    $("#switcher").onchange = e => { if (e.target.value) location.hash = `#/m/${e.target.value}`; };
  }
  async function fillSwitcher() {
    try {
      const list = await api("/meetings");
      $("#switcher").innerHTML = `<option value="">Open a meeting…</option>` + list.map(m => `<option value="${esc(m.id)}" ${m.id === App.id ? "selected" : ""}>${esc(m.filename)} · ${esc(m.date)}${m.example ? " (example)" : ""}</option>`).join("");
    } catch (e) { /* optional */ }
  }

  App.renderShell = () => { const s = $("#shell"); if (s && App.meeting) s.innerHTML = App.shellHtml(); bindShell(); };
  function bindShell() { const b = $("#exportBtn"); if (b) b.onclick = App.openExports; }

  App.renderView = async () => {
    const root = $("#view");
    if (!root || !App.meeting) return;
    const tab = App.tab, v = App.views[tab];
    if (!App.tabAvailable(tab)) {
      root.innerHTML = `<div class="empty"><b>Not available yet.</b>This section opens once the earlier steps have finished. Open the <a href="${App.hash("overview")}">overview</a> to see where the meeting is.</div>`;
      return;
    }
    const scroll = window.scrollY, id = App.id;
    if (!App.data[tab]) {
      try { App.data[tab] = await v.load(); } catch (e) { if (App.id === id) root.innerHTML = `<div class="notice err"><b style="font-weight:600">This section could not be loaded.</b><div>${esc(e.message)}</div></div>`; return; }
    }
    if (App.id !== id || App.tab !== tab) return;
    root.innerHTML = v.render(App.data[tab]);
    v.bind && v.bind(root, App.data[tab]);
    App.renderShell();
    window.scrollTo(0, scroll);
  };

  App.onChange = changed => { App.renderShell(); if (changed) App.renderView(); };
  App.onMissing = e => { main().innerHTML = `<div class="empty" style="margin-top:40px"><b>This meeting could not be opened.</b>${esc(e.message)} <a href="#/">Return to the meeting list.</a></div>`; };

  async function route() {
    const h = location.hash || "#/";
    const m = h.match(/^#\/m\/([\w-]+)(?:\/(\w+))?(?:\?(.*))?$/);
    App.player.detach();
    document.querySelectorAll(".player").forEach(p => p.remove());
    clearTimeout(App.timer);
    if (!m) {
      App.id = null; App.meeting = null; App.data = {}; App.sig = "";
      main().innerHTML = "";
      await App.views.home.render(main());
      fillSwitcher();
      return;
    }
    const [, id, tab, q] = m;
    App.query = Object.fromEntries(new URLSearchParams(q || ""));
    const same = App.id === id && App.meeting;
    App.tab = App.views[tab] ? tab : "overview";
    if (!same) {
      App.id = id; App.meeting = null; App.data = {}; App.sig = "";
      main().innerHTML = `<div id="shell"></div><div id="view" class="view"></div>`;
      document.body.insertAdjacentHTML("beforeend", App.playerHtml());
      App.bindPlayer();
      App.player.attach(id);
      await App.refresh();
      if (!App.meeting) return;
      fillSwitcher();
      App.renderShell();
      await App.renderView();
      App.poll();
    } else {
      if (!$("#shell")) main().innerHTML = `<div id="shell"></div><div id="view" class="view"></div>`;
      document.body.insertAdjacentHTML("beforeend", App.playerHtml());
      App.bindPlayer();
      App.player.attach(id);
      App.renderShell();
      await App.renderView();
      App.poll();
    }
  }

  document.addEventListener("DOMContentLoaded", () => { topbar(); route(); window.addEventListener("hashchange", route); });
})();
