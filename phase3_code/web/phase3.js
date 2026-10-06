// Source strings are rendered with textContent/esc; no model-supplied HTML runs.
let phase3Result = null, phase3Epoch = 0;
function invalidatePhase3() { phase3Epoch++; phase3Result = null; }

function mountPhase3(id) {
  phase3Epoch++;
  const epoch = phase3Epoch;
  phase3Result = null;
  const old = document.getElementById("phase3");
  if (old) old.remove();
  const panel = document.createElement("section");
  panel.id = "phase3";
  panel.style.marginTop = "24px";
  panel.innerHTML = '<h2>Phase 3 — Meeting minutes</h2><p><button id="sgo">Generate minutes — Phase 3</button> <span id="sstatus" class="muted"></span></p><div id="sout"></div>';
  document.getElementById("rout").appendChild(panel);
  document.getElementById("sgo").onclick = () => startPhase3(id, epoch);
  refreshPhase3(id, epoch).catch(e => { if (epoch === phase3Epoch) $("sstatus").textContent = e.message; });
}

async function phase3Fetch(url, options) {
  const r = await fetch(url, options);
  const body = await r.json();
  if (!r.ok) throw new Error(body.detail || "Phase 3 request failed");
  return body;
}

async function refreshPhase3(id, epoch) {
  const data = await phase3Fetch(`/jobs/${id}/summary`);
  if (epoch !== phase3Epoch) return data;
  $("sstatus").textContent = data.message || data.stage;
  if (data.result) renderPhase3(data.result, id);
  return data;
}

async function startPhase3(id, epoch) {
  $("sgo").disabled = true;
  $("sout").textContent = "";
  try {
    const current = await phase3Fetch(`/jobs/${id}/summary`);
    if (epoch !== phase3Epoch) return;
    await phase3Fetch(`/jobs/${id}/summarize`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source_revision: current.source_revision})});
    while (epoch === phase3Epoch) {
      const data = await refreshPhase3(id, epoch);
      if (["complete", "partial", "stale", "failed", "not_started"].includes(data.stage)) break;
      await new Promise(resolve => setTimeout(resolve, 1400));
    }
  } catch (e) {
    if (epoch === phase3Epoch) $("sstatus").textContent = e.message;
  } finally {
    if (epoch === phase3Epoch) $("sgo").disabled = false;
  }
}

function phase3Evidence(refs) {
  return [...new Set(refs.map(r => r.unit_id))].map(id => `<button class="sm" type="button" data-evidence="${esc(id)}">${esc(id)}</button>`).join(" ");
}

function showPhase3Evidence(id) {
  const u = phase3Result?.units.find(x => x.unit_id === id);
  if (!u) return;
  const panel = $("sevidence");
  panel.innerHTML = `<h3>Source evidence</h3><p>${ts(u.start_seconds)}–${ts(u.end_seconds)} · ${esc(u.speaker_name || u.speaker_id || "Unknown/mixed speaker")} · ${esc(u.segment_id)} · ${esc(u.timing_precision)}</p><blockquote>${esc(u.text)}</blockquote><p class="muted">Source characters ${u.start_char}:${u.end_char}; revision ${esc(u.source_revision.slice(0,12))}</p>`;
  panel.scrollIntoView({behavior: "smooth", block: "center"});
}

function renderPhase3(r, id) {
  phase3Result = r;
  const valid = r.events.filter(e => e.validation_status === "supported");
  const events = (kind) => valid.filter(e => e.kind === kind);
  const bullets = claims => "<ul>" + claims.map(c => `<li>${esc(c.text)} ${phase3Evidence(c.evidence)}</li>`).join("") + "</ul>";
  let h = r.manifest.status === "partial" ? '<p class="err">Incomplete processing. Use Generate minutes again to resume failed batches.</p>' : "";
  h += `<p class="muted">${r.manifest.total_units} source units · ${r.topics.length} topics · ${r.validation_report.supported_events} supported events · ${r.manifest.elapsed_seconds}s</p>`;
  h += sect("Executive summary", r.executive_summary.length, bullets(r.executive_summary), true);
  h += sect("Topic minutes", r.topics.length, r.topics.map(t => {
    const summary = r.topic_summaries.find(s => s.topic_id === t.topic_id);
    const share = t.duration_share == null ? "time unavailable" : (100*t.duration_share).toFixed(1)+"% of known speaker-time";
    return `<details><summary>${esc(t.title)} — ${share}; ${t.occurrences.length} occurrence(s)</summary>${bullets(summary?.claims || [])}<p>Topic evidence: ${phase3Evidence(t.unit_ids.slice(0,12).map(unit_id => ({unit_id})))}</p></details>`;
  }).join(""), true);
  const decisions = events("decision");
  h += sect("Decisions and history", decisions.length, items(decisions, e => [esc(e.status), esc(e.description), phase3Evidence(e.evidence)]), true);
  const actions = events("action");
  h += sect("Action items", actions.length, '<table><thead><tr><th>Task</th><th>Status</th><th>Owner as stated</th><th>Deadline as stated</th><th>Evidence</th></tr></thead><tbody>' + actions.map(e => `<tr><td>${esc(e.description)}</td><td>${esc(e.status)}</td><td>${esc(e.owner_text || e.owner_speaker_id || "Not stated")}</td><td>${esc(e.deadline_text || "Not stated")}</td><td>${phase3Evidence(e.evidence)}</td></tr>`).join("") + "</tbody></table>", true);
  const unresolved = valid.filter(e => (e.kind === "question" && e.status !== "resolved") || e.kind === "risk");
  h += sect("Open questions and stated risks", unresolved.length, items(unresolved, e => [esc(e.kind), esc(e.description), phase3Evidence(e.evidence)]), true);
  const revisions = r.relations.filter(x => ["SUPERSEDES", "CONTRADICTS"].includes(x.kind));
  const eventText = eid => r.events.find(e => e.event_id === eid)?.description || eid;
  h += sect("Revisions and unresolved conflicts", revisions.length, items(revisions, x => [esc(x.kind), esc(eventText(x.source)), esc(eventText(x.target)), phase3Evidence(x.evidence)]), true);
  h += '<details><summary>Knowledge graph — inspect a topic</summary><p><select id="sgtopic">' + r.topics.map(t => `<option value="${esc(t.topic_id)}">${esc(t.title)}</option>`).join("") + '</select></p><div id="sgraph"></div></details>';
  h += '<div id="sevidence" style="border:1px solid #ddd;padding:12px;margin-top:12px">Select an evidence button to inspect the accepted transcript.</div>';
  h += '<p>Download: ' + [["minutes","minutes.md"],["json","minutes.json"],["graph","knowledge_graph.json"],["actions","action_items.json"],["decisions","decisions.json"],["validation","validation_report.json"]].map(([key,label]) => `<a href="/jobs/${id}/phase3/download/${key}">${label}</a>`).join(" · ") + '</p>';
  h += sect("Validation and limitations", r.manifest.warnings.length, "<ul>" + r.manifest.warnings.map(w => `<li>${esc(w)}</li>`).join("") + "</ul>");
  $("sout").innerHTML = h;
  $("sout").onclick = e => { const button = e.target.closest("[data-evidence]"); if (button) showPhase3Evidence(button.dataset.evidence); };
  const drawGraph = () => {
    const topic = $("sgtopic").value;
    const selected = new Set(r.events.filter(e => e.topic_ids.includes(topic)).map(e => e.event_id));
    const nodes = new Map(r.knowledge_graph.nodes.map(n => [n.node_id, n]));
    const edges = r.knowledge_graph.edges.filter(e => selected.has(e.source) && e.kind !== "ABOUT");
    $("sgraph").innerHTML = '<p class="muted">Typed graph relations. Topic associations are model-derived; source buttons show the evidence. Review events are labelled.</p><table><tr><th>Record</th><th>Relation</th><th>Connected record</th><th>Evidence</th></tr>' + edges.map(e => `<tr><td>${esc(nodes.get(e.source)?.label)}${nodes.get(e.source)?.data?.validation_status === "needs_review" ? ' <span class="flag">(needs review)</span>' : ''}</td><td>${esc(e.kind)}</td><td>${esc(nodes.get(e.target)?.label)}</td><td>${phase3Evidence(e.evidence)}</td></tr>`).join("") + '</table>';
  };
  $("sgtopic").onchange = drawGraph;
  drawGraph();
}
