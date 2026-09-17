/* ============================================================================
   Attack-tree explorer (Cytoscape.js) — live client.

   Replaces the old Mermaid `graph TD` live tree. A cell navigator (left)
   drives one Cytoscape canvas per (behaviour, technique) cell (right).

   Design goals:
     * nodes are real HTML cards (DM Sans, status dot, score, meta line) laid
       over the canvas and kept in sync with Cytoscape's pan/zoom/positions, so
       they share the dashboard's type and tokens. Cytoscape still owns layout,
       hit-testing and the edges; its own node bodies are transparent;
     * lazy mount — a cell's graph is only built the first time its panel is
       shown (Cytoscape renders blank in a hidden / zero-size container);
     * NO full repaint on live runs. While the run is in flight the client
       polls `/runs/{id}/_tree.json` and INCREMENTALLY `cy.add()`s only new
       nodes/edges (and re-renders nodes whose verdict changed), re-running
       dagre with animation and re-fitting — so branches settle smoothly.

   Fully self-contained: the vendored libraries live under /static/vendor and
   are injected once (guarded on window.cytoscape); init is idempotent so a
   re-inserted partial never stacks Cytoscape instances.
   ============================================================================ */
(function () {
  "use strict";

  var VENDOR = "/static/vendor/";

  // Card geometry. Must match .tn / .tn-root in tree.css: the (invisible)
  // Cytoscape node is the hit target and the box dagre lays out.
  var NODE_W = 232, NODE_H = 72, ROOT_W = 320, ROOT_H = 58;

  var STATUS_LABEL = {
    success: "Success", partial: "Partial", refused: "Refused", pruned: "Pruned",
  };
  function statusOf(d) { return STATUS_LABEL[d.status] ? d.status : (d.status === "root" ? "root" : "refused"); }

  // Edge inks (paper palette from base.html; canvas can't read CSS variables).
  var EDGE = "#d3cfc2", EDGE_TRAIL = "#1a1a18", EDGE_WIN = "#3e8e5b";

  var STY = [
    { selector: "node", style: {
        "shape": "round-rectangle", "width": NODE_W, "height": NODE_H,
        "background-opacity": 0, "border-width": 0, "label": "",
    } },
    { selector: 'node[status = "root"]', style: { "width": ROOT_W, "height": ROOT_H } },
    { selector: "edge", style: {
        "width": 1, "line-color": EDGE,
        "curve-style": "round-taxi", "taxi-direction": "downward",
        "taxi-turn": "50%", "taxi-radius": 8,
        "target-arrow-shape": "none",
    } },
    // Ancestry of every successful node: the path that actually worked.
    { selector: "edge.win", style: { "width": 1.5, "line-color": EDGE_WIN, "z-index": 2 } },
    // Branches the search gave up on: nothing below them succeeded. Only
    // marked once the run is over; a live branch may still be deepened.
    { selector: "edge.dead", style: { "line-style": "dashed", "line-dash-pattern": [4, 4] } },
    // Ancestry of the selected node (click highlight; not in the legend).
    { selector: "edge.trail", style: { "width": 1.5, "line-color": EDGE_TRAIL, "line-style": "solid", "z-index": 3 } },
  ];

  var LAYOUT = { name: "dagre", rankDir: "TB", nodeSep: 18, rankSep: 46, fit: false };  // fitView() owns the viewport

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

  function nodeTitle(d) {
    var t = d.name || d.attempted || String(d.label || "").split("\n")[0];
    return t.replace(/ · d\d+\/b\d+$/, "");  // position already shows depth/branch
  }

  function scoreText(d) {
    return d.score == null ? "" : Number(d.score).toFixed(2);
  }

  // Meta line under a card name. Kept short so it never truncates: start
  // time, turns, score. Method and the full time range live in the detail.
  function metaParts(d) {
    var parts = [];
    if (d.time) parts.push(String(d.time).split("–")[0]);
    if (d.turn_count > 1) parts.push(d.turn_count + " turns");
    if (d.score != null) parts.push("score " + scoreText(d));
    return parts;
  }

  // ---- HTML node cards ----------------------------------------------------
  function renderCard(el, d) {
    var st = statusOf(d);
    var keep = (el.classList.contains("sel") ? " sel" : "") + (el.classList.contains("hover") ? " hover" : "");
    if (st === "root") {
      el.className = "tn tn-root" + keep;
      el.innerHTML = '<span class="tn-k">Objective</span><span class="tn-title">' + esc(nodeTitle(d)) + "</span>";
      return;
    }
    // Status is the card's colour alone (green success, amber partial, plain
    // refused, dashed pruned): no dot, no label.
    el.className = "tn tn-" + st + keep;
    el.innerHTML =
      '<span class="tn-title">' + esc(nodeTitle(d)) + "</span>" +
      '<span class="tn-meta">' + esc(metaParts(d).join(" · ")) + "</span>";
  }

  function kv(k, v) {
    return "<div><dt>" + k + "</dt><dd>" + esc(v) + "</dd></div>";
  }

  function turn(k, v) {
    return v ? '<div class="td-turn"><div class="td-k">' + k + '</div><div class="td-v">' + esc(v) + "</div></div>" : "";
  }

  function nodeDetail(panel, d) {
    var el = panel.querySelector(".tree-detail");
    if (!el) return;
    var st = statusOf(d);
    if (st === "root") {
      el.innerHTML = '<div class="td-h"><span class="td-t">' + esc(nodeTitle(d)) + '</span><span class="pill grey">Objective</span></div>' +
        '<p class="td-hint">' + esc(d.a || "The opening moves branch from here.") + "</p>";
      return;
    }
    var score = scoreText(d);
    var pillCls = st === "pruned" ? "grey" : st;
    var link = d.trace_id
      ? '<a class="td-link" href="/traces/' + esc(d.trace_id) + '">Open full trace &rarr;</a>'
      : "";
    var meta = "";
    if (d.time) meta += kv("Time (UTC)", d.time);
    if (d.method) meta += kv("Method", d.method);
    if (d.turn_count > 1) meta += kv("Turns", d.turn_count);
    var body;
    if (d.email || d.injection) {
      body = turn("Delivered email", d.email) + turn("Hidden white-text injection", d.injection);
    } else {
      body = turn("Prompt", d.u) + turn("Response", d.a);
    }
    el.innerHTML =
      '<div class="td-h"><span class="td-t">' + esc(nodeTitle(d)) + "</span>" +
      '<span class="pill ' + pillCls + '">' + STATUS_LABEL[st] + (score ? " · " + score : "") + "</span>" +
      link + "</div>" +
      (d.attempted && d.attempted !== nodeTitle(d) ? '<p class="td-approach">' + esc(d.attempted) + "</p>" : "") +
      (meta ? '<dl class="td-meta">' + meta + "</dl>" : "") +
      (body ? '<div class="td-transcript">' + body + "</div>"
            : '<p class="td-hint">No inline transcript captured for this node. Open the full trace for detail.</p>');
  }

  function resetDetail(panel) {
    var el = panel.querySelector(".tree-detail");
    if (el) {
      el.innerHTML = '<div class="td-h"><span class="td-t">Select a node</span></div>' +
        '<p class="td-hint">Each node is an attempt or a grouped conversation. Select a node to inspect what was attempted, its time, method, and evidence.</p>';
    }
  }

  // Fit the whole tree, but never magnify past 1:1 — a three-node tree should
  // show normal-size cards, not poster-size ones.
  // Extra headroom at the top keeps the root clear of the zoom controls.
  var FIT_PAD = 32, FIT_PAD_TOP = 64;
  function fitView(cy, duration) {
    var bb = cy.elements().boundingBox();
    if (!bb.w || !bb.h) return;
    var w = cy.width(), h = cy.height();
    var availH = h - FIT_PAD_TOP - FIT_PAD;
    var z = Math.min((w - 2 * FIT_PAD) / bb.w, availH / bb.h, 1);
    z = Math.max(z, cy.minZoom());
    var view = { zoom: z, pan: {
      x: (w - z * (bb.x1 + bb.x2)) / 2,
      y: FIT_PAD_TOP + (availH - z * bb.h) / 2 - z * bb.y1,
    } };
    if (duration) cy.animate(view, { duration: duration });
    else cy.viewport(view);
  }

  // ---- per-explorer controller -------------------------------------------
  function setup(root) {
    if (root.__attackTreeReady) return;   // idempotent
    root.__attackTreeReady = true;

    var runId = root.getAttribute("data-run-id");
    var live = root.getAttribute("data-live") === "1";
    function isLive() { return live; }
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
          if (!ctrl.cy) return;
          ctrl.cy.resize();
          // Late reflows (fonts, scrollbars) would leave the first fit
          // off-centre; re-fit unless the user has taken over the viewport.
          if (ctrl.shown && !ctrl.userZoomed) fitView(ctrl.cy);
        });
        ro.observe(mountEl);
        ctrl._ro = ro;
      }
      // Once the user zooms/pans, remember it so htmx re-asserts and live
      // updates don't yank the viewport back to fit. The Fit button clears it.
      mountEl.addEventListener("wheel", function () { ctrl.userZoomed = true; }, { passive: true });
      ctrl.cy.on("dragpan", function () { ctrl.userZoomed = true; });
      // HTML card layer. One transform follows the viewport; each card sits
      // at its node's model position, so pan/zoom never touches the cards.
      var layer = document.createElement("div");
      layer.className = "cy-cards";
      mountEl.appendChild(layer);
      ctrl.cards = {};
      function syncViewport() {
        var p = ctrl.cy.pan();
        layer.style.transform = "translate(" + p.x + "px," + p.y + "px) scale(" + ctrl.cy.zoom() + ")";
      }
      function place(n) {
        var card = ctrl.cards[n.id()];
        if (!card) return;
        var pos = n.position();
        card.style.transform = "translate(" + (pos.x - n.width() / 2) + "px," + (pos.y - n.height() / 2) + "px)";
      }
      function addCard(n, entering) {
        var card = document.createElement("div");
        renderCard(card, n.data());
        if (entering) card.classList.add("enter");
        ctrl.cards[n.id()] = card;
        layer.appendChild(card);
        place(n);
      }
      // Mark the ancestry of every successful node (the path that worked).
      ctrl.markWins = function () {
        var edges = ctrl.cy.edges().removeClass("win dead");
        var wins = ctrl.cy.nodes('[status = "success"]').predecessors("edge").addClass("win");
        if (!isLive()) edges.not(wins).addClass("dead");
      };
      ctrl.cy.nodes().forEach(function (n) { addCard(n, false); });
      ctrl.markWins();
      syncViewport();
      ctrl.cy.on("viewport", syncViewport);
      ctrl.cy.on("position", "node", function (e) { place(e.target); });
      ctrl.cy.on("add", "node", function (e) { addCard(e.target, true); });
      ctrl.cy.on("data", "node", function (e) {
        var card = ctrl.cards[e.target.id()];
        if (card) renderCard(card, e.target.data());
      });
      ctrl.cy.on("mouseover", "node", function (e) {
        var card = ctrl.cards[e.target.id()];
        if (card) card.classList.add("hover");
        mountEl.style.cursor = "pointer";
      });
      ctrl.cy.on("mouseout", "node", function (e) {
        var card = ctrl.cards[e.target.id()];
        if (card) card.classList.remove("hover");
        mountEl.style.cursor = "";
      });
      function select(n) {
        Object.keys(ctrl.cards).forEach(function (id) { ctrl.cards[id].classList.remove("sel"); });
        ctrl.cy.edges().removeClass("trail");
        if (!n) { resetDetail(ctrl.el); return; }
        ctrl.cards[n.id()].classList.add("sel");
        n.predecessors("edge").addClass("trail");
        nodeDetail(ctrl.el, n.data());
      }
      ctrl.cy.on("tap", function (e) {
        if (e.target === ctrl.cy) select(null);           // background tap clears
        else if (e.target.isNode && e.target.isNode()) select(e.target);
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
          fitView(ctrl.cy);
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
          fitView(ctrl.cy, 260);
        }
        return;
      }
      var zoomBtn = e.target.closest && e.target.closest("[data-tree-zoom]");
      if (zoomBtn && root.contains(zoomBtn)) {
        var zPanel = zoomBtn.closest(".tree-panel");
        var zCtrl = zPanel && panelsById[zPanel.getAttribute("data-cell-key")];
        if (zCtrl && zCtrl.cy) {
          var cy = zCtrl.cy;
          var factor = zoomBtn.getAttribute("data-tree-zoom") === "in" ? 1.25 : 0.8;
          zCtrl.userZoomed = true;
          cy.animate({
            zoom: { level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } },
          }, { duration: 160 });
        }
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
          bar.style.background = pct >= 66 ? "var(--crit)" : (pct >= 33 ? "var(--high)" : "var(--muted)");
        }
      }
      var head = ctrl.el.querySelector(".tree-panel-head .th-count");
      if (head) head.textContent = cell.n_success + "/" + cell.n_traces + " success";

      var newNodes = [], newEdges = [], statusChanges = [];
      var previous = {};
      ctrl.nodes.forEach(function (node) { previous[node.data.id] = JSON.stringify(node.data); });
      cell.nodes.forEach(function (nd) {
        var id = nd.data.id;
        if (!(id in ctrl.nodeIds)) { newNodes.push(nd); ctrl.nodeIds[id] = nd.data.status; }
        else if (previous[id] !== JSON.stringify(nd.data)) { statusChanges.push(nd); ctrl.nodeIds[id] = nd.data.status; }
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
        if (el && el.length) {
          el.data(nd.data);
          var card = ctrl.cards[nd.data.id];
          if (card && card.classList.contains("sel")) nodeDetail(ctrl.el, nd.data);
        }
      });
      if (statusChanges.length && !(newNodes.length || newEdges.length)) ctrl.markWins();
      if (newNodes.length || newEdges.length) {
        ctrl.cy.add({ nodes: newNodes, edges: newEdges });
        ctrl.markWins();
        var visible = ctrl.el.classList.contains("active") && ctrl.el.offsetParent !== null;
        if (visible) {
          if (!ctrl.userZoomed) ctrl.cy.one("layoutstop", function () { fitView(ctrl.cy, 200); });
          ctrl.cy.layout(Object.assign({}, LAYOUT, { animate: true, animationDuration: 300 })).run();
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
          if (data.finished) {
            stop();
            live = false;  // search is over: unsuccessful branches are now pruned
            order.forEach(function (k) { if (panelsById[k].cy) panelsById[k].markWins(); });
          }
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
