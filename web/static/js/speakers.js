"use strict";
/* Speaker review: who was heard, how much, what the meeting itself says about their name, and a place to confirm, edit or leave it unnamed. */
(function () {
  const { esc, $, $$, ts, api, post, plural } = App;

  App.views.speakers = {
    load: () => api(`/meetings/${App.id}/speakers`),
    render(d) {
      if (!d.diarized) {
        return `<div class="empty"><b>The speakers could not be told apart.</b>This recording's transcript has no speaker labels, so every line is attributed to an unknown speaker and no speaker can be named. Owners of action items are taken only from names stated in the recording.</div>`;
      }
      const review = App.stageState("speakers") === "needs_review";
      const rows = d.speakers.map(s => {
        const sugg = s.suggestions.length
          ? s.suggestions.map(x => `<div class="suggest"><div class="eyebrow">Suggested ${x.name ? "name" : "role"}</div>
              <div style="margin-top:4px"><b style="font-weight:600;font-size:16px">${esc(x.name || x.role)}</b>${x.name && x.role ? ` <span class="muted">· ${esc(x.role)}</span>` : ""}
                ${x.in_force ? `<span class="tag ok" style="margin-left:8px">In use</span>` : x.decision === "reject" ? `<span class="tag" style="margin-left:8px">Rejected</span>` : `<span class="tag amber" style="margin-left:8px">Needs your confirmation</span>`}</div>
              <q>${esc(x.quote)}</q><div class="cite">${esc(x.evidence_text)} · <button class="go" style="all:unset;cursor:pointer;color:var(--amber-ink);text-decoration:underline" data-seek="${x.start}">${ts(x.start)}</button></div>
              <div class="row" style="margin-top:9px"><button class="btn sm ${x.in_force ? "on" : ""}" data-sid="${esc(x.id)}" data-d="accept">Accept</button><button class="btn sm ${x.decision === "reject" ? "on" : ""}" data-sid="${esc(x.id)}" data-d="reject">Reject</button>
                ${x.decision ? `<button class="btn sm quiet" data-sid="${esc(x.id)}" data-d="reset">Reset</button>` : ""}</div></div>`).join("")
          : `<div class="muted" style="margin-top:12px;font-size:14px">No identity suggested. Add a name if you know this speaker.</div>`;
        return `<div class="spk" data-label="${esc(s.label)}"><div><h3>${esc(s.display)}</h3><div class="share" title="Share of speaking time"><i style="width:${Math.max(2, s.share)}%"></i></div>
            <div class="stats">${s.share}% of speaking time<br>${plural(s.lines, "line")} · ${s.words.toLocaleString()} words<br>first heard ${ts(s.first_start)}</div></div>
          <div><div class="namebox"><input type="text" value="${esc(s.name || "")}" placeholder="No name" maxlength="40" aria-label="Name for ${esc(s.display)}" data-name="${esc(s.label)}">
              <button class="btn sm" data-save="${esc(s.label)}">Save name</button>${s.name_source === "typed" ? `<span class="tag">Entered by you</span>` : s.name ? `<span class="tag ok">From the meeting</span>` : ""}</div>
            ${sugg}
            <div style="margin-top:12px"><button class="btn sm quiet" data-occ="${esc(s.label)}">Show all ${s.lines} lines</button></div><div class="occ hide" data-occ-list="${esc(s.label)}">${s.occurrences.map(o => `<div><button class="ts mono" style="all:unset;cursor:pointer;color:var(--ink-3);font:12.5px var(--mono)" data-seek="${o.start}">${ts(o.start)}</button><span>${esc(o.text)}</span></div>`).join("")}</div></div></div>`;
      }).join("");
      return `<div class="rhead"><h2>Speakers</h2><span class="muted">${plural(d.speakers.length, "speaker")} detected</span></div>
        <p class="ink2" style="max-width:46em;margin:8px 0 20px">Speakers are separated from the audio. A name is only suggested when the meeting itself shows it: a speaker introducing themselves, or being addressed by name and then answering. Nothing is guessed from how someone sounds or what they talk about. Names you confirm are used in the transcript and the report.</p>
        <div>${rows}</div>
        ${review ? `<div class="sticky"><span class="muted" style="font-size:13.5px">You can change names later; the report is rebuilt without reprocessing.</span><button class="btn primary" data-continue="speakers">Continue to refinement</button></div>`
          : `<div class="sticky"><span class="muted" style="font-size:13.5px">Speakers confirmed.</span><a class="btn" href="${App.hash("transcript")}">Open the transcript</a></div>`}`;
    },
    bind(root) {
      const reload = async () => { App.data.speakers = null; await App.renderView(); App.refresh(); };
      $$("[data-save]", root).forEach(b => (b.onclick = async () => {
        const label = b.dataset.save, v = $(`[data-name="${label}"]`, root).value.trim();
        const r = await App.act(() => post(`/meetings/${App.id}/speaker-names`, { names: { [label]: v } }), v ? "Name saved." : "Name cleared.");
        if (r) reload();
      }));
      $$("[data-name]", root).forEach(i => (i.onkeydown = e => { if (e.key === "Enter") $(`[data-save="${i.dataset.name}"]`, root).click(); }));
      $$("[data-sid]", root).forEach(b => (b.onclick = async () => { const r = await App.act(() => post(`/meetings/${App.id}/names/${b.dataset.sid}`, { decision: b.dataset.d })); if (r) reload(); }));
      $$("[data-occ]", root).forEach(b => (b.onclick = () => { const l = $(`[data-occ-list="${b.dataset.occ}"]`, root); l.classList.toggle("hide"); b.textContent = l.classList.contains("hide") ? b.textContent.replace("Hide", "Show") : b.textContent.replace("Show", "Hide"); }));
      App.bindActions(root);
    },
  };
})();
