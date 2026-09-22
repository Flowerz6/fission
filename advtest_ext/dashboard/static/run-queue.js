/* ============================================================================
   Queued-runs indicator (top-right).

   Polls /live-tests/status and surfaces any run that is in flight or queued
   back-to-back (the batch queue), each linking to its live console at
   /runs/{run_id}. The standalone launcher page is gone — a run's progress lives
   on its own /runs/{id} page, and this indicator is how you find in-flight and
   queued runs from anywhere. Hidden when nothing is running or queued.
   ============================================================================ */
(function () {
  "use strict";

  var wrap = document.getElementById("run-queue");
  var btn = document.getElementById("run-queue-btn");
  var countEl = document.getElementById("run-queue-count");
  var menu = document.getElementById("run-queue-menu");
  if (!wrap || !btn || !countEl || !menu) return;

  var ACTIVE = { running: 1, starting: 1, stopping: 1 };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function itemsFrom(s) {
    var items = [];
    var seen = {};
    (s.batch || []).forEach(function (b) {
      if (b.status === "queued" || b.status === "running") {
        items.push({ label: b.label || b.playbook || "Run", run_id: b.run_id, status: b.status });
        if (b.run_id) seen[b.run_id] = 1;
      }
    });
    if (ACTIVE[s.status] && s.run_id && !seen[s.run_id]) {
      items.unshift({ label: "Current run", run_id: s.run_id, status: "running" });
    }
    return items;
  }

  function render(items) {
    if (!items.length) {
      wrap.hidden = true;
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      return;
    }
    wrap.hidden = false;
    countEl.textContent = String(items.length);
    menu.innerHTML = items
      .map(function (it) {
        var inner =
          '<span class="rq-label">' + esc(it.label) + "</span>" +
          '<span class="rq-status ' + (it.status === "running" ? "running" : "") + '">' + esc(it.status) + "</span>";
        return it.run_id
          ? '<a class="run-queue-item" role="menuitem" href="/runs/' + encodeURIComponent(it.run_id) + '">' + inner + "</a>"
          : '<div class="run-queue-item">' + inner + "</div>";
      })
      .join("");
  }

  function poll() {
    fetch("/live-tests/status", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (s) { render(itemsFrom(s)); })
      .catch(function () { wrap.hidden = true; });
  }

  btn.addEventListener("click", function () {
    var open = menu.hidden;
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.addEventListener("click", function (e) {
    if (!wrap.contains(e.target)) {
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    }
  });

  poll();
  setInterval(poll, 4000);
})();
