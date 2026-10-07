"use strict";
/* The meeting report: a document. Decisions and action items are kept apart; owners and deadlines are shown only when the recording states them. */
(function () {
  const { esc, $, $$, ts, api, plural } = App;
  const cite = e => `<span class="cite"><button style="all:unset;cursor:pointer;text-decoration:underline" data-seek="${e.start}" title="Play from here">${esc(e.time)}</button> · ${esc(e.speaker)} · <a href="${App.hash("transcript", "seg=" + (e.segment_id || ""))}">in the transcript</a></span>`;
  const quote = e => `<div class="cite" style="margin-top:3px">“${esc(e.quote)}”</div>`;
  const U = '<span class="unspec">Unspecified</span>';

  App.views.report = {
    load: () => api(`/meetings/${App.id}/report`),
    render(r) {
      const s = App.meeting.session, st = App.stageState("report");
      if (!r.ready) {
        if (st === "failed") return `<div class="notice err"><b style="font-weight:600">The meeting report could not be produced.</b><div>${esc((s.stages.report.error || {}).action || "Retry this step.")}</div><div class="row"><button class="btn primary" data-retry="report">Retry</button></div></div>`;
        if (st === "processing") return `<div class="empty"><b>The meeting record is being written.</b>${esc(s.stages.report.message || "")} This page updates by itself.</div>`;
        const waiting = App.stageState("refinement") === "needs_review" ? `<div class="row" style="margin-top:12px"><button class="btn primary" data-continue="refinement">Continue and write the report</button></div>`
          : App.stageState("speakers") === "needs_review" ? `<div class="row" style="margin-top:12px"><a class="btn primary" href="${App.hash("speakers")}">Review the speakers first</a></div>` : "";
        return `<div class="empty"><b>The report has not been written yet.</b>It is produced after the speakers and the proposed corrections have been reviewed.${waiting}</div>`;
      }
      const exp = k => `/meetings/${App.id}/export/${k}`;
      const m = App.meeting;
      const people = r.speakers.map(x => `${esc(x.name || x.label.replace("SPEAKER_", "Speaker "))}${x.role ? " (" + esc(x.role) + ")" : ""}`);
      let n = 0;
      const H = t => `<h2><span class="n">${String(++n).padStart(2, "0")}</span>${esc(t)}</h2>`;
      const dec = r.decisions.length ? `<table class="t"><thead><tr><th>#</th><th>Decision</th><th>Source</th></tr></thead><tbody>${r.decisions.map((d, i) => `<tr><td>D${i + 1}</td><td>${esc(d.text)}${d.unconfirmed ? ` <span class="tag">Not confirmed</span>` : ""}${d.status !== "confirmed" ? ` <span class="tag">${esc(d.status)}</span>` : ""}${d.context ? `<div class="ctx">${esc(d.context)}</div>` : ""}${quote(d.evidence)}</td><td>${cite(d.evidence)}</td></tr>`).join("")}</tbody></table>`
        : `<p class="unspec">No explicit decisions were identified in this recording.</p>`;
      const act = r.action_items.length ? `<table class="t"><thead><tr><th>#</th><th>Task</th><th>Owner</th><th>Deadline</th><th>Source</th></tr></thead><tbody>${r.action_items.map((a, i) => `<tr><td>A${i + 1}</td><td>${esc(a.task)}${a.status !== "committed" && a.status !== "assigned" ? ` <span class="tag">${esc(a.status)}</span>` : ""}${quote(a.evidence)}</td>
          <td>${a.owner ? esc(a.owner) : U}</td><td>${a.deadline ? esc(a.deadline) : U}</td><td>${cite(a.evidence)}</td></tr>`).join("")}</tbody></table>`
        : `<p class="unspec">No actionable tasks were identified in this recording.</p>`;
      const unchecked = r.final_check === false && !m.report_stale ? `<div class="notice docbar" style="max-width:860px;margin:0 auto 14px"><span><b style="font-weight:600">The final check did not run.</b> Decisions and action items are matched to the recording but not double-checked.</span><button class="btn sm" data-retry="report">Run the final check again</button></div>` : "";
      return `${unchecked}${m.report_stale ? `<div class="notice docbar" style="max-width:860px;margin:0 auto 14px"><span>${esc(m.reason)}</span><button class="btn sm" data-retry="report">Regenerate report</button></div>` : ""}
        <div class="docbar"><span class="eyebrow">Meeting record</span><span class="row"><a class="btn sm primary" href="${exp("report_pdf")}" download>Download PDF</a><a class="btn sm" href="${exp("report_md")}" download>Markdown</a>
          <a class="btn sm" href="${exp("record_json")}" download>Structured JSON</a><button class="btn sm" id="print">Print</button></span></div>
        <article class="paper"><div class="bar"></div><h1>Meeting record</h1><div class="src">${esc(App.meeting.session.filename)}</div>
          <dl class="kv"><dt>Date</dt><dd>${esc(s.meeting_date || "Not provided")}</dd><dt>Duration</dt><dd>${ts(r.duration)}</dd><dt>Participants</dt><dd>${people.length ? people.join(", ") : "Speakers were not distinguished in this recording"}</dd></dl>
          ${r.headline ? `<p class="lead">${esc(r.headline)}</p>` : ""}
          ${r.summary.length ? H("Summary") + `<ul>${r.summary.map(x => `<li>${esc(x.text)}</li>`).join("")}</ul>` : ""}
          ${r.topics.length ? H("Discussion by topic") + r.topics.map(t => `<h3>${esc(t.title)} <span class="cite">${ts(t.start)}–${ts(t.end)}${t.outcome ? " · " + esc(t.outcome) : ""}</span></h3><ul>${t.points.map(p => `<li>${esc(p)}</li>`).join("")}</ul>`).join("") : ""}
          ${H("Key decisions")}${dec}
          ${H("Action items")}${act}
          ${r.open_questions.length ? H("Open questions") + `<ul>${r.open_questions.map(q => `<li>${esc(q.text)} ${cite(q.evidence)}</li>`).join("")}</ul>` : ""}
          ${r.key_figures.length ? H("Key figures") + `<table class="t"><thead><tr><th>Figure</th><th>Value</th><th>Source</th></tr></thead><tbody>${r.key_figures.map(f => `<tr><td style="width:auto;font:inherit;color:inherit">${esc(f.label)}</td><td>${esc(f.value)}</td><td>${cite(f.evidence)}</td></tr>`).join("")}</tbody></table>` : ""}
          ${r.attention.length ? H("Notes") + `<ul>${r.attention.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
          <p class="note">Every item in this record is taken from the recording; the source shows the time and speaker. Owners and deadlines appear only where the recording states them, otherwise they are marked Unspecified. The raw transcript is kept separately and unchanged.</p></article>`;
    },
    bind(root) {
      const p = $("#print", root);
      if (p) p.onclick = () => window.print();
      App.bindActions(root);
    },
  };
})();
