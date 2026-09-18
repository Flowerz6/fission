/* ============================================================================
   New-run launcher — a centred modal over the Runs list.

   Choose a Workspace surface (left), pick single-workspace or across-workspaces
   (whether the run drives a second account), choose a guide for that surface,
   set the tree-search width, and launch. Options come from /live-tests/options
   (guides keyed by surface, flagged single- vs across-workspaces, each with a
   one-line blurb). Every launch runs the guided-workspace workflow with
   the chosen guide and returns to the new run's live page. The <a href="/runs/
   new"> still works with no JS — this only intercepts the click.
   ============================================================================ */
(function () {
  "use strict";

  var dialog = document.getElementById("new-run-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return; // no-JS / old browser: link falls through

  var form = document.getElementById("new-run-form");
  var wsEl = document.getElementById("nr-workspaces");
  var cfgEl = document.getElementById("nr-config");
  var errEl = document.getElementById("nr-error");
  var lockedEl = document.getElementById("nr-locked");
  var launchBtn = document.getElementById("nr-launch");

  var GUIDED_WORKFLOW = "guided-workspace-playbook";
  var ICONS = {
    docs: "google-docs", sheets: "google-sheets", gmail: "google-gmail",
    slides: "google-slides", drive: "google-drive", calendar: "google-calendar",
  };

  var options = null; // { enabled, guides:[{id,title,surface,surface_label,multi_workspace,second_accounts,maturity,blurb}] }
  var mode = "single"; // "single" | "multi"  (single-workspace | across-workspaces)
  var workspace = null; // surface key
  var guideId = null;

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
    wsEl.innerHTML = "";
    cfgEl.innerHTML = '<p class="ld-loading">Loading…</p>';
    fetch("/live-tests/options", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) { options = data; init(); })
      .catch(function () {
        cfgEl.innerHTML = '<p class="ld-loading">Could not load run options. <a href="/runs/new">Open the full launcher →</a></p>';
      });
  }
  function close() { dialog.close(); }

  document.addEventListener("click", function (e) {
    var opener = e.target.closest && e.target.closest("[data-open-new-run]");
    if (opener) { e.preventDefault(); open(); return; }
    if (e.target.closest && e.target.closest("[data-nr-cancel]")) { e.preventDefault(); close(); }
  });
  dialog.addEventListener("click", function (e) { if (e.target === dialog) close(); });

  // ---- data helpers -------------------------------------------------------
  function guidesFor(m) {
    return options.guides.filter(function (g) { return !!g.multi_workspace === (m === "multi"); });
  }
  function surfacesFor(m) {
    var seen = {}, order = [];
    guidesFor(m).forEach(function (g) {
      if (!seen[g.surface]) { seen[g.surface] = g.surface_label; order.push(g.surface); }
    });
    return order.map(function (k) { return { key: k, label: seen[k] }; });
  }
  function guidesIn(m, surface) {
    return guidesFor(m).filter(function (g) { return g.surface === surface; });
  }

  function init() {
    lockedEl.hidden = !!options.enabled;
    launchBtn.disabled = !options.enabled;
    var surfaces = surfacesFor(mode);
    workspace = surfaces.length ? surfaces[0].key : null;
    render();
  }

  // ---- render -------------------------------------------------------------
  function render() {
    var surfaces = surfacesFor(mode);
    if (!surfaces.some(function (s) { return s.key === workspace; })) {
      workspace = surfaces.length ? surfaces[0].key : null;
    }
    wsEl.innerHTML = surfaces.map(function (s) {
      var icon = ICONS[s.key]
        ? '<img class="ld-ws-icon" src="/static/product-icons/' + ICONS[s.key] + '.svg" alt="">'
        : '<span class="ld-ws-icon ld-ws-icon-x">' + esc(s.label.slice(0, 1)) + "</span>";
      return (
        '<button type="button" class="ld-ws" role="radio" aria-checked="' + (s.key === workspace) +
        '" data-ws="' + esc(s.key) + '">' + icon + "<span>" + esc(s.label) + "</span></button>"
      );
    }).join("") || '<p class="ld-loading">None.</p>';
    renderConfig();
  }

  function renderConfig() {
    var guides = workspace ? guidesIn(mode, workspace) : [];
    if (!guides.some(function (g) { return g.id === guideId; })) {
      guideId = guides.length ? guides[0].id : null;
    }
    var modeToggle =
      '<div class="ld-modes" role="tablist" aria-label="Scope">' +
      '<button type="button" role="tab" class="ld-mode' + (mode === "single" ? " on" : "") +
      '" data-mode="single" aria-selected="' + (mode === "single") + '">Single workspace</button>' +
      '<button type="button" role="tab" class="ld-mode' + (mode === "multi" ? " on" : "") +
      '" data-mode="multi" aria-selected="' + (mode === "multi") + '">Across workspaces</button></div>';

    if (!guides.length) {
      cfgEl.innerHTML =
        modeToggle +
        '<p class="ld-empty">No ' + (mode === "multi" ? "cross-workspace" : "single-workspace") +
        " guides for this surface yet.</p>";
      launchBtn.disabled = true;
      return;
    }
    var opts = guides.map(function (g) {
      var label = g.title + (g.maturity ? "  ·  " + g.maturity : "");
      return '<option value="' + esc(g.id) + '"' + (g.id === guideId ? " selected" : "") + ">" + esc(label) + "</option>";
    }).join("");
    cfgEl.innerHTML =
      modeToggle +
      '<label class="ld-field ld-field-wide"><span class="ld-lbl">Guide</span>' +
      '<select id="nr-guide">' + opts + "</select></label>" +
      '<p class="ld-blurb" id="nr-blurb"></p>' +
      '<div class="ld-grid">' +
      field("Branching", "nr-bf", 3, 1, 5) +
      field("Depth", "nr-depth", 6, 1, 12) +
      field("Node budget", "nr-nodes", 80, 1, 400) +
      "</div>";
    launchBtn.disabled = !options.enabled;
    document.getElementById("nr-guide").addEventListener("change", function (e) {
      guideId = e.target.value; updateBlurb();
    });
    updateBlurb();
  }

  function field(label, id, val, min, max) {
    return (
      '<label class="ld-field"><span class="ld-lbl">' + esc(label) + "</span>" +
      '<input id="' + id + '" type="number" inputmode="numeric" value="' + val +
      '" min="' + min + '" max="' + max + '"></label>'
    );
  }

  function updateBlurb() {
    var el = document.getElementById("nr-blurb");
    if (!el) return;
    var g = options.guides.filter(function (x) { return x.id === guideId; })[0];
    var text = g ? g.blurb : "";
    if (g && g.second_accounts && g.second_accounts.length) {
      text += (text ? "  ·  " : "") + "Across workspaces: needs a second signed-in account (" +
        g.second_accounts.join(", ") + ").";
    }
    el.textContent = text;
  }

  // Delegated: workspace pick + mode switch (both re-render the panel).
  wsEl.addEventListener("click", function (e) {
    var b = e.target.closest(".ld-ws");
    if (b) { workspace = b.getAttribute("data-ws"); render(); showError(""); }
  });
  cfgEl.addEventListener("click", function (e) {
    var m = e.target.closest(".ld-mode");
    if (m) { mode = m.getAttribute("data-mode"); render(); showError(""); }
  });

  function intInRange(id, min, max, label) {
    var el = document.getElementById(id);
    var v = el ? el.valueAsNumber : NaN;
    if (Number.isNaN(v)) throw label + " is required.";
    if (v < min || v > max) throw label + " must be between " + min + " and " + max + ".";
    return v;
  }

  // ---- launch -------------------------------------------------------------
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!options || !options.enabled || !guideId) { showError("Choose a guide to run."); return; }
    showError("");
    var body = { confirmed: true, playbook: guideId };
    try {
      body.branching_factor = intInRange("nr-bf", 1, 5, "Branching");
      body.max_depth = intInRange("nr-depth", 1, 12, "Depth");
      body.max_nodes_per_seed = intInRange("nr-nodes", 1, 400, "Node budget");
    } catch (msg) { showError(msg); return; }
    launchBtn.disabled = true;
    launchBtn.textContent = "Launching…";
    fetch("/live-tests/" + GUIDED_WORKFLOW + "/start", {
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
        window.location = res.payload.run_id ? "/runs/" + res.payload.run_id : "/runs/new";
      })
      .catch(function () {
        showError("Could not reach the server. Try the full launcher.");
        launchBtn.disabled = false;
        launchBtn.textContent = "Launch run";
      });
  });
})();
