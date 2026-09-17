/* ============================================================================
   New-run launcher — a centred modal over the Runs list.

   Replaces navigating to the full /runs/new page for the common case: choose a
   workflow, configure it (guide + tree-search width for the guided one), and
   launch. Options come from /live-tests/options; the staging plan preview comes
   from /live-tests/plan/{guide}. On launch we go to the new run's live page
   (or, for the config-less sweep workflows that don't hand back a run id, the
   full launcher's live console). The <a href="/runs/new"> still works with no
   JS — this only intercepts the click.
   ============================================================================ */
(function () {
  "use strict";

  var dialog = document.getElementById("new-run-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return; // no-JS / old browser: link falls through

  var form = document.getElementById("new-run-form");
  var wfEl = document.getElementById("nr-workflows");
  var cfgEl = document.getElementById("nr-config");
  var errEl = document.getElementById("nr-error");
  var lockedEl = document.getElementById("nr-locked");
  var launchBtn = document.getElementById("nr-launch");

  var options = null; // cached { enabled, workflows, guides }
  var selectedWorkflow = null;
  var planCache = {};

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function showError(msg) { errEl.textContent = msg || ""; errEl.hidden = !msg; }

  // ---- open / close -------------------------------------------------------
  function open() {
    showError("");
    dialog.showModal();
    if (options) return;
    wfEl.innerHTML = '<p class="ld-loading">Loading workflows…</p>';
    cfgEl.innerHTML = "";
    fetch("/live-tests/options", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) { options = data; renderWorkflows(); })
      .catch(function () {
        wfEl.innerHTML = '<p class="ld-loading">Could not load run options. <a href="/runs/new">Open the full launcher →</a></p>';
      });
  }
  function close() { dialog.close(); }

  document.addEventListener("click", function (e) {
    var opener = e.target.closest && e.target.closest("[data-open-new-run]");
    if (opener) { e.preventDefault(); open(); return; }
    if (e.target.closest && e.target.closest("[data-nr-cancel]")) { e.preventDefault(); close(); }
  });
  // Backdrop click closes (a click whose target is the dialog element itself).
  dialog.addEventListener("click", function (e) { if (e.target === dialog) close(); });

  // ---- workflow chooser ---------------------------------------------------
  function renderWorkflows() {
    var locked = !options.enabled;
    lockedEl.hidden = !locked;
    launchBtn.disabled = locked;
    wfEl.innerHTML = options.workflows.map(function (w, i) {
      return (
        '<button type="button" class="ld-wf" role="radio" aria-checked="' + (i === 0 ? "true" : "false") +
        '" data-wf="' + esc(w.id) + '">' +
        '<span class="ld-wf-title">' + esc(w.title) + "</span>" +
        '<span class="ld-wf-meta">' + esc(w.surface) + " &middot; " + esc(w.method) + "</span>" +
        "</button>"
      );
    }).join("");
    selectWorkflow(options.workflows[0].id);
  }

  function selectWorkflow(id) {
    selectedWorkflow = options.workflows.filter(function (w) { return w.id === id; })[0];
    if (!selectedWorkflow) return;
    wfEl.querySelectorAll(".ld-wf").forEach(function (b) {
      b.setAttribute("aria-checked", b.getAttribute("data-wf") === id ? "true" : "false");
    });
    renderConfig();
    showError("");
  }

  wfEl.addEventListener("click", function (e) {
    var b = e.target.closest(".ld-wf");
    if (b) selectWorkflow(b.getAttribute("data-wf"));
  });

  // ---- per-workflow config ------------------------------------------------
  function guideOptionsMarkup() {
    var bySurface = {};
    options.guides.forEach(function (g) { (bySurface[g.surface] = bySurface[g.surface] || []).push(g); });
    var preferred = "sheets_skill_leak";
    var haveDefault = options.guides.some(function (g) { return g.id === preferred; });
    return Object.keys(bySurface).sort().map(function (surface) {
      var opts = bySurface[surface].map(function (g) {
        var label = g.title + (g.maturity ? "  ·  " + g.maturity : "");
        var sel = (haveDefault ? g.id === preferred : false) ? " selected" : "";
        return '<option value="' + esc(g.id) + '"' + sel + ">" + esc(label) + "</option>";
      }).join("");
      return '<optgroup label="' + esc(surface) + '">' + opts + "</optgroup>";
    }).join("");
  }

  function renderConfig() {
    var w = selectedWorkflow;
    var prereqs = (w.prerequisites || []).map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("");
    var prereqBlock = prereqs
      ? '<div class="ld-prereq"><span class="ld-lbl">Before launch</span><ul>' + prereqs + "</ul></div>"
      : "";
    if (!w.configurable) {
      cfgEl.innerHTML =
        '<p class="ld-desc">' + esc(w.description) + "</p>" + prereqBlock +
        '<p class="ld-note">This workflow runs a fixed configuration — nothing to set. It streams into the runs list as it goes.</p>';
      launchBtn.disabled = !options.enabled;
      return;
    }
    cfgEl.innerHTML =
      '<p class="ld-desc">' + esc(w.description) + "</p>" +
      '<label class="ld-field ld-field-wide"><span class="ld-lbl">Guide — what to test</span>' +
      '<select id="nr-guide">' + guideOptionsMarkup() + "</select></label>" +
      '<p class="ld-objective" id="nr-objective"></p>' +
      '<div class="ld-grid">' +
      field("Branching factor", "nr-bf", 3, 1, 5, "How many sibling attempts per node.") +
      field("Tree depth", "nr-depth", 6, 1, 12, "How many turns deep the search goes.") +
      field("Node budget / seed", "nr-nodes", 80, 1, 400, "Hard cap on nodes explored per seed.") +
      "</div>" +
      '<details class="ld-plan"><summary>Preview staging plan</summary><div id="nr-plan" class="ld-plan-body">Select a guide to preview.</div></details>' +
      prereqBlock;
    launchBtn.disabled = !options.enabled;
    var guideSel = document.getElementById("nr-guide");
    guideSel.addEventListener("change", onGuideChange);
    onGuideChange();
  }

  function field(label, id, val, min, max, hint) {
    return (
      '<label class="ld-field"><span class="ld-lbl">' + esc(label) + "</span>" +
      '<input id="' + id + '" type="number" inputmode="numeric" value="' + val +
      '" min="' + min + '" max="' + max + '">' +
      '<span class="ld-hint">' + esc(hint) + "</span></label>"
    );
  }

  function onGuideChange() {
    var id = document.getElementById("nr-guide").value;
    var g = options.guides.filter(function (x) { return x.id === id; })[0];
    var objEl = document.getElementById("nr-objective");
    if (objEl) objEl.textContent = g && g.objective ? g.objective : "";
    var planEl = document.getElementById("nr-plan");
    if (!planEl) return;
    if (planCache[id]) { planEl.innerHTML = planCache[id]; return; }
    planEl.textContent = "Loading plan…";
    fetch("/live-tests/plan/" + encodeURIComponent(id), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (plan) {
        var steps = (plan.steps || []).map(function (s) {
          return '<li><code>' + esc(s.op) + "</code> " + esc(s.intent) + "</li>";
        }).join("") || "<li>Direct chat, no out-of-band staging.</li>";
        var html =
          "<ol class=\"ld-plan-steps\">" + steps + "</ol>" +
          '<p class="ld-plan-tail">then ' + esc(plan.seed_turns) + " seed turn(s), then adaptive crescendo &rarr; " + esc(plan.target) + "</p>";
        planCache[id] = html;
        planEl.innerHTML = html;
      })
      .catch(function () { planEl.textContent = "Plan preview unavailable."; });
  }

  function intInRange(id, min, max, label) {
    var el = document.getElementById(id);
    if (!el) return null;
    var v = el.valueAsNumber;
    if (Number.isNaN(v)) throw label + " is required.";
    if (v < min || v > max) throw label + " must be between " + min + " and " + max + ".";
    return v;
  }

  // ---- launch -------------------------------------------------------------
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!selectedWorkflow || !options.enabled) return;
    showError("");
    var body = { confirmed: true, playbook: null, branching_factor: null, max_depth: null, max_nodes_per_seed: null };
    if (selectedWorkflow.configurable) {
      var guideSel = document.getElementById("nr-guide");
      if (!guideSel || !guideSel.value) { showError("Choose a guide to run."); return; }
      body.playbook = guideSel.value;
      try {
        body.branching_factor = intInRange("nr-bf", 1, 5, "Branching factor");
        body.max_depth = intInRange("nr-depth", 1, 12, "Tree depth");
        body.max_nodes_per_seed = intInRange("nr-nodes", 1, 400, "Node budget");
      } catch (msg) { showError(msg); return; }
    }
    launchBtn.disabled = true;
    launchBtn.textContent = "Launching…";
    fetch("/live-tests/" + encodeURIComponent(selectedWorkflow.id) + "/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json().then(function (p) { return { ok: r.ok, payload: p }; }); })
      .then(function (res) {
        if (!res.ok) {
          showError(res.payload.detail || "Could not launch the run.");
          launchBtn.disabled = false;
          launchBtn.textContent = "Launch run";
          return;
        }
        // Go to the new run's live page when we have its id; otherwise the full
        // launcher, whose console streams the sweep workflows that have none.
        window.location = res.payload.run_id ? "/runs/" + res.payload.run_id : "/runs/new";
      })
      .catch(function () {
        showError("Could not reach the server. Try the full launcher.");
        launchBtn.disabled = false;
        launchBtn.textContent = "Launch run";
      });
  });
})();
