"use strict";
/* Pipeline details: what each stage received, what it produced, what it flagged. Implementation details stay behind an explicit switch. */
(function () {
  const { esc, $, $$, ts, api } = App;
  App._dev = false;

  App.views.pipeline = {
    load: () => api(`/meetings/${App.id}/pipeline${App._dev ? "?developer=true" : ""}`),
    render(d) {
      const stages = d.stages.map(s => {
        const open = s.state === "failed" || s.state === "needs_review";
        const iss = s.issues.length ? `<div><div class="lbl">Issues</div><div class="issues">${s.issues.map(i => `<div class="issue ${i.severity}">${App.icons[i.severity]}<div><span class="sev">${App.SEV[i.severity]}</span>${esc(i.message)}</div><span></span></div>`).join("")}</div></div>` : "";
        const err = s.error ? `<div class="notice err" style="margin:0"><b style="font-weight:600">${esc(s.error.title)}</b><div>${esc(s.error.action)}</div><div class="row"><button class="btn sm" data-retry="${esc(s.key)}">Retry this step</button></div></div>` : "";
        const out = s.output ? `<div><div class="lbl">Output · ${esc(s.output.title)}</div><details><summary class="btn sm" style="list-style:none;display:inline-flex">View raw output</summary><pre class="raw" style="margin-top:8px">${esc(s.output.content)}</pre></details></div>` : "";
        return `<details class="stage" ${open ? "open" : ""}><summary><span style="font-weight:600">${esc(s.label)}</span><span><span class="tag ${s.state === "completed" ? "ok" : s.state === "failed" ? "err" : s.state === "needs_review" ? "amber" : ""}">${esc(App.STATE_TEXT[s.state])}</span></span>
            <span class="muted x">${esc(s.message)}</span><span class="mono muted x" style="font-size:12.5px">${s.seconds != null ? s.seconds + " s" : ""}${s.attempts > 1 ? " · attempt " + s.attempts : ""}</span></summary>
          <div class="body">${err}${s.input ? `<div><div class="lbl">Input</div>${esc(s.input)}</div>` : ""}${out}${iss}</div></details>`;
      }).join("");
      const hist = d.events.length ? `<div class="section"><h2>Run history</h2><div class="hist">${d.events.slice().reverse().map(e => `<div><b>${esc(App.when(e.t))}</b> · ${esc(e.stage)} · ${esc(e.event)}${e.detail ? " · " + esc(e.detail) : ""}</div>`).join("")}</div></div>` : "";
      const dev = d.developer ? `<div class="section dev"><h2>Developer details</h2><p class="muted" style="margin:0 0 10px;font-size:13px">The individual processing calls behind the stages above.</p>
        <table class="t"><thead><tr><th>#</th><th>Stage</th><th>Part</th><th>Service</th><th>Effort</th><th>Time</th><th>Tokens in/out</th><th>Notes</th></tr></thead><tbody>${d.developer.map(c => `<tr><td>${c.n}</td><td>${esc(c.stage)}</td><td>${esc(c.window)}</td><td>${esc(c.model)}</td><td>${esc(c.effort)}</td><td>${(c.seconds || 0).toFixed(1)} s</td><td>${c.prompt_tokens ?? "–"} / ${c.completion_tokens ?? "–"}</td>
          <td>${[c.error ? "error: " + c.error : "", c.fallback_for ? "answered for " + c.fallback_for : "", c.effort_fallback ? "answered at lower effort" : ""].filter(Boolean).map(esc).join("; ")}</td></tr>`).join("")}</tbody></table></div>` : "";
      return `<div class="rhead"><h2>Pipeline details</h2><label class="row" style="font-size:13.5px;gap:8px"><input type="checkbox" id="dev" ${App._dev ? "checked" : ""}> Show developer details</label></div>
        <p class="ink2" style="max-width:48em;margin:8px 0 18px">What each stage received and produced, and what it flagged. Open a stage to read its raw output.</p>
        <div>${stages}</div>${hist}${dev}`;
    },
    bind(root) {
      $("#dev", root).onchange = e => { App._dev = e.target.checked; App.data.pipeline = null; App.renderView(); };
      App.bindActions(root);
    },
  };
})();
