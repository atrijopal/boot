"use strict";
/* The meeting workspace: header, pipeline stepper, tabs, overview, and the export drawer. */
(function () {
  const { esc, $, $$, ts, api, post, plural } = App;
  const hash = (tab, q) => `#/m/${App.id}${tab && tab !== "overview" ? "/" + tab : ""}${q ? "?" + q : ""}`;
  App.hash = hash;

  App.tabAvailable = k => {
    const st = App.stageState;
    if (k === "overview" || k === "pipeline") return true;
    if (k === "speakers" || k === "transcript") return st("transcription") === "completed";
    if (k === "refinement") return st("refinement") !== "waiting";
    if (k === "report") return st("report") !== "waiting" || ["needs_review", "completed"].includes(st("refinement"));
    return true;
  };

  App.shellHtml = () => {
    const m = App.meeting, s = m.session, a = s.audio || {};
    const sp = App.data.speakers && App.data.speakers.diarized ? ` · ${plural(App.data.speakers.speakers.length, "speaker")}` : "";
    const steps = App.STAGES.map(([k, label], i) => {
      const st = s.stages[k], st_ = st.state, avail = App.tabAvailable(App.STAGE_TAB[k]);
      const msg = st_ === "failed" ? (st.error ? st.error.title : "") : st.message || "";
      return `<a class="step ${st_}" ${avail ? `href="${hash(App.STAGE_TAB[k])}"` : ""} aria-label="${esc(label)}: ${esc(App.STATE_TEXT[st_])}"><div class="bar"></div>
        <div class="lbl"><span>${String(i + 1).padStart(2, "0")}</span>${esc(label)}</div><div class="st">${esc(App.STATE_TEXT[st_])}</div><div class="msg">${esc(msg)}</div></a>`;
    }).join("");
    const tabs = App.TABS.map(([k, label]) => {
      const on = App.tab === k, off = !App.tabAvailable(k);
      const cnt = k === "refinement" && App.data.refinement && App.data.refinement.counts && App.data.refinement.counts.needs_review ? `<span class="count">${App.data.refinement.counts.needs_review}</span>` : "";
      return `<a href="${hash(k)}" class="${on ? "active" : ""} ${off ? "off" : ""}" ${off ? 'aria-disabled="true" tabindex="-1"' : ""}>${esc(label)}${cnt}</a>`;
    }).join("");
    return `<div class="mhead"><div class="title"><div><div class="eyebrow">Meeting</div><h1>${esc(s.filename)}</h1>
        <div class="meta">${esc(s.meeting_date || "Date not provided")}${a.duration_seconds ? " · " + ts(a.duration_seconds) : ""}${sp}${a.file_size_bytes ? " · " + App.size(a.file_size_bytes) : ""}</div>
          ${s.example ? `<div class="notice" style="margin:12px 0 0;max-width:46em"><b style="font-weight:600">Example record.</b> ${esc(s.example_note || "")}</div>` : ""}</div>
        <div class="row"><button class="btn" id="exportBtn">Export</button></div></div>
      <nav class="stepper" aria-label="Processing stages">${steps}</nav><nav class="tabs" aria-label="Meeting sections">${tabs}</nav></div>`;
  };

  // ---------------------------------------------------------------- overview
  function nextStep(m) {
    const s = m.session, st = s.stages;
    const failed = App.STAGES.find(([k]) => st[k].state === "failed");
    if (failed) {
      const e = st[failed[0]].error || { title: "This step failed.", action: "Retry this step." };
      return { tone: "err", title: `${failed[1]} could not be completed`, body: `${e.message && e.message !== e.title ? e.message + " " : ""}${e.title} ${e.action}`.replace(/\s+/g, " "), actions: `<button class="btn primary" data-retry="${failed[0]}">Retry ${esc(failed[1].toLowerCase())}</button>` };
    }
    const running = App.STAGES.find(([k]) => st[k].state === "processing");
    if (running) return { title: `${running[1]} in progress`, body: st[running[0]].message || "Working on it. This page updates by itself.", actions: "" };
    if (st.speakers.state === "needs_review") return { tone: "amber", title: "Review the speakers", body: `${st.speakers.message}. Confirm a suggested name, type one, or leave a speaker unnamed. No name is shown unless the meeting itself shows it or you add it.`,
      actions: `<a class="btn primary" href="${hash("speakers")}">Review speakers</a><button class="btn" data-continue="speakers">Continue without changes</button>` };
    if (st.refinement.state === "needs_review") return { tone: "amber", title: "Review the proposed corrections", body: `${st.refinement.message}. The raw transcript stays untouched; accepted corrections appear only in the refined transcript.`,
      actions: `<a class="btn primary" href="${hash("refinement")}">Review corrections</a><button class="btn" data-continue="refinement">Continue with current choices</button>` };
    if (st.report.state === "completed" && m.final_check === false && !m.report_stale) return { tone: "amber", title: "The meeting record is ready, but the final check did not run",
      body: "The decisions and action items are matched to the recording, but they have not been double-checked and the summary is quotations only. Run the final check again to confirm them.",
      actions: `<a class="btn primary" href="${hash("report")}">Open the report</a><button class="btn" data-retry="report">Run the final check again</button>` };
    if (st.report.state === "completed") return { tone: "ok", title: "The meeting record is ready", body: m.report_stale ? m.reason : "Open the report, or download it from Export.",
      actions: `<a class="btn primary" href="${hash("report")}">Open the report</a>${m.report_stale ? `<button class="btn" data-retry="report">Regenerate report</button>` : ""}` };
    const keys = App.STAGES.map(([k]) => k), i = keys.findIndex(k => st[k].state === "waiting");
    if (i > 0 && st[keys[i - 1]].state === "completed") {
      const [k, label] = App.STAGES[i];
      return { title: k === "report" ? "Ready to write the meeting report" : `Next step: ${label.toLowerCase()}`, body: "Earlier steps are finished and kept. This step has not been run yet.",
        actions: `<button class="btn primary" data-retry="${k}">${k === "report" ? "Write the meeting report" : `Run ${esc(label.toLowerCase())}`}</button>` };
    }
    return { title: "Waiting to start", body: "Upload a recording to begin.", actions: "" };
  }

  App.views.overview = {
    async load() {
      const d = {};
      const t = App.stageState("transcription") === "completed";
      await Promise.all([
        t ? api(`/meetings/${App.id}/speakers`).then(x => (d.speakers = x)).catch(() => {}) : 0,
        App.stageState("refinement") !== "waiting" ? api(`/meetings/${App.id}/refinement`).then(x => (d.refinement = x)).catch(() => {}) : 0,
        App.stageState("report") === "completed" ? api(`/meetings/${App.id}/report`).then(x => (d.report = x)).catch(() => {}) : 0,
      ]);
      return d;
    },
    render(d) {
      const m = App.meeting, s = m.session, n = nextStep(m), a = s.audio || {};
      const facts = [["File", s.filename], ["Meeting date", s.meeting_date || "Not provided"], ["Duration", a.duration_seconds ? ts(a.duration_seconds) : "—"],
        ["Review steps", s.mode === "auto" ? "Skipped (automatic run)" : "Speakers and corrections"]];
      if (d.speakers && d.speakers.diarized) facts.push(["Speakers", String(d.speakers.speakers.length)]);
      if (d.refinement && d.refinement.ready) { const c = d.refinement.counts; facts.push(["Corrections", `${c.proposed} proposed · ${c.accepted} in force · ${c.needs_review} awaiting you`]); }
      if (d.report && d.report.ready) {
        const r = d.report, noOwner = r.action_items.filter(x => !x.owner).length;
        facts.push(["Decisions", String(r.decisions.length)], ["Action items", `${r.action_items.length}${noOwner ? ` · ${noOwner} without a stated owner` : ""}`], ["Open questions", String(r.open_questions.length)]);
      }
      const all = m.issues, counts = { error: 0, warning: 0, review: 0, info: 0 };
      all.forEach(i => counts[i.severity]++);
      App._issueFilter = App._issueFilter || "all";
      const shown = all.filter(i => App._issueFilter === "all" || i.severity === App._issueFilter);
      const filt = [["all", `All ${all.length}`], ...["error", "warning", "review", "info"].filter(k => counts[k]).map(k => [k, `${App.SEV[k]} ${counts[k]}`])];
      return `<div class="cols"><div>
          <div class="notice ${n.tone === "err" ? "err" : n.tone === "ok" ? "" : n.tone === "amber" ? "" : ""}" style="margin-top:0;${n.tone === "ok" ? "border-color:var(--ok);background:#e8f1e9" : ""}"><b style="font-weight:600">${esc(n.title)}</b><div>${esc(n.body)}</div>${n.actions ? `<div class="row">${n.actions}</div>` : ""}</div>
          ${requiredOutputs(m)}
          <div class="section"><h2>Where the system is unsure <span class="eyebrow">${all.length ? plural(all.length, "item") : ""}</span></h2>
            ${all.length ? `<div class="filters">${filt.map(([k, l]) => `<button class="btn sm ${App._issueFilter === k ? "on" : ""}" data-filter="${k}">${esc(l)}</button>`).join("")}</div>
              <div class="issues">${shown.map(i => `<div class="issue ${i.severity}">${App.icons[i.severity]}<div><span class="sev">${App.SEV[i.severity]}</span>${esc(i.message)}</div>
                  <div class="row" style="gap:10px">${i.at != null ? `<button class="go" data-seek="${i.at}">Play ${ts(i.at)}</button><a class="go" href="${hash("transcript", "seg=" + (i.segment_id || ""))}">Transcript</a>` : `<a class="go" href="${hash(App.STAGE_TAB[i.stage] || "overview")}">Open</a>`}</div></div>`).join("")}</div>`
              : `<div class="empty"><b>Nothing flagged so far.</b>Uncertainty is listed here as the recording is processed.</div>`}</div>
        </div><aside><div class="section" style="margin-top:0"><h2>This meeting</h2><dl class="kv">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl></div></aside></div>`;
    },
    bind(root) {
      $$("[data-filter]", root).forEach(b => (b.onclick = () => { App._issueFilter = b.dataset.filter; App.renderView(); }));
      bindActions(root);
    },
  };

  // the five outputs the brief asks for, each with where to read it and what to download
  function requiredOutputs(m) {
    const ready = Object.fromEntries((m.exports || []).map(e => [e.key, e.ready])), x = k => `/meetings/${App.id}/export/${k}`;
    const rows = [
      ["Raw transcript", "Speech-to-text before any language-model step", "transcript", [["TXT", "raw_transcript_txt"], ["JSON", "raw_transcript_json"]]],
      ["Refined transcript", "After terminology correction; every change is listed", "refinement", [["TXT", "refined_transcript_txt"], ["JSON", "refined_transcript_json"]]],
      ["Meeting minutes", "Summary and organised account of the discussion", "report", [["PDF", "report_pdf"], ["MD", "report_md"]]],
      ["Key decisions", "Decisions reached; an empty list if there were none", "report", [["JSON", "record_json"]]],
      ["Action items", "Tasks with stated owner and deadline, otherwise unspecified", "report", [["CSV", "actions_csv"], ["JSON", "record_json"]]],
    ];
    return `<div class="section" style="margin-top:22px"><h2>Required outputs</h2><div class="outs">${rows.map(([t, d, tab, dl]) => {
      const ok = dl.some(([, k]) => ready[k]);
      return `<div class="out"><div><b>${esc(t)}</b><span>${esc(d)}</span></div><div class="row" style="gap:10px">${ok
        ? `<a class="go" href="${hash(tab)}">View</a>${dl.filter(([, k]) => ready[k]).map(([l, k]) => `<a class="go" href="${x(k)}" download>${l}</a>`).join("")}` : `<span class="muted" style="font-size:13px">Not produced yet</span>`}</div></div>`;
    }).join("")}</div></div>`;
  }

  // retry / continue buttons work the same wherever they appear
  function bindActions(root) {
    $$("[data-retry]", root).forEach(b => (b.onclick = async () => { b.disabled = true; await App.act(() => post(`/meetings/${App.id}/retry`, { stage: b.dataset.retry }), "Running this step again."); App.kick(); }));
    $$("[data-continue]", root).forEach(b => (b.onclick = async () => { b.disabled = true; await App.act(() => post(`/meetings/${App.id}/continue`, { stage: b.dataset.continue }), "Continuing."); App.kick(); }));
  }
  App.bindActions = bindActions;

  // ---------------------------------------------------------------- export drawer
  App.openExports = async () => {
    let list;
    try { list = await api(`/meetings/${App.id}/exports`); } catch (e) { App.toast(e.message, "err"); return; }
    const groups = ["Raw transcript", "Refined transcript", "Meeting report", "Structured record", "More", "Package"];
    const html = groups.map(g => {
      const rows = list.filter(x => x.group === g);
      if (!rows.length) return "";
      return `<div class="xg"><h3>${esc(g)}</h3>${rows.map(x => `<div class="xr"><div><b style="font-weight:500">${esc(x.label)}</b> <span class="tag">${esc(x.ext)}</span><div class="fn">${esc(x.filename)}</div></div>
          ${x.ready ? `<a class="btn sm" href="/meetings/${esc(App.id)}/export/${esc(x.key)}" download>Download</a>` : `<button class="btn sm" disabled>Not available</button>`}
          ${x.ready ? "" : `<div class="why">${esc(x.reason)}</div>`}</div>`).join("")}</div>`;
    }).join("");
    const box = document.createElement("div");
    box.innerHTML = `<div class="scrim"></div><aside class="drawer" role="dialog" aria-label="Export"><div class="row" style="justify-content:space-between"><h2>Export</h2><button class="btn sm quiet" id="xclose">Close</button></div>
      <p class="muted" style="margin:4px 0 0;font-size:13px">A file is offered only once the step that makes it has finished. The structured record and the report carry the same decisions and tasks.</p>${html}</aside>`;
    document.body.appendChild(box);
    const close = () => box.remove();
    $(".scrim", box).onclick = close;
    $("#xclose", box).onclick = close;
    document.addEventListener("keydown", function esc_(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc_); } });
  };
})();
