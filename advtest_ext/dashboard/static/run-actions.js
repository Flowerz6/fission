/* Run actions use the server's persisted records and text-only DOM updates. */
(function () {
  'use strict';
  var menu = document.getElementById('run-actions-menu');
  if (!menu) return;
  var dialog = document.getElementById('run-action-dialog');
  var form = document.getElementById('run-action-form');
  var input = document.getElementById('run-new-name');
  var error = document.getElementById('run-action-error');
  var submit = document.getElementById('run-action-submit');
  var cancel = dialog.querySelector('[data-run-cancel]');
  var trigger = null, selected = null, action = '', busy = false;
  var view = menu.querySelector('[data-run-view]');
  var exportLink = menu.querySelector('[data-run-export]');
  var deleteButton = menu.querySelector('[data-run-delete]');

  function closeMenu() {
    if (menu.matches(':popover-open')) menu.hidePopover();
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
  }
  function positionMenu() {
    if (!trigger || !menu.matches(':popover-open')) return;
    var rect = trigger.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(rect.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8)) + 'px';
    menu.style.top = Math.max(8, rect.bottom + menu.offsetHeight + 8 > innerHeight ? rect.top - menu.offsetHeight - 4 : rect.bottom + 4) + 'px';
  }
  document.querySelectorAll('.run-actions-trigger').forEach(function (button) {
    button.addEventListener('click', function () {
      var wasOpen = menu.matches(':popover-open') && trigger === button;
      closeMenu();
      if (wasOpen) return;
      trigger = button;
      selected = { id: button.dataset.runId, name: button.dataset.runName };
      var base = '/runs/' + encodeURIComponent(selected.id);
      view.href = base;
      exportLink.href = base + '/report?export=1';
      deleteButton.disabled = button.dataset.runFinished !== 'true';
      deleteButton.title = deleteButton.disabled ? 'Active or unfinished runs cannot be deleted' : '';
      menu.showPopover();
      positionMenu();
      button.setAttribute('aria-expanded', 'true');
      view.focus({ preventScroll: true });
    });
  });
  menu.addEventListener('toggle', function (event) {
    if (event.newState === 'closed' && trigger) trigger.setAttribute('aria-expanded', 'false');
  });
  menu.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { closeMenu(); trigger.focus(); return; }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    var items = Array.from(menu.querySelectorAll('[role="menuitem"]:not(:disabled)'));
    var index = items.indexOf(document.activeElement);
    index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[index].focus();
  });
  menu.addEventListener('click', function (event) {
    if (event.target.closest('a')) closeMenu();
  });
  window.addEventListener('resize', positionMenu);
  window.addEventListener('scroll', positionMenu, true);

  function edit(kind) {
    action = kind;
    closeMenu();
    var deleting = kind === 'delete';
    document.getElementById('run-action-title').textContent = deleting ? 'Delete run?' : 'Rename run';
    document.getElementById('run-action-description').textContent = deleting
      ? 'This permanently removes the run, its attempts, findings, evaluation results and review queue entries. Saved human review labels and files on disk are kept. This cannot be undone.'
      : 'Choose a name to use throughout the dashboard.';
    document.getElementById('run-action-name').textContent = selected.name;
    document.getElementById('run-action-id').textContent = selected.id;
    document.getElementById('run-rename-field').hidden = deleting;
    input.disabled = deleting;
    input.value = selected.name;
    submit.textContent = deleting ? 'Delete run' : 'Save name';
    submit.classList.toggle('btn-danger', deleting);
    error.hidden = true;
    dialog.showModal();
    if (deleting) cancel.focus(); else { input.focus(); input.select(); }
  }
  menu.querySelector('[data-run-rename]').addEventListener('click', function () { edit('rename'); });
  deleteButton.addEventListener('click', function () { edit('delete'); });
  cancel.addEventListener('click', function () { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', function (event) { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', function () { if (trigger) trigger.focus(); });
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (busy) return;
    var name = input.value.trim();
    if (action === 'rename' && !name) {
      error.textContent = 'Enter a run name.'; error.hidden = false; input.focus(); return;
    }
    busy = true; submit.disabled = true; cancel.disabled = true; error.hidden = true;
    var label = submit.textContent;
    submit.textContent = action === 'delete' ? 'Deleting…' : 'Saving…';
    try {
      var response = await fetch('/runs/' + encodeURIComponent(selected.id), {
        method: action === 'delete' ? 'DELETE' : 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(action === 'delete' ? { confirmation: selected.id } : { name: name })
      });
      if (!response.ok) {
        var result = await response.json().catch(function () { return {}; });
        throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not update this run. Please try again.');
      }
      // The service cookie survives. Resolve it again if deletion removed its last run.
      if (action === 'delete') window.location.assign('/runs');
      else window.location.reload();
    } catch (failure) {
      error.textContent = failure.message || 'Could not reach the dashboard. Please try again.';
      error.hidden = false;
      busy = false; submit.disabled = false; cancel.disabled = false; submit.textContent = label;
    }
  });
})();
