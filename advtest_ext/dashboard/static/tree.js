/* ============================================================================
   Attack-tree explorer (Cytoscape.js) — live client.

   Replaces the old Mermaid `graph TD` live tree. A cell navigator (left)
   drives one Cytoscape canvas per (behaviour, technique) cell (right).

   Design goals, per the approved mock:
     * round-rectangle nodes, dagre TB layout, the exact status palette;
     * lazy mount — a cell's graph is only built the first time its panel is
       shown (Cytoscape renders blank in a hidden / zero-size container);
     * NO full repaint on live runs. While the run is in flight the client
       polls `/runs/{id}/_tree.json` and INCREMENTALLY `cy.add()`s only new
       nodes/edges (and re-classes nodes whose verdict changed), re-running
       dagre with animation and re-fitting — so branches settle smoothly.

   Fully self-contained: the vendored libraries live under /static/vendor and
   are injected once (guarded on window.cytoscape); init is idempotent so a
   re-inserted partial never stacks Cytoscape instances.
   ============================================================================ */
(function () {
  "use strict";

  var VENDOR = "/static/vendor/";

  // ---- status palette (approved hexes; committed for both themes) --------
  var COL = {
    success: { bg: "#e8f3ee", br: "#57a880", tx: "#1f5a3c" },
    partial: { bg: "#f8f0dd", br: "#cf9a4c", tx: "#7a5716" },
    refused: { bg: "#faebec", br: "#d5595b", tx: "#8a3030" },
    pruned:  { bg: "#f1eaf5", br: "#9b6aaa", tx: "#64456f" },
    // The synthetic playbook root — a warm neutral "container" look, distinct
    // from the coloured verdict nodes it fans out into.
    root:    { bg: "#f3efe7", br: "#c7b49f", tx: "#5a5045" },
  };
  function col(s) { return COL[s] || COL.refused; }

  var STY = [
    { selector: "node", style: {
        "shape": "round-rectangle", "width": 134, "height": 50,
        "label": "data(label)", "text-wrap": "wrap", "text-max-width": 120,
        "text-valign": "center", "text-halign": "center",
        "font-family": "ui-sans-serif, system-ui", "font-size": 10,
        "font-weight": 600, "line-height": 1.25,
        "background-color": function (e) { return col(e.data("status")).bg; },
        "border-color": function (e) { return col(e.data("status")).br; },
        "color": function (e) { return col(e.data("status")).tx; },
        "border-width": 1.5,
        "border-style": function (e) { return e.data("status") === "pruned" ? "dashed" : "solid"; },
    } },
    { selector: "node.sel", style: { "border-width": 3 } },
    { selector: "node.focus", style: {
        "border-width": 3.5, "border-color": "#c0472f",
        "shadow-blur": 20, "shadow-color": "#ee7a6b", "shadow-opacity": 0.55,
    } },
    { selector: "edge", style: {
        "width": 2, "line-color": "#e2dfe9",
        "curve-style": "bezier", "target-arrow-shape": "none",
    } },
  ];

  var LAYOUT = { name: "dagre", rankDir: "TB", nodeSep: 24, rankSep: 48, fit: true, padding: 28 };

  // ---- vendored-library loader (inject once, then flush the boot queue) ---
  var _libState = "idle"; // idle | loading | ready
  var _libQueue = [];

  function injectScript(src, cb) {
    var s = document.createElement("script");
    s.src = src;
    s.async = false;
    s.onload = cb;
    s.onerror = function () { console.error("attack-tree: failed to load " + src); };
    document.head.appendChild(s);
  }

  function ensureLibs(cb) {
    if (_libState === "ready" || (window.cytoscape && window.__cyDagreReady)) {
      _libState = "ready";
      cb();
      return;
    }
    _libQueue.push(cb);
    if (_libState === "loading") return;
    _libState = "loading";
    var finish = function () {
      try {
        if (window.cytoscape && window.cytoscapeDagre && !window.__cyDagreReady) {
          window.cytoscape.use(window.cytoscapeDagre);
          window.__cyDagreReady = true;
        }
      } catch (e) { console.error("attack-tree: dagre registration failed", e); }
      _libState = "ready";
      var q = _libQueue; _libQueue = [];
      q.forEach(function (fn) { try { fn(); } catch (e) { console.error(e); } });
    };
    // dagre → cytoscape → cytoscape-dagre (dagre must exist before the ext).
    var steps = [];
    if (!window.dagre) steps.push(VENDOR + "dagre.min.js");
    if (!window.cytoscape) steps.push(VENDOR + "cytoscape.min.js");
    if (!window.cytoscapeDagre) steps.push(VENDOR + "cytoscape-dagre.js");
    (function next(i) {
      if (i >= steps.length) { finish(); return; }
      injectScript(steps[i], function () { next(i + 1); });
    })(0);
  }

  // ---- helpers ------------------------------------------------------------
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function nodeDetail(panel, d) {
    var el = panel.querySelector(".tree-detail");
    if (!el) return;
    var c = col(d.status);
    var scoreLabel = (d.score == null) ? d.status : (Number(d.score).toFixed(2) + " · " + d.status);
    var name = String(d.label || "").split("\n")[0];
    var link = d.trace_id
      ? '<a class="td-link" href="/traces/' + esc(d.trace_id) + '">open full trace &rarr;</a>'
      : "";
    var body;
    if (d.email || d.injection) {
      body = '<div class="td-transcript">' +
        (d.email ? '<div><span class="u">delivered email:</span> ' + esc(d.email) + "</div>" : "") +
        (d.injection ? '<div><span class="a">hidden white-text injection:</span> ' + esc(d.injection) + "</div>" : "") +
        "</div>";
    } else if (d.u || d.a) {
      body = '<div class="td-transcript">' +
        (d.u ? '<div><span class="u">prompt:</span> ' + esc(d.u) + "</div>" : "") +
        (d.a ? '<div><span class="a">response:</span> ' + esc(d.a) + "</div>" : "") +
        "</div>";
    } else {
      body = '<p class="td-hint">No inline transcript captured for this node — open the full trace for detail.</p>';
    }
    el.innerHTML =
      '<div class="td-h"><span class="td-t">' + esc(name) + "</span>" +
      '<span class="td-pill" style="color:' + c.br + ";background:" + c.bg + '">' + esc(scoreLabel) + "</span>" +
      link + "</div>" + body;
  }

  function resetDetail(panel) {
    var el = panel.querySelector(".tree-detail");
    if (el) {
      el.innerHTML = '<div class="td-h"><span class="td-t">Select a node</span></div>' +
        '<p class="td-hint">Each node is one conversation turn. Select a node to inspect its prompt, response, verdict, and evidence.</p>';
    }
  }

  // ---- per-explorer controller -------------------------------------------
  function setup(root) {
    if (root.__attackTreeReady) return;   // idempotent
    root.__attackTreeReady = true;

    var runId = root.getAttribute("data-run-id");
    var live = root.getAttribute("data-live") === "1";
    var panelsById = {};   // cellKey -> panel controller
    var order = [];        // cellKey in nav order
    var currentKey = null; // the cell the user is viewing

    root.querySelectorAll(".tree-panel").forEach(function (panelEl) {
      var key = panelEl.getAttribute("data-cell-key");
      var dataEl = panelEl.querySelector("script.cell-data");
      var initial = { nodes: [], edges: [] };
      try { initial = JSON.parse(dataEl.textContent || "{}"); } catch (e) { /* keep empty */ }
      var ctrl = {
        el: panelEl,
        key: key,
        cy: null,
        nodeIds: {},
        edgeIds: {},
        nodes: initial.nodes || [],
        edges: initial.edges || [],
        dirty: false,
      };
      (ctrl.nodes).forEach(function (n) { ctrl.nodeIds[n.data.id] = n.data.status; });
      (ctrl.edges).forEach(function (e) { ctrl.edgeIds[e.data.id] = true; });
      panelsById[key] = ctrl;
      order.push(key);
    });

    function mount(ctrl) {
      if (ctrl.cy) return;
      var mountEl = ctrl.el.querySelector(".cy-mount");
      if (!mountEl) return;
      ctrl.cy = window.cytoscape({
        container: mountEl,
        elements: { nodes: ctrl.nodes, edges: ctrl.edges },
        style: STY,
        layout: Object.assign({}, LAYOUT, { animate: false }),
        minZoom: 0.4, maxZoom: 2.2, wheelSensitivity: 0.22,
        // Wheel zoom is on (the earlier "drift" was a grid-overflow layout bug,
        // fixed in tree.css). Nodes aren't individually draggable; box-select off.
        boxSelectionEnabled: false,
        autoungrabify: true,
      });
      // Keep the renderer in sync if the container is ever resized (layout
      // reflow, window resize) so the graph never appears shifted or clipped.
      if (window.ResizeObserver) {
        var ro = new ResizeObserver(function () {
          if (ctrl.cy) ctrl.cy.resize();
        });
        ro.observe(mountEl);
        ctrl._ro = ro;
      }
      // Once the user zooms/pans, remember it so htmx re-asserts and live
      // updates don't yank the viewport back to fit. The Fit button clears it.
      mountEl.addEventListener("wheel", function () { ctrl.userZoomed = true; }, { passive: true });
      ctrl.cy.on("dragpan", function () { ctrl.userZoomed = true; });
      ctrl.cy.on("tap", "node", function (e) {
        ctrl.cy.nodes().removeClass("sel focus");
        e.target.addClass("sel");
        nodeDetail(ctrl.el, e.target.data());
      });
    }

    function showPanelByKey(key, userAction) {
      currentKey = key;
      root.querySelectorAll(".tree-panel").forEach(function (p) {
        p.classList.toggle("active", p.getAttribute("data-cell-key") === key);
      });
      root.querySelectorAll(".cell-item").forEach(function (c) {
        c.classList.toggle("active", c.getAttribute("data-cell-key") === key);
      });
      var ctrl = panelsById[key];
      if (!ctrl) return;
      mount(ctrl);
      // The container was display:none until now; give Cytoscape its size.
      requestAnimationFrame(function () {
        if (!ctrl.cy) return;
        ctrl.cy.resize();
        if (ctrl.dirty) {
          ctrl.cy.layout(Object.assign({}, LAYOUT, { animate: false })).run();
          ctrl.dirty = false;
        }
        // Fit ONLY on first reveal or a deliberate cell switch — never on an
        // htmx re-assert. Otherwise a poll on another run-page partial fires
        // htmx:afterSettle every ~2s, re-runs this, and resets the user's zoom
        // ("shrinks back"). Don't override a zoom the user set, either.
        if (!ctrl.shown || (userAction && !ctrl.userZoomed)) {
          ctrl.cy.fit(undefined, 28);
        }
        ctrl.shown = true;
      });
    }

    // Navigator + fit-button clicks (delegated on the explorer root).
    root.addEventListener("click", function (e) {
      var item = e.target.closest && e.target.closest(".cell-item");
      if (item && root.contains(item)) {
        showPanelByKey(item.getAttribute("data-cell-key"), true);
        return;
      }
      var fit = e.target.closest && e.target.closest("[data-tree-fit]");
      if (fit && root.contains(fit)) {
        var panel = fit.closest(".tree-panel");
        var ctrl = panel && panelsById[panel.getAttribute("data-cell-key")];
        if (ctrl && ctrl.cy) {
          ctrl.userZoomed = false;  // user asked to reset the view
          ctrl.cy.animate({ fit: { padding: 28 } }, { duration: 260 });
        }
        return;
      }
    });

    // Open the first cell. `__reassert` re-applies our selection after an
    // HTMX settle — other live partials on the run page fire htmx:afterSettle,
    // and legacy global handlers may reset the active panel to the first one;
    // this keeps the tree on the cell the user is actually viewing.
    if (order.length) showPanelByKey(order[0]);
    root.__reassert = function () { if (currentKey) showPanelByKey(currentKey); };

    // ---- live incremental polling ----------------------------------------
    if (!live || !runId) return;

    var timer = null;
    var structuralRebuildInFlight = false;

    function applyCell(cell) {
      var ctrl = panelsById[cell.key];
      if (!ctrl) return false;  // new cell — signal a structural rebuild

      // Navigator + head counts (cheap, no graph repaint).
      var navItem = root.querySelector('.cell-item[data-cell-key="' + cssEsc(cell.key) + '"]');
      if (navItem) {
        var n = navItem.querySelector(".ci-n");
        if (n) n.textContent = cell.n_success + "/" + cell.n_traces;
        var bar = navItem.querySelector(".ci-bar span");
        if (bar) {
          var pct = cell.n_traces ? Math.round((cell.n_success / cell.n_traces) * 100) : 0;
          bar.style.width = pct + "%";
          bar.style.background = pct >= 66 ? "#d5595b" : (pct >= 33 ? "#cf9a4c" : "#948f9f");
        }
      }
      var head = ctrl.el.querySelector(".tree-panel-head .th-count");
      if (head) head.textContent = cell.n_success + "/" + cell.n_traces + " success";

      var newNodes = [], newEdges = [], statusChanges = [];
      cell.nodes.forEach(function (nd) {
        var id = nd.data.id;
        if (!(id in ctrl.nodeIds)) { newNodes.push(nd); ctrl.nodeIds[id] = nd.data.status; }
        else if (ctrl.nodeIds[id] !== nd.data.status) { statusChanges.push(nd); ctrl.nodeIds[id] = nd.data.status; }
      });
      cell.edges.forEach(function (ed) {
        var id = ed.data.id;
        if (!(id in ctrl.edgeIds)) { newEdges.push(ed); ctrl.edgeIds[id] = true; }
      });

      // Keep the stored elements current so a not-yet-mounted panel builds
      // fresh with the latest data when it's first shown.
      ctrl.nodes = cell.nodes;
      ctrl.edges = cell.edges;

      var changed = newNodes.length || newEdges.length || statusChanges.length;
      if (!changed) return true;

      if (!ctrl.cy) { ctrl.dirty = true; return true; }  // hidden panel: defer to show

      // Re-class changed nodes in place (data() re-evaluates the style maps).
      statusChanges.forEach(function (nd) {
        var el = ctrl.cy.getElementById(nd.data.id);
        if (el && el.length) { el.data("status", nd.data.status); el.data("label", nd.data.label); }
      });
      if (newNodes.length || newEdges.length) {
        ctrl.cy.add({ nodes: newNodes, edges: newEdges });
        var visible = ctrl.el.classList.contains("active") && ctrl.el.offsetParent !== null;
        if (visible) {
          ctrl.cy.layout(Object.assign({}, LAYOUT, { animate: true, animationDuration: 300 })).run();
          if (!ctrl.userZoomed) ctrl.cy.animate({ fit: { padding: 28 } }, { duration: 300 });
          newNodes.forEach(function (nd) {
            var el = ctrl.cy.getElementById(nd.data.id);
            if (el && el.length) { el.style("opacity", 0); el.animate({ style: { opacity: 1 } }, { duration: 300 }); }
          });
        } else {
          ctrl.dirty = true;  // lay out when the panel is next shown
        }
      }
      return true;
    }

    function structuralRebuild() {
      if (structuralRebuildInFlight) return;
      structuralRebuildInFlight = true;
      fetch("/runs/" + encodeURIComponent(runId) + "/_tree", { headers: { "HX-Request": "true" } })
        .then(function (r) { return r.status === 200 ? r.text() : null; })
        .then(function (html) {
          if (!html) return;
          var tmp = document.createElement("div");
          tmp.innerHTML = html;
          var fresh = tmp.querySelector(".tree-explorer");
          if (fresh && root.parentNode) {
            stop();
            root.parentNode.replaceChild(fresh, root);
            boot();  // re-init the replaced explorer
          }
        })
        .catch(function () { /* transient; next poll retries */ })
        .finally(function () { structuralRebuildInFlight = false; });
    }

    function poll() {
      fetch("/runs/" + encodeURIComponent(runId) + "/_tree.json")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;
          var needsRebuild = false;
          (data.phases || []).forEach(function (ph) {
            (ph.cells || []).forEach(function (cell) {
              if (!applyCell(cell)) needsRebuild = true;
            });
          });
          if (needsRebuild) { structuralRebuild(); return; }
          if (data.finished) stop();
        })
        .catch(function () { /* transient network error; keep polling */ });
    }

    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    root.__attackTreeStop = stop;

    timer = setInterval(poll, 2000);
  }

  // Minimal CSS.escape shim for the attribute selectors we build.
  function cssEsc(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/["\\\]]/g, "\\$&");
  }

  function boot() {
    var roots = document.querySelectorAll(".tree-explorer");
    if (!roots.length) return;
    ensureLibs(function () {
      document.querySelectorAll(".tree-explorer").forEach(function (root) {
        if (root.__attackTreeReady) { if (root.__reassert) root.__reassert(); }
        else setup(root);
      });
    });
  }

  window.AttackTree = { boot: boot };

  if (document.readyState !== "loading") boot();
  else document.addEventListener("DOMContentLoaded", boot);
  document.addEventListener("htmx:afterSettle", boot);
})();
