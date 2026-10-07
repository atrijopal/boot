"use strict";
/* Refinement review: every proposed correction is inspectable, with the heard text, the proposal, the reason and the evidence. Nothing is applied silently. */
(function () {
  const { esc, $, $$, ts, api, post, plural } = App;
  const S = (App._rf = { filter: "all", mode: "changes" });

  const FILTERS = [["all", "All"], ["needs_review", "Needs review"], ["accepted", "Accepted"], ["rejected", "Rejected"]];

  function changeRow(c) {
    const sim = c.similarity != null ? `<span class="cite" title="How alike the two spellings sound and look. A match score, not a probability.">Similarity ${Number(c.similarity).toFixed(2)}</span>` : "";
    const ev = c.evidence.length ? c.evidence.map(e => `<span class="cite">${esc(e)}</span>`).join("") : (c.blocked ? "" : `<span class="cite">No other evidence in the recording supports this change.</span>`);
    const state = c.blocked ? "Blocked" : c.state === "accepted" ? "Accepted" : c.state === "rejected" ? "Rejected" : "Needs review";
    return `<div class="change ${c.state === "rejected" ? "rejected" : ""}" data-edit="${esc(c.id)}">
      <div><button class="ts" style="all:unset;cursor:pointer;font:12.5px/1.75 var(--mono);color:var(--ink-3)" data-seek="${c.start}" title="Play from here">${ts(c.start)}</button></div>
      <div><div class="line"><span class="lb">Heard</span>${c.before ? esc(c.before) : ""}<del>${esc(c.original)}</del>${esc(c.after)}
          <span class="lb">Proposed</span>${c.before ? esc(c.before) : ""}<ins>${esc(c.proposed)}</ins>${esc(c.after)}</div>
        <div class="why">${esc(c.reason)}${ev}${sim}${c.blocked ? `<span class="cite" style="color:var(--err)">Blocked by the safety checks: ${esc(c.blocked_because || "it would change what was said")}.</span>` : ""}
          <a class="cite" href="${App.hash("transcript", "seg=" + c.segment_id)}">Open in the transcript</a></div></div>
      <div class="act"><div class="state">${esc(state)}</div>
        ${c.blocked ? "" : `<button class="btn sm ${c.in_force ? "on" : ""}" data-d="accept">Accept</button><button class="btn sm ${c.decision === "reject" ? "on" : ""}" data-d="reject">Reject</button>${c.decision ? `<button class="btn sm quiet" data-d="reset">Reset</button>` : ""}`}</div></div>`;
  }

  App.views.refinement = {
    load: () => api(`/meetings/${App.id}/refinement`),
    render(d) {
      if (!d.ready) return `<div class="empty"><b>Refinement has not run yet.</b>It starts after the speakers are confirmed.</div>`;
      const c = d.counts, review = App.stageState("refinement") === "needs_review";
      const n = k => d.changes.filter(x => x.state === k).length;
      const list = d.changes.filter(x => S.filter === "all" || x.state === S.filter);
      const head = `<div class="rhead"><h2>Refinement review</h2><span class="muted">${c.proposed ? plural(c.proposed, "proposed change") : "No changes proposed"}</span></div>
        <p class="ink2" style="max-width:48em;margin:8px 0 16px">Recognition errors in technical terms, acronyms and names are corrected here, never numbers, dates, times or negations. The raw transcript is not touched: accepted corrections appear only in the refined transcript.</p>`;
      if (!c.proposed) return head + `<div class="empty"><b>No corrections were proposed.</b>The refined transcript is identical to the raw transcript.</div>`;
      const tools = `<div class="tool"><div class="filters" style="margin:0">${FILTERS.map(([k, l]) => `<button class="btn sm ${S.filter === k ? "on" : ""}" data-f="${k}">${esc(l)} ${k === "all" ? c.proposed : n(k === "needs_review" ? "needs_review" : k)}</button>`).join("")}</div>
          <span style="flex:1"></span>
          <button class="btn sm" data-bulk="accept_suggested" ${c.needs_review ? "" : "disabled"}>Accept all needing review</button><button class="btn sm" data-bulk="reject_suggested" ${c.needs_review ? "" : "disabled"}>Reject all needing review</button>
          <div class="seg"><button class="${S.mode === "changes" ? "on" : ""}" data-m="changes">Changes</button><button class="${S.mode === "side" ? "on" : ""}" data-m="side">Side by side</button></div></div>`;
      const body = S.mode === "changes" ? `<div class="rlist">${list.map(changeRow).join("") || `<div class="empty" style="margin-top:14px"><b>Nothing here.</b>No change matches this filter.</div>`}</div>`
        : `<div id="cmp"><div class="empty">Loading the transcripts…</div></div>`;
      const foot = review ? `<div class="sticky"><span class="${c.needs_review ? "" : "muted"}" style="font-size:13.5px">${c.needs_review ? `${plural(c.needs_review, "correction")} still need${c.needs_review === 1 ? "s" : ""} your decision and will not be applied if you continue.` : "Every correction has a decision."}</span>
          <button class="btn primary" data-continue="refinement">Continue to the meeting report</button></div>` : `<div class="sticky"><span class="muted" style="font-size:13.5px">Changes made now update the refined transcript. The report may need regenerating.</span><a class="btn" href="${App.hash("report")}">Open the report</a></div>`;
      return head + tools + body + foot;
    },
    bind(root, d) {
      const apply = async (fn, ok) => { const r = await App.act(fn, ok); if (r) { App.data.refinement = r; await App.renderView(); App.refresh(); } };
      $$("[data-f]", root).forEach(b => (b.onclick = () => { S.filter = b.dataset.f; App.renderView(); }));
      $$("[data-m]", root).forEach(b => (b.onclick = () => { S.mode = b.dataset.m; App.renderView(); }));
      $$("[data-bulk]", root).forEach(b => (b.onclick = () => apply(() => post(`/meetings/${App.id}/edits`, { action: b.dataset.bulk }), b.dataset.bulk === "accept_suggested" ? "Accepted." : "Rejected.")));
      $$(".change", root).forEach(row => $$("[data-d]", row).forEach(b => (b.onclick = () => apply(() => post(`/meetings/${App.id}/edits/${row.dataset.edit}`, { decision: b.dataset.d })))));
      App.bindActions(root);
      if (S.mode === "side" && d.ready && d.counts.proposed) {
        api(`/meetings/${App.id}/transcript`).then(t => {
          if (!t.refined) return;
          const box = $("#cmp", root);
          if (!box) return;
          const ch = new Map(t.refined.map(s => [s.id, s.changes || []]));
          box.innerHTML = `<div class="cmp"><div class="h">Raw</div><div class="h">Refined</div>${t.raw.map((s, i) => {
            const r = t.refined[i], c = ch.get(s.id) || [];
            let a = esc(s.text), b = esc(r.text);
            c.forEach(x => { a = a.replace(esc(x.original), `<del>${esc(x.original)}</del>`); b = b.replace(esc(x.corrected), `<ins>${esc(x.corrected)}</ins>`); });
            return `<div class="c ${c.length ? "chg" : ""}"><span class="cite">${ts(s.start)}</span> ${a}</div><div class="c ${c.length ? "chg" : ""}">${b}</div>`;
          }).join("")}</div>`;
        }).catch(() => {});
      }
    },
  };
})();
