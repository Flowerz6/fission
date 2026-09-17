/* Keep finding selection and full evidence inside the run being reviewed. */
(function () {
  'use strict';
  var pending = null;
  function section() { return document.getElementById('run-findings'); }
  function updateNavigation(root) {
    if (!root) return;
    var items = Array.from(root.querySelectorAll('[data-run-finding]'));
    var index = items.findIndex(function (item) { return item.dataset.runFinding === root.dataset.selectedFinding; });
    root.querySelectorAll('[data-finding-step]').forEach(function (button) {
      button.disabled = index < 0 || index + Number(button.dataset.findingStep) < 0 || index + Number(button.dataset.findingStep) >= items.length;
    });
    var position = root.querySelector('[data-finding-position]');
    if (position) position.textContent = (index + 1) + ' of ' + items.length;
  }
  async function selectFinding(link, recordHistory) {
    var root = section(), pane = root.querySelector('.run-evidence'), status = root.querySelector('[role="status"]');
    if (pending) pending.abort();
    var request = new AbortController(); pending = request;
    pane.setAttribute('aria-busy', 'true'); status.hidden = false; status.textContent = 'Loading evidence…';
    try {
      var response = await fetch(link.dataset.evidenceUrl, { signal: request.signal });
      if (!response.ok) throw new Error('Could not load this finding.');
      var html = await response.text();
      if (request.signal.aborted || !root.isConnected) return;
      pane.innerHTML = html;
      root.dataset.selectedFinding = link.dataset.runFinding;
      root.querySelectorAll('[data-run-finding]').forEach(function (item) {
        if (item === link) item.setAttribute('aria-current', 'true'); else item.removeAttribute('aria-current');
      });
      if (recordHistory) history.pushState(null, '', link.href);
      updateNavigation(root);
      status.hidden = true;
      pane.focus({ preventScroll: true });
      if (innerWidth <= 820) pane.scrollIntoView({ block: 'start' });
    } catch (failure) {
      if (failure.name === 'AbortError') return;
      status.textContent = 'Could not load this finding. ';
      var fallback = document.createElement('a'); fallback.href = link.href; fallback.textContent = 'Open it in the run page →'; status.appendChild(fallback);
    } finally {
      if (pending === request) { pending = null; pane.removeAttribute('aria-busy'); }
    }
  }
  document.addEventListener('click', function (event) {
    var jump = event.target.closest('[data-transcript-jump]');
    if (jump) {
      var transcript = jump.closest('.run-finding-evidence').querySelector('.run-transcript');
      var findingTurn = transcript.querySelector('[data-finding-turn]');
      if (findingTurn) transcript.scrollTop += findingTurn.getBoundingClientRect().top - transcript.getBoundingClientRect().top - 12;
      transcript.focus({ preventScroll: true });
      return;
    }
    var sectionLink = event.target.closest('.run-section-nav a');
    if (sectionLink && sectionLink.hash) {
      var targetSection = document.getElementById(sectionLink.hash.slice(1));
      var details = targetSection && (targetSection.matches('details') ? targetSection : targetSection.querySelector('details'));
      if (details) details.open = true;
    }
    var link = event.target.closest('[data-run-finding]');
    if (link) {
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); selectFinding(link, true); return;
    }
    var button = event.target.closest('[data-finding-step]');
    if (!button || button.disabled) return;
    var root = section(), links = Array.from(root.querySelectorAll('[data-run-finding]'));
    var index = links.findIndex(function (item) { return item.dataset.runFinding === root.dataset.selectedFinding; });
    var next = links[index + Number(button.dataset.findingStep)];
    if (next) selectFinding(next, true);
  });
  window.addEventListener('popstate', function () {
    var root = section(); if (!root) return;
    var id = new URL(location.href).searchParams.get('finding');
    var links = Array.from(root.querySelectorAll('[data-run-finding]'));
    var link = links.find(function (item) { return item.dataset.runFinding === id; }) || links[0];
    if (link) selectFinding(link, false);
  });
  document.addEventListener('htmx:configRequest', function (event) {
    if (event.detail.elt.id === 'run-findings') event.detail.parameters.finding = section().dataset.selectedFinding;
  });
  document.addEventListener('htmx:beforeSwap', function (event) {
    if (event.detail.target.id !== 'run-findings') return;
    var current = section().dataset.selectedFinding;
    var incoming = new DOMParser().parseFromString(event.detail.xhr.responseText, 'text/html').getElementById('run-findings');
    if (pending || (current && incoming && incoming.dataset.selectedFinding !== current)) event.detail.shouldSwap = false;
  });
  document.addEventListener('htmx:afterSwap', function () { updateNavigation(section()); });
  updateNavigation(section());
})();
