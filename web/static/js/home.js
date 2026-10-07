"use strict";
/* New meeting: one dropzone, the file's real details, and the meetings already processed. */
(function () {
  const { esc, $, $$, ts, size, api } = App;
  const OK_EXT = ["wav", "mp3", "m4a", "mp4", "flac", "ogg", "opus", "webm", "aac"];
  const STATUS = { uploaded: "Starting", transcribing: "Processing", transcript_ready: "Transcript ready", speakers_ready: "Speakers to review", refining: "Processing", refinement_ready: "Corrections to review",
    generating_report: "Writing the report", report_ready: "Record ready", failed: "Needs attention" };
  App.statusText = s => STATUS[s] || s;

  App.views.home = {
    async render(root) {
      let list = [];
      try { list = await api("/meetings"); } catch (e) { /* the list is optional */ }
      root.innerHTML = `<div class="home"><section>
          <div class="eyebrow">New meeting</div>
          <h1>Turn a meeting recording into a verified, <i>structured record</i>.</h1>
          <p class="lede">Upload an English-language recording. You review the speakers and any proposed corrections before the meeting record is written, and every item in it points back to what was said.</p>
          <div class="drop" id="drop"><b>Drop a recording here</b><p>or choose a file. WAV, MP3, M4A, FLAC, OGG, OPUS, WebM, AAC.</p>
            <button class="btn" id="browse" type="button">Browse files</button><input type="file" id="file" class="hide" accept=".wav,.mp3,.m4a,.mp4,.flac,.ogg,.opus,.webm,.aac,audio/*"></div>
          <div id="chosen"></div>
          <div class="opts">
            <label class="field"><span class="eyebrow">Meeting date (optional)</span><input type="date" id="mdate"><span class="muted" style="font-size:12px">Lets “by Friday” become a calendar date. Left empty, no date is assumed.</span></label>
            <label><input type="checkbox" id="review" checked> <span><b style="font-weight:500">Pause for review</b><br><span class="muted" style="font-size:13px">Stop after the speakers and after the proposed corrections so you can confirm them. Untick to run straight through.</span></span></label>
          </div>
          <div class="row"><button class="btn primary" id="go" disabled>Process recording</button><span id="progress" class="muted mono" style="font-size:12.5px"></span></div>
        </section>
        <aside class="recent"><h2 class="eyebrow">Recent meetings</h2>
          ${list.length ? `<div class="list">${list.map(m => `<a class="item" href="#/m/${esc(m.id)}"><span class="t">${esc(m.filename)}${m.example ? ' <span class="tag">Example</span>' : ""}</span><span class="tag ${m.status === "failed" ? "err" : m.status === "report_ready" ? "ok" : /ready|review/i.test(App.statusText(m.status)) ? "amber" : ""}">${esc(App.statusText(m.status))}</span>
              <span class="muted mono" style="font-size:12px">${esc(m.date)}${m.duration_seconds ? " · " + ts(m.duration_seconds) : ""}</span></a>`).join("")}</div>`
            : `<div class="empty"><b>No meeting has been processed.</b>Upload a recording to create a meeting record.</div>`}
        </aside></div>`;
      let file = null;
      const drop = $("#drop", root), input = $("#file", root), go = $("#go", root), chosen = $("#chosen", root);
      const choose = f => {
        file = f;
        go.disabled = true;
        if (!f) { chosen.innerHTML = ""; return; }
        const ext = (f.name.split(".").pop() || "").toLowerCase();
        const supported = OK_EXT.includes(ext) || (f.type || "").startsWith("audio/");
        chosen.innerHTML = `<div class="file"><span class="name">${esc(f.name)}</span><span class="mono muted" style="font-size:12.5px">${size(f.size)} · ${esc(ext.toUpperCase() || "file")}${supported ? "" : ""}</span>
          <span class="muted" id="dur" style="font-size:13px">${supported ? "Reading duration…" : ""}</span><span></span></div>
          ${supported ? "" : `<div class="notice err">This file type is not supported. Choose a recording in one of the formats listed above.</div>`}`;
        if (f.size === 0) { chosen.insertAdjacentHTML("beforeend", `<div class="notice err">This file is empty. Choose a recording that contains audio.</div>`); return; }
        if (!supported) return;
        go.disabled = false;
        const a = new Audio(URL.createObjectURL(f));
        a.addEventListener("loadedmetadata", () => { const d = $("#dur", root); if (d && isFinite(a.duration)) d.textContent = `Duration ${ts(a.duration)}`; URL.revokeObjectURL(a.src); });
        a.addEventListener("error", () => { const d = $("#dur", root); if (d) d.textContent = "Duration will be read during processing"; });
      };
      $("#browse", root).onclick = () => input.click();
      drop.onclick = e => { if (e.target === drop || e.target.tagName === "B" || e.target.tagName === "P") input.click(); };
      input.onchange = () => choose(input.files[0]);
      ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
      ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
      drop.addEventListener("drop", e => { if (e.dataTransfer.files[0]) choose(e.dataTransfer.files[0]); });
      go.onclick = () => {
        if (!file) return;
        const fd = new FormData();
        fd.append("file", file);
        fd.append("mode", $("#review", root).checked ? "review" : "auto");
        if ($("#mdate", root).value) fd.append("meeting_date", $("#mdate", root).value);
        go.disabled = true;
        const x = new XMLHttpRequest();
        x.open("POST", "/meetings");
        x.upload.onprogress = e => { if (e.lengthComputable) $("#progress", root).textContent = `Uploading ${Math.round((100 * e.loaded) / e.total)}%`; };
        x.onload = () => {
          let b = null;
          try { b = JSON.parse(x.responseText); } catch (e) { /* ignore */ }
          if (x.status === 200 && b && b.id) { location.hash = `#/m/${b.id}`; return; }
          go.disabled = false;
          $("#progress", root).textContent = "";
          chosen.insertAdjacentHTML("beforeend", `<div class="notice err">${esc((b && b.detail) || "The upload failed. Check your connection and try again.")}</div>`);
        };
        x.onerror = () => { go.disabled = false; $("#progress", root).textContent = ""; chosen.insertAdjacentHTML("beforeend", `<div class="notice err">The server could not be reached. Check your connection and try again.</div>`); };
        x.send(fd);
      };
    },
  };
})();
