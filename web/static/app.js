"use strict";
/* Meeting AI front-end. No framework: plain functions that build HTML strings. */
const App = (() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ts = s => { s = Math.round(s || 0); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
  const chip = (cls, text) => `<span class="chip ${cls}">${esc(text)}</span>`;
  const tri = v => v === true ? '<span class="ok" title="yes">&#10003;</span>' : v === false ? '<span class="bad" title="no">&#10007;</span>' : '<span class="muted">&ndash;</span>';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const S = { id: null, p1: null, p2: null, p3: null, stages: null };

  async function api(url, opts) {
    const r = await fetch(url, opts);
    let body = null;
    try { body = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error((body && (body.detail || (body.error && body.error.message))) || `Request failed (${r.status})`);
    return body;
  }
  const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  const table = (rows, head, timeFirst) => rows.length
    ? "<table>" + (head ? "<tr>" + head.map(h => `<th>${h}</th>`).join("") + "</tr>" : "")
      + rows.map(r => "<tr>" + r.map((c, i) => `<td class="${timeFirst && i === 0 ? "t" : ""}">${c}</td>`).join("") + "</tr>").join("") + "</table>"
    : '<span class="muted">none found</span>';
  const sect = (title, n, body, open, id) =>
    `<details class="sect" ${open ? "open" : ""} ${id ? `id="${id}"` : ""}><summary><h3>${title}${n != null ? ` <span class="count">(${n})</span>` : ""}</h3></summary>${body}</details>`;
  const pre = obj => `<pre>${esc(typeof obj === "string" ? obj : JSON.stringify(obj, null, 2))}</pre>`;

  /* ------------------------------------------------------------------ upload + job loading */
  async function init() {
    try {
      const h = await api("/health");
      $("provider").innerHTML = Object.entries(h.providers).map(([k, v]) =>
        `<option value="${k}" ${k === h.default_provider ? "selected" : ""}>${k}${v.configured ? "" : " (no key)"}</option>`).join("");
    } catch (e) { /* the page still works without it */ }
    $("go").onclick = processFile;
    $("p2-go").onclick = startRefine;
    $("p3-go").onclick = startMinutes;
    $("jobs").onchange = e => { if (e.target.value) loadJob(e.target.value); };
    await refreshJobs();
    const wanted = new URLSearchParams(location.search).get("job");
    if (wanted) loadJob(wanted);
  }

  async function refreshJobs() {
    try {
      const list = await api("/jobs");
      $("jobs").innerHTML = '<option value="">&mdash;</option>' + list.map(j =>
        `<option value="${esc(j.job_id)}" ${j.job_id === S.id ? "selected" : ""}>${esc(j.file || j.job_id)} (${ts(j.duration_seconds)})${j.has_minutes ? " [minutes]" : j.has_refinement ? " [refined]" : ""}</option>`).join("");
    } catch (e) { /* ignore */ }
  }

  function showError(el, e) { $(el).innerHTML = `<div class="err">${esc(e.message || e)}</div>`; }

  /** Polls one phase of a job until it is done (returns its state) or failed/skipped (throws).
   *  Returns null if the user has opened another job meanwhile, so an old loop never draws over the new one. */
  async function pollJob(id, key, onTick) {
    while (true) {
      if (S.id && S.id !== id) return null;
      const j = await api("/jobs/" + id);
      const s = key ? (j[key] || {}) : j;
      onTick && onTick(s, j);
      if (s.stage === "failed" || s.stage === "skipped") throw new Error((s.error && s.error.message) || s.message || "Failed");
      if (s.stage === "done") return s;
      await sleep(1200);
    }
  }
  const active = s => !!s && !["done", "failed", "skipped"].includes(s.stage);  // queued or running on the server

  async function processFile() {
    $("error").innerHTML = "";
    const f = $("file").files[0];
    if (!f) { showError("error", "Choose an audio file first."); return; }
    const all = $("runall").checked;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("provider", $("provider").value);
    fd.append("run_all", all ? "true" : "false");  // the server chains steps 2 and 3, so the run continues even if this tab is closed
    $("go").disabled = true;
    $("status").textContent = "Uploading...";
    try {
      const r = await api("/process-audio", { method: "POST", body: fd });
      S.id = r.job_id;
      history.replaceState(null, "", "?job=" + encodeURIComponent(r.job_id));  // a reload resumes following this run
      const step = all ? "Step 1 of 3, transcript. " : "";
      if (!(await pollJob(r.job_id, "", s => { $("status").textContent = step + s.stage + ": " + (s.message || ""); }))) return;
      $("status").textContent = all ? "Transcript ready. Refining and writing the minutes now (steps 2 and 3)..." : "Transcript ready.";
      await loadJob(r.job_id);  // with "run all", this follows steps 2 and 3 until they end
      if (all && S.id === r.job_id) $("status").textContent = S.p3 ? "All three steps finished. Results and downloads are below." : "Finished with errors: see the step that failed below.";
      refreshJobs();
    } catch (e) { showError("error", e); $("status").textContent = ""; }
    finally { $("go").disabled = false; }
  }

  async function loadJob(id) {
    try {
      S.id = id;
      let j = await api("/jobs/" + id);
      if (j.stage === "failed") { showError("error", (j.error && j.error.message) || j.message || "Transcription failed."); return; }
      if (j.stage !== "done") {  // step 1 still running (for example after a reload): wait for it
        if (!(await pollJob(id, "", s => { $("status").textContent = "Step 1, transcript. " + s.stage + ": " + (s.message || ""); }))) return;
        j = await api("/jobs/" + id);
        $("status").textContent = "Transcript ready.";
      }
      Object.assign(S, { p1: j.result, p2: (j.refine && j.refine.result) || null, p3: (j.minutes && j.minutes.result) || null, stages: null });
      history.replaceState(null, "", "?job=" + encodeURIComponent(id));
      showAll();
      $("p2-go").disabled = false;  // a follower of a previously opened job may have left it disabled
      if (S.p3) loadStages();
      // steps the server is still running or has queued (a chained run, or one started before a reload): follow them
      if (active(j.refine)) await followRefine(id);
      else if (j.refine && j.refine.stage === "skipped") $("p2-status").innerHTML = `<div class="note">${esc(j.refine.message)}</div>`;
      j = await api("/jobs/" + id);
      if (S.id !== id) return;
      if (active(j.minutes)) await followMinutes(id);
      else if (j.minutes && j.minutes.stage === "skipped") $("p3-progress").innerHTML = `<div class="note">${esc(j.minutes.message)} Fix that step, then use the button above.</div>`;
    } catch (e) { showError("error", e); }
  }

  /** Render everything from an already-loaded state (also used by the screenshot tests). */
  function showAll() {
    $("phases").hidden = false;
    renderPhase1();
    renderPhase2();
    renderPhase3();
  }

  /* ------------------------------------------------------------------ phase 1 */
  function renderPhase1() {
    const r = S.p1, f = r.protected_facts, c = r.meeting_context;
    $("p1-chip").innerHTML = chip("ok", "done");
    const names = [...f.people.map(x => ["person", x]), ...f.organizations.map(x => ["org", x]), ...f.locations.map(x => ["place", x]),
      ...f.other_entities.map(x => ["other", x]), ...f.proper_noun_candidates.map(x => ["name? (heuristic)", x])];
    let h = `<p class="muted">${esc(r.provider)} / ${esc(r.model)} &middot; ${ts(r.audio_metadata.duration_seconds)} of audio &middot; speech-to-text ${r.timings_seconds.asr}s &middot; total ${r.timings_seconds.total}s${r.cache_hit ? " &middot; cached" : ""}
      &middot; <a href="/jobs/${S.id}/download">download JSON</a></p>`;
    if (r.warnings.length) h += `<div class="note">${r.warnings.map(esc).join("<br>")}</div>`;
    h += sect("Transcript", r.raw_transcript.segments.length, `<div class="scroll">${table(r.raw_transcript.segments.map(s => [ts(s.start), esc(s.speaker), esc(s.text)]), null, true)}</div>`, true);
    h += sect("Numbers", f.numbers.length, table(f.numbers.map(x => [ts(x.start), "<b>" + esc(x.value) + "</b>", esc(x.surface),
      `<span class="muted">${esc(x.extra.kind)}${x.extra.unit_candidate ? " + " + esc(x.extra.unit_candidate) : ""}</span>`]), null, true));
    h += sect("Names", names.length, table(names.map(([k, x]) => [ts(x.start), "<b>" + esc(x.value) + "</b>", `<span class="muted">${esc(k)}</span>`]), null, true));
    h += sect("Technical terms and acronyms", f.acronyms.length + f.technical_terms.length, table([...f.acronyms, ...f.technical_terms].map(x =>
      [ts((x.mentions[0] || {}).start || 0), "<b>" + esc(x.term) + "</b>", `<span class="muted">${esc(x.kind)} &times;${x.count}</span>`,
        x.asr_suspect ? `<span class="flag">ASR-suspect: ${esc(x.asr_suspect_reason)}</span>` : ""]), null, true));
    h += sect("Dates, times, durations", f.dates.length + f.times.length + f.durations.length, table([...f.dates, ...f.times, ...f.durations]
      .sort((a, b) => a.start - b.start).map(x => [ts(x.start), "<b>" + esc(x.surface) + "</b>", `<span class="muted">${esc(x.type)} (not resolved)</span>`]), null, true));
    h += sect("Negations", f.negations.length, table(f.negations.map(x => [ts(x.start), "<b>" + esc(x.surface) + "</b>", esc(x.extra.scope)]), null, true));
    for (const [k, label] of [["candidate_commitments", "Candidate commitments"], ["candidate_decisions", "Candidate decisions"], ["candidate_proposals", "Candidate proposals"],
      ["candidate_tasks", "Candidate tasks"], ["candidate_rejections", "Candidate rejections"], ["candidate_questions", "Candidate questions"], ["uncertainty", "Uncertainty and hedges"]])
      h += sect(label + " (model-labelled)", f[k].length, table(f[k].map(x => [ts(x.start), esc(x.value), x.extra.said_by ? `<span class="muted">${esc(x.extra.said_by)}</span>` : ""]), null, true));
    h += sect("Topic hints", c.topic_hints.length, table(c.topic_hints.map(t => [ts(t.start), esc(t.terms.join(", "))]), null, true));
    $("p1-body").innerHTML = h;
  }

  /* ------------------------------------------------------------------ phase 2 */
  const isApplied = e => e.status !== "rejected" && (e.user_decision === "accept" || (e.user_decision !== "reject" && e.status === "applied"));
  const nameApplied = n => n.status !== "rejected" && (n.user_decision ? n.user_decision === "accept" : n.status === "applied");

  async function startRefine() {
    $("p2-go").disabled = true;
    $("p2-status").textContent = "Starting...";
    try { await api(`/jobs/${S.id}/refine`, { method: "POST" }); }
    catch (e) { $("p2-status").innerHTML = `<div class="err">${esc(e.message)}</div>`; $("p2-go").disabled = false; return; }
    await followRefine(S.id);
  }

  /** Shows a refinement that is queued or running on the server until it ends. Returns true if it finished. */
  async function followRefine(id) {
    $("p2-go").disabled = true;
    $("p3-go").disabled = true;
    let ok = false;
    try {
      const s = await pollJob(id, "refine", s => { $("p2-status").textContent = (s.stage || "") + ": " + (s.message || ""); });
      if (!s) return false;
      $("p2-status").textContent = "";
      S.p2 = s.result;
      S.stages = null;
      renderPhase2();
      renderPhase3();
      ok = true;
    } catch (e) { $("p2-status").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
    $("p2-go").disabled = false;
    $("p3-go").disabled = !S.p2;
    return ok;
  }

  function markup(text, edits, side) {
    const es = edits.filter(e => e.char_start != null && e.status !== "rejected").sort((a, b) => a.char_start - b.char_start);
    let out = "", pos = 0;
    for (const e of es) {
      if (e.char_start < pos) continue;
      out += esc(text.slice(pos, e.char_start));
      const orig = esc(text.slice(e.char_start, e.char_end));
      if (isApplied(e)) out += side === "raw" ? `<del>${orig}</del>` : `<ins>${esc(e.replace)}</ins>`;
      else out += side === "raw" ? `<u class="sug" title="suggested: ${esc(e.replace)}">${orig}</u>` : orig;
      pos = e.char_end;
    }
    return out + esc(text.slice(pos));
  }

  async function p2action(url, body) {
    try { S.p2 = await post(url, body); renderPhase2(); }
    catch (e) { alert(e.message); }
  }
  const decide = (id, d) => p2action(`/jobs/${S.id}/edits/${id}`, { decision: d });
  const decideName = (id, d) => p2action(`/jobs/${S.id}/speaker-names/${id}`, { decision: d });
  const renameSpeaker = label => p2action(`/jobs/${S.id}/speakers/${label}/name`, { name: $("nm_" + label).value });

  function speakerPanel(R) {
    const talk = {};
    R.refined_transcript.segments.filter(s => s.speaker_known).forEach(s => {
      const t = talk[s.speaker] = talk[s.speaker] || { secs: 0, name: null };
      t.secs += s.end - s.start;
      if (s.speaker_name) t.name = s.speaker_name;
    });
    const labels = Object.keys(talk).sort();
    if (!labels.length) return '<span class="muted">No speaker labels for this recording. Choose the deepgram provider, or add a Deepgram key so it can label Groq\'s text.</span>';
    let h = `<p class="muted">Voices (who speaks) come from ${esc(R.speaker_source)}. Names are only taken from what people say in the recording.</p><table>`;
    for (const l of labels) {
      const props = R.speaker_names.filter(n => n.speaker === l), man = R.manual_names[l] || "";
      h += `<tr><td class="t">${esc(l)}</td><td><b>${esc(talk[l].name || "(unnamed)")}</b>${man ? ' <span class="muted">(typed by you)</span>' : ""}<br><span class="muted">${Math.round(talk[l].secs)}s of speech</span></td><td>`;
      h += props.map(n => {
        const st = nameApplied(n) ? "applied" : n.status === "rejected" ? "rejected" : "suggested";
        const btn = n.status === "rejected" ? "" : (nameApplied(n) ? `<button class="sm" onclick="App.decideName('${n.name_id}','reject')">Reject</button>` : `<button class="sm" onclick="App.decideName('${n.name_id}','accept')">Accept</button>`)
          + (n.user_decision ? ` <button class="sm" onclick="App.decideName('${n.name_id}','reset')">Reset</button>` : "");
        return `<div>${chip(st, st)} <b>${esc(n.name)}</b> <span class="muted">${esc(n.basis.replace(/_/g, " "))} at ${ts(n.start)}</span> ${btn}<br>
          <span class="muted">&ldquo;${esc(n.quote)}&rdquo; ${esc(n.note)}${n.reject_reason ? ' <span class="bad">blocked: ' + esc(n.reject_reason) + "</span>" : ""}</span></div>`;
      }).join("") || '<span class="muted">no name stated in the recording</span>';
      h += `</td><td><input id="nm_${l}" size="14" placeholder="type a name" value="${esc(man)}"> <button class="sm" onclick="App.renameSpeaker('${l}')">Set</button></td></tr>`;
    }
    return h + "</table>";
  }

  function renderPhase2() {
    const R = S.p2;
    $("p2-go").textContent = R ? "Refine again" : "Refine transcript";
    if (!R) { $("p2-chip").innerHTML = chip("", "not run"); $("p2-body").innerHTML = '<p class="muted">Fixes mis-heard terms and names, labels speakers, and shows exactly what changed. Run it before generating minutes.</p>'; return; }
    const E = R.edits, st = R.stats, inv = R.invariants;
    $("p2-chip").innerHTML = chip("ok", `${E.filter(isApplied).length} edits applied`);
    const badge = (ok, t) => `<span class="${ok ? "ok" : "bad"}">${ok ? "&#10003;" : "&#10007;"} ${t}</span>`;
    let h = `<p class="muted">refiner ${esc(R.refine_model)} &middot; verifier ${esc(R.verify_model || "off")} &middot; second ASR ${esc(R.second_asr || "none")} &middot; ${R.timings_seconds.total}s</p>`;
    h += `<p>${chip("applied", E.filter(isApplied).length + " applied")} ${chip("suggested", E.filter(e => e.status === "suggested" && !isApplied(e)).length + " suggested")} ${chip("rejected", E.filter(e => e.status === "rejected").length + " blocked")}
      <span class="muted">&middot; ${st.segments_changed} of ${st.segments_total} segments changed</span></p>`;
    h += `<p>${badge(inv.segment_count_unchanged, "segment count")} &nbsp; ${badge(inv.ids_times_speakers_unchanged, "timestamps and speakers")} &nbsp; ${badge(inv.numbers_unchanged, "numbers")}
      &nbsp; ${badge(inv.dates_times_unchanged, "dates/times")} &nbsp; ${badge(inv.negations_unchanged, "negations")}</p>`;
    if (R.warnings.length) h += `<div class="note">${R.warnings.map(esc).join("<br>")}</div>`;
    h += sect("Speakers", Object.keys(R.manual_names).length + R.speaker_names.length, speakerPanel(R), true);
    const segs = S.p1.normalized_transcript.segments, refined = R.refined_transcript.segments;
    const changed = segs.filter(s => E.some(e => e.segment_id === s.segment_id && e.status !== "rejected"));
    h += sect("What changed (raw vs refined)", changed.length, changed.length ? changed.map(s => {
      const es = E.filter(e => e.segment_id === s.segment_id), rs = refined.find(x => x.segment_id === s.segment_id) || s;
      return `<div class="seg"><div class="muted">${ts(s.start)} ${esc(rs.speaker_name || rs.speaker)}</div><div class="cols"><div>${markup(s.text, es, "raw")}</div><div>${markup(s.text, es, "refined")}</div></div></div>`;
    }).join("") : '<span class="muted">no changes proposed</span>', true);
    h += sect("All edits (model proposals and our checks)", E.length, table(E.map(e => {
      const ev = [...e.support.map(x => esc(x.detail)), e.reject_reason ? `<span class="bad">blocked: ${esc(e.reject_reason)}</span>` : "",
        e.verdict ? `verifier: ${e.verdict.same_meaning ? "same meaning" : "meaning may differ"}${e.verdict.new_information ? ", new info" : ""}` : (e.status === "applied" ? "unverified" : "")].filter(Boolean).join("<br>");
      const state = isApplied(e) ? "applied" : e.status === "rejected" ? "rejected" : "suggested";
      const btn = e.status === "rejected" ? "" : (isApplied(e) ? `<button class="sm" onclick="App.decide('${e.edit_id}','reject')">Reject</button>` : `<button class="sm" onclick="App.decide('${e.edit_id}','accept')">Accept</button>`)
        + (e.user_decision ? ` <button class="sm" onclick="App.decide('${e.edit_id}','reset')">Reset</button>` : "");
      return [ts(e.start), `<del>${esc(e.find)}</del> &rarr; <ins>${esc(e.replace)}</ins>`, chip(state, state) + (e.user_decision ? " (you)" : ""),
        `<span class="muted">${esc(e.kind)}</span>`, `<span class="muted">${ev}${e.reason ? "<br>model: " + esc(e.reason) : ""}</span>`, btn];
    }), null, true), true);
    h += sect("Refined transcript with speakers", R.refined_transcript.segments.length, `<div class="scroll">${table(R.refined_transcript.segments.map(s => {
      const sg = R.segment_speakers[s.segment_id];
      const who = s.speaker_name ? `${esc(s.speaker_name)} <span class="muted">(${esc(s.speaker)})</span>` : esc(s.speaker);
      const mixed = sg && sg.mixed ? ` <span class="flag" title="two voices in this segment">&#9888; also ${esc(sg.runner_up)} (${Math.round(sg.runner_up_share * 100)}%)</span>` : "";
      return [ts(s.start), who, esc(s.text) + mixed];
    }), null, true)}</div>`);
    h += `<p>Download: <a href="/jobs/${S.id}/refined?format=txt">refined transcript</a> &middot; <a href="/jobs/${S.id}/refined?format=speakers">speakers</a> &middot; <a href="/jobs/${S.id}/refined?format=diff">diff</a> &middot;
      <a href="/jobs/${S.id}/refined?format=csv">edits.csv</a> &middot; <a href="/jobs/${S.id}/refined?format=json">full JSON</a></p>`;
    $("p2-body").innerHTML = h;
  }

  /* ------------------------------------------------------------------ phase 3: running */
  const STEP_LABELS = { chunk: "Split transcript", topics: "Find topics", extract_all: "Extract records", extract_agreements: "Decisions and proposals", extract_actions: "Actions",
    extract_questions_risks: "Questions and risks", extract_facts: "Key figures", verify: "Verify items", reconcile: "Link items", summarize: "Write topic minutes", executive: "Write executive summary" };

  let minutesStarted = 0, minutesTimer = null;
  function elapsedHint() {
    const secs = Math.round((Date.now() - minutesStarted) / 1000), box = $("p3-elapsed");
    if (!box) return;
    box.innerHTML = `Elapsed ${ts(secs)}.` + (secs > 180 ? " Taking a while: the model API may be rate limiting. A run stops by itself after at most 15 minutes and reports what it could not finish." : "");
  }

  function renderProgress(s) {
    const done = new Set(s.done || []), run = new Set(s.running || []);
    const order = ["chunk", "topics", "extract_all", "extract_agreements", "extract_actions", "extract_questions_risks", "extract_facts", "verify", "reconcile", "summarize", "executive"];
    const seenExtract = order.slice(2, 7).some(n => done.has(n) || run.has(n));
    const steps = order.filter(n => !n.startsWith("extract") || done.has(n) || run.has(n) || (!seenExtract && n === "extract_all"));
    $("p3-progress").innerHTML = `<div class="stepper">${steps.map(n => `<span class="s ${done.has(n) ? "done" : run.has(n) ? "run" : ""}">${done.has(n) ? "&#10003; " : ""}${esc(STEP_LABELS[n] || n)}</span>`).join("")}</div><div id="p3-elapsed" class="muted"></div>`;
    elapsedHint();
  }

  async function startMinutes() {
    $("p3-go").disabled = true;
    try { await api(`/jobs/${S.id}/minutes`, { method: "POST" }); }
    catch (e) { $("p3-progress").innerHTML = `<div class="err">${esc(e.message)}</div>`; $("p3-go").disabled = !S.p2; return; }
    await followMinutes(S.id);
  }

  /** Shows a minutes run that is queued or running on the server until it ends. */
  async function followMinutes(id) {
    $("p3-go").disabled = true;
    $("p2-go").disabled = true;  // re-refining mid-run would make these minutes out of date at once
    $("p3-body").innerHTML = "";
    minutesStarted = Date.now();
    clearInterval(minutesTimer);
    minutesTimer = setInterval(elapsedHint, 1000);
    renderProgress({});
    try {
      const s = await pollJob(id, "minutes", renderProgress);
      clearInterval(minutesTimer);
      if (!s) return;
      S.p3 = s.result;
      S.stages = null;
      $("p3-progress").innerHTML = "";
      renderPhase3();
      loadStages();
      refreshJobs();
    } catch (e) { clearInterval(minutesTimer); $("p3-progress").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
    $("p3-go").disabled = !S.p2;
    $("p2-go").disabled = false;
  }

  async function loadStages() {
    try { S.stages = await api(`/jobs/${S.id}/minutes/stages`); }
    catch (e) { S.stages = { error: e.message }; }
    const box = $("p3-stages");
    if (box) box.innerHTML = stagesHtml(S.stages);
  }

  /* ------------------------------------------------------------------ phase 3: page */
  function renderPhase3() {
    const M = S.p3;
    $("p3-go").textContent = M ? "Regenerate minutes" : "Generate minutes";
    $("p3-go").disabled = !S.p2;
    if (!S.p2) { $("p3-chip").innerHTML = chip("", "needs step 2"); $("p3-body").innerHTML = '<p class="muted">Refine the transcript first (step 2). Minutes are written from the refined transcript and its speaker names.</p>'; return; }
    if (!M) { $("p3-chip").innerHTML = chip("", "not generated"); $("p3-body").innerHTML = '<p class="muted">Runs several model calls (extract records, verify, link, write) and shows every step, then the final report.</p>'; return; }
    $("p3-chip").innerHTML = chip(M.status, M.status) + (M.stale ? " " + chip("warn", "out of date") : "");
    $("p3-body").innerHTML = `<div class="part"><h2>A. Every model step, in order</h2><div id="p3-stages"><p class="muted">Loading step-by-step outputs...</p></div></div>
      <div class="part" id="report"><h2>B. Final report</h2>${finalReport(M)}</div>`;
    if (S.stages) $("p3-stages").innerHTML = stagesHtml(S.stages);
  }

  /* ------------------------------------------------------------------ phase 3: step-by-step LLM outputs */
  function callBlock(c) {
    const meta = c.error ? `<span class="bad">${esc(c.error)}</span>`
      : c.cache_hit ? chip("warn", "reused from cache")
        : `<span>${c.prompt_tokens ?? "?"} tokens in / ${c.completion_tokens ?? "?"} out</span><span>${(c.secs || 0).toFixed(1)}s${c.retries ? `, ${c.retries} rate-limit retries` : ""}</span>`;
    return `<div class="callhead">${chip(c.error ? "failed" : "ok", "model call #" + c.call_id)} <span>${esc(c.model)}</span><span>effort ${esc(c.effort)}</span><span>prompt: <code>${esc(c.prompt)}</code></span> ${meta}</div>
      <details class="inner"><summary>Input sent to the model (${c.input.length} chars)</summary>${pre(c.input)}</details>
      <details class="inner"><summary>Raw model output</summary>${pre(c.output === null ? "(no answer)" : c.output)}</details>`;
  }
  const quotes = ev => (ev || []).map(e => `<div class="quote"><code>${esc(e.segment_id)}</code> &ldquo;${esc(e.quote)}&rdquo;</div>`).join("");

  function stagesHtml(G) {
    if (!G || G.error) return `<div class="note">${esc((G && G.error) || "Step-by-step log not available.")}</div>`;
    const O = G.outcomes || {}, calls = G.calls || [], meta = G.meta || {};
    const callsOf = (node, ref) => calls.filter(c => (typeof node === "string" ? c.node === node : node(c.node)) && (ref === undefined || c.ref === ref));
    let h = "";

    // configuration
    const cfg = Object.entries(meta.models || {}).map(([k, v]) => [esc(k), esc(v), esc((meta.efforts || {})[k] || "")]);
    h += sect("0. Configuration: models, windows, prompts", null,
      `<p class="muted">Extraction mode: <b>${esc(meta.extract_mode || "")}</b> (combined = one model call per window for every kind of record).</p>`
      + table(cfg, ["Stage", "Model", "Reasoning effort"])
      + `<h3>Transcript windows sent to the extractor</h3>` + table((meta.chunks || []).map(c => [esc(c.chunk_id), `${esc(c.first)} to ${esc(c.last)}`, `${ts(c.start)} to ${ts(c.end)}`]), ["Window", "Segments", "Time"])
      + `<details class="inner"><summary>Prompt file hashes (prompts/minutes/*.md)</summary>${pre(meta.prompt_hashes || {})}</details>`, false);

    // 1 extraction
    const ex = O.extraction || [];
    const refs = [...new Set(ex.map(r => r.ref).concat(callsOf(n => n.startsWith("extract")).map(c => c.ref)))].sort();
    h += sect("1. Extraction: what the model proposed, and what our quote and number checks kept", ex.length,
      `<p class="muted">The model proposes records with quotes. Code then checks that every quote exists in the cited segment and that no number is invented. Dropped items stay visible here with the reason.</p>`
      + (refs.map(ref => {
        const rows = ex.filter(r => r.ref === ref), kept = rows.filter(r => r.result === "kept").length;
        return `<div class="stage"><header><b>Window ${esc(ref)}</b> ${chip("kept", kept + " kept")} ${chip(rows.length - kept ? "dropped" : "", (rows.length - kept) + " dropped")}</header>
          ${callsOf(n => n.startsWith("extract"), ref).map(callBlock).join("")}
          ${table(rows.map(r => [chip("", r.lens), esc(r.status), esc(r.text) + quotes(r.evidence), chip(r.result, r.result) + (r.reason ? `<div class="muted">${esc(r.reason)}</div>` : "")]), ["Kind", "Status", "Statement and evidence", "Our check"])}</div>`;
      }).join("") || '<span class="muted">No extraction calls recorded.</span>'), true);

    // 2 merge
    const mg = (O.merge || [])[0];
    h += sect("2. Merge duplicates", null, mg ? `<p>${mg.before} proposed records &rarr; ${mg.after} after merging. <span class="muted">${esc(mg.note)}</span></p>` : '<span class="muted">n/a</span>', false);

    // 3 verification
    const ver = O.verification || [];
    h += sect("3. Verification: a second model checks each record against the transcript", ver.length,
      `<p class="muted">The verifier sees the quote plus the lines around it and answers: is the statement faithful (supported), does the status fit its meaning, are the owner and deadline justified. Only verified records reach the report.</p>`
      + callsOf("verify").map(callBlock).join("")
      + table(ver.map(v => { const d = v.verdict || {}; return [ts(v.start), `<code>${esc(v.item_id)}</code> ${chip("", v.lens)} ${esc(v.text)}<div class="quote">&ldquo;${esc(v.quote)}&rdquo;</div>`,
        tri(d.supported), tri(d.status_ok), esc(d.better_status || ""), tri(d.owner_ok), tri(d.deadline_ok), `<span class="muted">${esc(d.reason || "")}</span>`,
        chip(v.result.startsWith("verified") ? "verified" : "needs_review", v.result) + (v.status_before !== v.status_after ? `<div class="muted">${esc(v.status_before)} &rarr; ${esc(v.status_after)}</div>` : "")]; }),
      ["Time", "Record", "Faithful", "Status ok", "Better status", "Owner ok", "Deadline ok", "Reason", "Outcome"]), true);

    // 4 reconcile
    const rc = O.reconcile || [], rcCalls = callsOf("reconcile");
    h += sect("4. Linking decisions, proposals and questions", rc.length,
      rcCalls.length ? `<p class="muted">One call relates verified items to each other (replaces, contradicts, adopts, rejects, answers). A relation is applied only if its quote is in the evidence of one of the two items.</p>`
        + rcCalls.map(callBlock).join("") + (rc.length ? table(rc.map(r => [`<code>${esc(r.source)}</code> <b>${esc(r.kind)}</b> <code>${esc(r.target)}</code><div class="quote">&ldquo;${esc(r.quote)}&rdquo;</div>`,
          chip(r.result, r.result) + (r.reason ? `<div class="muted">${esc(r.reason)}</div>` : "")]), ["Relation proposed by the model", "Our check"]) : '<span class="muted">The model proposed no relations.</span>')
        : '<span class="muted">Skipped: it needs at least one decision and one other decision, proposal or question.</span>', rcCalls.length > 0);

    // 5 topics
    const tp = O.topics || [], tf = O.topics_final || [];
    h += sect("5. Topics of the meeting", tf.length, `<p class="muted">The model proposes contiguous topics; code makes the ranges complete, non-overlapping and a sensible count.</p>`
      + callsOf("topics").map(callBlock).join("")
      + table(tp.map(t => [esc(t.ref), esc(t.title), `${esc(t.start_segment)} to ${esc(t.end_segment)}`, chip(t.result, t.result)]), ["Window", "Topic proposed by the model", "Range", "Our check"])
      + `<h3>Final topics after normalisation</h3>` + table(tf.map(t => [esc(t.topic_id), esc(t.title), `${esc(t.start_segment)} to ${esc(t.end_segment)}`, `${ts(t.start)} to ${ts(t.end)}`]), ["Id", "Title", "Segments", "Time"]), false);

    // 6 topic minutes
    const tc = O.topic_claims || [];
    h += sect("6. Topic minutes: bullets written by the model, each checked", tc.length,
      `<p class="muted">The writer sees one topic's transcript plus its verified records. Each bullet must cite its sources, add no numbers, and then pass an independent checker.</p>`
      + tf.map(t => `<div class="stage"><header><b>${esc(t.topic_id)}</b> ${esc(t.title)}</header>${callsOf("summarize:" + t.topic_id).map(callBlock).join("")}
        ${table(tc.filter(c => c.ref === t.topic_id).map(c => [esc(c.text), `<span class="muted">${esc((c.item_ids || []).join(", "))}</span>`, chip(c.result, c.result) + (c.reason ? `<div class="muted">${esc(c.reason)}</div>` : "")]), ["Bullet", "Cites items", "Check"])}</div>`).join("")
      + `<details class="inner"><summary>Claim-checker calls</summary>${callsOf("verify_topic_claims").map(callBlock).join("") || "<span class='muted'>none</span>"}</details>`, true);

    // 7 executive
    const xc = O.executive_claims || [];
    h += sect("7. Executive summary: sentences written by the model, each checked", xc.length,
      callsOf("executive").map(callBlock).join("")
      + table(xc.map(c => [esc(c.text), `<span class="muted">${esc((c.item_ids || []).concat(c.topic_ids || []).join(", "))}</span>`, chip(c.result, c.result) + (c.reason ? `<div class="muted">${esc(c.reason)}</div>` : "")]), ["Sentence", "Cites", "Check"])
      + `<details class="inner"><summary>Claim-checker calls</summary>${callsOf("verify_executive").map(callBlock).join("") || "<span class='muted'>none</span>"}</details>`, true);

    // 8 call log
    h += sect("8. All model calls", calls.length, `<div class="scroll">${table(calls.map(c => [c.call_id, esc(c.node), esc(c.ref), esc(c.model), esc(c.effort),
      c.cache_hit ? "cache" : (c.prompt_tokens ?? ""), c.cache_hit ? "" : (c.completion_tokens ?? ""), (c.secs || 0).toFixed(1), esc(c.error || "")]), ["#", "Stage", "Ref", "Model", "Effort", "Tokens in", "Tokens out", "Secs", "Error"])}</div>`, false);
    return h;
  }

  /* ------------------------------------------------------------------ phase 3: final report */
  const REG = [["decisions", "Decisions"], ["action_items", "Action items"], ["open_questions", "Open questions"], ["proposals", "Proposals considered"],
    ["disagreements", "Disagreements"], ["risks", "Risks and concerns"], ["key_figures", "Key figures and facts"]];

  let FLAGGED = new Set();  // fact items whose figure conflicts with another figure in the meeting
  const refLinks = ids => (ids || []).map(i => `<a href="#item-${esc(i)}" onclick="App.focusItem('${esc(i)}');return false;">${esc(i)}</a>`
    + (FLAGGED.has(i) ? ' <span class="flag" title="This figure is flagged for review: see Key figures">&#9888;</span>' : "")).join(" ");

  function itemHtml(i) {
    const who = i.speakers.length ? esc(i.speakers.join(", ")) : "unknown speaker";
    let extra = "";
    if (i.lens === "action") {
      const o = i.attrs.owner || {}, d = i.attrs.deadline;
      extra = `<div>Owner: ${o.text ? `<b>${esc(o.text)}</b> <span class="muted">(${esc((o.basis || "").replace(/_/g, " "))})</span>` : '<span class="muted">unspecified</span>'} &nbsp;|&nbsp; Deadline: ${d ? `<b>${esc(d)}</b>` : '<span class="muted">unspecified</span>'}</div>`;
    }
    if (i.lens === "fact") {
      extra = `<div>${esc(i.attrs.label)}: <b>${esc(i.attrs.value)}</b></div>` + (i.attrs.flags || []).map(f => `<div class="flag">&#9888; ${esc(f)}</div>`).join("");
    }
    if (i.superseded_by) extra += `<div class="muted">Replaced by ${refLinks([i.superseded_by])}</div>`;
    if (i.resolved_by) extra += `<div class="muted">Answered by ${refLinks([i.resolved_by])}</div>`;
    const notes = (i.verification.notes || []).map(n => `<div class="muted">${esc(n)}</div>`).join("");
    return `<div class="item" id="item-${esc(i.item_id)}"><div class="item-h"><code>${esc(i.item_id)}</code> ${chip(i.status === "superseded" ? "warn" : "", i.status)}
      <span class="item-t">${esc(i.lens === "fact" ? i.text : i.text)}</span><span class="muted">${who} &middot; ${ts(i.start)}</span></div>${extra}
      <details class="inner"><summary>Evidence (${i.evidence.length})</summary>${i.evidence.map(e => `<div class="quote">&ldquo;${esc(e.quote)}&rdquo; <span class="muted">&mdash; ${esc(e.speaker_name || e.speaker)}, ${ts(e.start)}, ${esc(e.segment_id)}</span></div>`).join("")}${notes}</details></div>`;
  }

  function finalReport(M) {
    const t = M.trace_summary || {}, id = S.id;
    FLAGGED = new Set(M.items.filter(i => i.lens === "fact" && (i.attrs.flags || []).length).map(i => i.item_id));
    const counts = REG.map(([k, label]) => `${label}: <b>${M.registers[k].length}</b>`).join(" &nbsp;&middot;&nbsp; ");
    let h = "";
    if (M.stale) h += '<div class="note">The refined transcript or speaker names changed after these minutes were generated. Use &ldquo;Regenerate minutes&rdquo; to update them.</div>';
    if (M.status === "partial") h += '<div class="note"><b>Incomplete.</b> Some steps failed or fell back (see the notes below). Parts of this report may be missing.</div>';
    h += `<p class="muted">${ts(M.meta.duration_seconds)} recording &middot; ${M.meta.segments} segments &middot; run <code>${esc(M.meta.run_id)}</code> &middot; models: ${esc([...new Set(Object.values(M.meta.models))].join(", "))}</p>
      <p>${counts}</p>
      <p>Download: <a href="/jobs/${id}/minutes/download?format=md">minutes.md</a> &middot; <a href="/jobs/${id}/minutes/download?format=json">minutes.json</a> &middot; <a href="/jobs/${id}/minutes/download?format=csv">action_items.csv</a> &middot;
      <a href="/jobs/${id}/minutes/download?format=stages">step log</a> &middot; <a href="/jobs/${id}/minutes/download?format=trace">trace</a></p>`;
    h += "<h3>Executive summary</h3>" + (M.executive_summary.length
      ? `<ul class="summary-list">${M.executive_summary.map(c => `<li>${esc(c.text)} <span class="refs">${refLinks(c.item_ids)}</span></li>`).join("")}</ul>`
      : '<p class="muted">No verified outcomes were extracted for this meeting.</p>');
    h += "<h3>Participants</h3>" + (M.participants.length ? table(M.participants.map(p => [esc(p.name || p.speaker) + (p.name ? ` <span class="muted">(${esc(p.speaker)})</span>` : ""), ts(p.talk_seconds) + " of speech", p.segments + " segments"])) : '<p class="muted">Speaker labels were not available.</p>');

    const total = Math.max(1, M.meta.duration_seconds);
    h += "<h3>Topics</h3>" + `<div class="timeline">${M.topics.map(tp => `<button style="flex:${Math.max(4, (tp.end - tp.start) / total * 100)}" title="${esc(tp.title)} (${ts(tp.start)}-${ts(tp.end)})" onclick="App.focusTopic('${esc(tp.topic_id)}')">${esc(tp.title)}</button>`).join("")}</div>`;
    h += M.topics.map(tp => `<div class="item" id="topic-${esc(tp.topic_id)}"><div class="item-h"><code>${esc(tp.topic_id)}</code><span class="item-t">${esc(tp.title)}</span><span class="muted">${ts(tp.start)} to ${ts(tp.end)}</span></div>
      <ul class="summary-list">${tp.claims.map(c => `<li>${esc(c.text)} <span class="refs">${refLinks(c.item_ids)}</span></li>`).join("") || '<li class="muted">No verified narrative for this topic.</li>'}</ul>
      ${tp.note ? `<div class="muted">${esc(tp.note)}</div>` : ""}</div>`).join("");

    for (const [k, label] of REG) {
      const list = M.registers[k];
      h += sect(label, list.length, list.length ? list.map(itemHtml).join("") : '<p class="muted">None recorded in this meeting.</p>', list.length > 0, "reg-" + k);
    }
    if (M.needs_review.length) h += sect("Items needing review", M.needs_review.length, `<p class="muted">Quote-checked, but the verifier did not confirm them. They are not used in the summaries.</p>`
      + M.needs_review.map(i => itemHtml(i).replace('class="item"', 'class="item" style="opacity:.8"')).join(""), false);
    if (M.relations.length) h += sect("Relations between items", M.relations.length, table(M.relations.map(r => [`${refLinks([r.source])} <b>${esc(r.kind)}</b> ${refLinks([r.target])}`, `<span class="muted">&ldquo;${esc(r.quote)}&rdquo;</span>`])), false);
    h += sect("Notes and limitations", M.warnings.length, M.warnings.length ? `<ul class="summary-list">${M.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : "<p class='muted'>None.</p>", M.warnings.length > 0);

    const maxSec = Math.max(0.001, ...Object.values(t.node_seconds || {}));
    h += sect("Run trace", null, `<p class="muted">${t.llm_calls} model calls &middot; ${t.cache_hits} reused from cache &middot; ${t.failed_calls} failed &middot; ${t.rate_limited} rate-limit retries &middot; ${t.prompt_tokens} tokens in / ${t.completion_tokens} out &middot; wall time ${t.wall_seconds}s</p>
      <div class="bars">${Object.entries(t.node_seconds || {}).map(([n, s]) => `<div class="bar"><span class="n">${esc(STEP_LABELS[n] || n)}</span><span class="b" style="width:${Math.max(2, s / maxSec * 260)}px"></span><span>${s}s</span></div>`).join("")}</div>
      ${table(Object.entries(t.by_model || {}).map(([m, v]) => [esc(m), v.calls, v.prompt_tokens, v.completion_tokens]), ["Model", "Calls", "Tokens in", "Tokens out"])}`, false);
    return h;
  }

  function focusItem(id) {
    const el = $("item-" + id);
    if (!el) return;
    let p = el.parentElement;
    while (p) { if (p.tagName === "DETAILS") p.open = true; p = p.parentElement; }
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 1800);
  }
  function focusTopic(id) { const el = $("topic-" + id); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }

  document.addEventListener("DOMContentLoaded", init);
  return { decide, decideName, renameSpeaker, focusItem, focusTopic, state: S, showAll, stagesHtml, finalReport, renderPhase3 };
})();
