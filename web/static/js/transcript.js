"use strict";
/* The transcript: the raw text is the source of truth and is never changed. Search, filter by speaker, jump through the recording. */
(function () {
  const { esc, $, $$, ts, api } = App;
  const S = (App._tx = { mode: "raw", speaker: null, q: "", dense: false, matches: false });
  const rx = q => new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  const who = s => s.speaker_name || s.speaker || "Unknown speaker";

  function textHtml(s, refined) {
    let t = esc(s.text);
    if (refined && s.changes) s.changes.forEach(c => { const e = esc(c.corrected); t = t.replace(e, `<ins title="Corrected from “${esc(c.original)}”">${e}</ins>`); });
    if (S.q.trim().length > 1) t = t.replace(new RegExp(`(${S.q.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/&/g, "&amp;")})(?![^<]*>)`, "gi"), "<mark>$1</mark>");
    return t;
  }

  function lanes(segs) {
    const dur = Math.max(1, segs.length ? segs[segs.length - 1].end : 1);
    const by = new Map();
    segs.forEach(s => { const k = s.speaker_label || "?"; if (!by.has(k)) by.set(k, []); by.get(k).push(s); });
    if (by.size < 2) return "";  // a single lane tells nothing
    return `<div class="lanes" aria-label="Who speaks when. Click to jump there.">${[...by.entries()].map(([k, list]) => {
      const first = list[0];
      return `<div class="lane ${S.speaker && S.speaker !== k ? "dim" : ""}"><span class="ln" title="${esc(who(first))}">${esc(who(first))}</span><div class="tr" data-lane="${esc(k)}" data-dur="${dur}">${list.map(s => `<b style="left:${(100 * s.start / dur).toFixed(2)}%;width:${Math.max(0.12, 100 * (s.end - s.start) / dur).toFixed(2)}%"></b>`).join("")}<i class="cur" style="left:0"></i></div></div>`;
    }).join("")}</div>`;
  }

  App.views.transcript = {
    load: () => api(`/meetings/${App.id}/transcript`),
    render(d) {
      const refinedOk = !!d.refined;
      if (S.mode === "refined" && !refinedOk) S.mode = "raw";
      const segs = S.mode === "refined" ? d.refined : d.raw;
      const speakers = [...new Map(d.raw.filter(s => s.speaker_label).map(s => [s.speaker_label, who(s)])).entries()];
      const q = S.q.trim().length > 1 ? S.q.trim().toLowerCase() : "";
      let shown = segs.filter(s => (!S.speaker || s.speaker_label === S.speaker) && (!S.matches || !q || s.text.toLowerCase().includes(q)));
      const nMatch = q ? segs.filter(s => s.text.toLowerCase().includes(q)).length : 0;
      let prev = null;
      const rows = shown.map(s => {
        const same = prev === (s.speaker_label || "?") && !S.speaker;
        const sp = !same;
        prev = s.speaker_label || "?";
        return `<div class="row-t ${sp ? "sp-start" : ""}" data-seg="${esc(s.id)}" data-start="${s.start}" data-end="${s.end}"><button class="ts" data-seek="${s.start}" title="Play from here">${ts(s.start)}</button>
          <span class="who ${same ? "same" : ""}" title="${esc(s.speaker || "")}">${esc(who(s))}</span><span class="txt">${textHtml(s, S.mode === "refined")}</span></div>`;
      }).join("");
      const exp = k => `/meetings/${App.id}/export/${k}`;
      const kind = S.mode === "refined" ? "refined" : "raw";
      return `<div class="rhead"><h2>${S.mode === "refined" ? "Refined transcript" : "Raw transcript"}</h2><span class="muted">${segs.length} lines</span></div>
        <p class="ink2" style="margin:6px 0 16px;max-width:48em">${S.mode === "refined" ? "The raw transcript with the corrections you accepted. Changed words are marked." : "Exactly what the transcription produced. It is never changed by refinement."}</p>
        <div class="tool"><div class="seg" role="tablist"><button class="${S.mode === "raw" ? "on" : ""}" data-mode="raw">Raw</button><button class="${S.mode === "refined" ? "on" : ""}" data-mode="refined" ${refinedOk ? "" : "disabled title=\"Available once refinement has run\""}>Refined</button></div>
          <input class="search" type="search" placeholder="Search the transcript" value="${esc(S.q)}" aria-label="Search the transcript">
          ${q ? `<span class="muted mono" style="font-size:12px">${nMatch} ${nMatch === 1 ? "line" : "lines"}</span><button class="btn sm ${S.matches ? "on" : ""}" data-matches>Matches only</button>` : ""}
          <span class="grow" style="flex:1"></span>
          <div class="seg"><button class="${!S.dense ? "on" : ""}" data-dense="0">Comfortable</button><button class="${S.dense ? "on" : ""}" data-dense="1">Compact</button></div>
          <button class="btn sm" data-copy>Copy</button><a class="btn sm" href="${exp(kind + "_transcript_txt")}" download>TXT</a><a class="btn sm" href="${exp(kind + "_transcript_json")}" download>JSON</a></div>
        ${speakers.length ? `<div class="filters"><button class="btn sm ${!S.speaker ? "on" : ""}" data-spk="">All speakers</button>${speakers.map(([k, n]) => `<button class="btn sm ${S.speaker === k ? "on" : ""}" data-spk="${esc(k)}">${esc(n)}</button>`).join("")}</div>` : ""}
        ${lanes(segs)}
        <div class="tx ${S.dense ? "compact" : ""}">${rows || `<div class="empty"><b>No lines match.</b>Clear the search or the speaker filter.</div>`}</div>`;
    },
    bind(root, d) {
      const redo = () => App.renderView();
      $$("[data-mode]", root).forEach(b => (b.onclick = () => { S.mode = b.dataset.mode; redo(); }));
      $$("[data-dense]", root).forEach(b => (b.onclick = () => { S.dense = b.dataset.dense === "1"; redo(); }));
      $$("[data-spk]", root).forEach(b => (b.onclick = () => { S.speaker = b.dataset.spk || null; redo(); }));
      const inp = $(".search", root);
      let t = null;
      inp.oninput = () => { clearTimeout(t); t = setTimeout(async () => { S.q = inp.value; await redo(); const n = $(".search"); if (n) { n.focus(); n.setSelectionRange(n.value.length, n.value.length); } }, 200); };
      const mm = $("[data-matches]", root);
      if (mm) mm.onclick = () => { S.matches = !S.matches; redo(); };
      $("[data-copy]", root).onclick = async () => {
        const segs = S.mode === "refined" ? d.refined : d.raw;
        const text = segs.map(s => `[${ts(s.start)}] ${who(s)}: ${s.text}`).join("\n");
        try { await navigator.clipboard.writeText(text); App.toast("Transcript copied."); } catch (e) { App.toast("Copying was blocked by the browser.", "err"); }
      };
      // lanes: click to jump; the cursor follows the recording
      $$(".lane .tr", root).forEach(el => (el.onclick = e => { const r = el.getBoundingClientRect(); App.player.seek(((e.clientX - r.left) / r.width) * parseFloat(el.dataset.dur)); }));
      const rowsEl = $$(".row-t", root);
      App.player.onTime = [time => {
        $$(".lane .cur", root).forEach(c => { const dur = parseFloat(c.parentElement.dataset.dur); c.style.left = (100 * time / dur) + "%"; });
        let hit = null;
        for (const r of rowsEl) { if (time >= parseFloat(r.dataset.start) && time < parseFloat(r.dataset.end)) { hit = r; break; } }
        rowsEl.forEach(r => r.classList.toggle("active", r === hit));
      }];
      const want = (App.query || {}).seg;
      if (want) {
        const row = $(`[data-seg="${want}"]`, root);
        if (row) { row.scrollIntoView({ block: "center" }); row.classList.add("flash"); }
        App.query = {};
      }
    },
  };
})();
