/* Report-builder modal on the Reports page. The form is a plain GET to
   /reports/view (opens the generated report in a new tab), so this only
   manages the dialog open/close. */
(function () {
  "use strict";
  var dialog = document.getElementById("report-builder-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return;

  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-open-report-builder]")) {
      e.preventDefault();
      dialog.showModal();
    } else if (e.target.closest && e.target.closest("[data-rb-cancel]")) {
      e.preventDefault();
      dialog.close();
    }
  });
  // Backdrop click closes.
  dialog.addEventListener("click", function (e) {
    if (e.target === dialog) dialog.close();
  });
  // Close the dialog once the report opens in its new tab, so returning to
  // this tab shows the reports list, not a stuck-open modal.
  var form = document.getElementById("report-builder-form");
  if (form) form.addEventListener("submit", function () { setTimeout(function () { dialog.close(); }, 100); });

  // "All" severities toggle: checking it clears the specific severities (an
  // empty severity selection means every severity); checking any specific one
  // clears "All"; clearing them all re-checks "All".
  var group = document.getElementById("rb-severity");
  var allBox = document.getElementById("rb-sev-all");
  if (group && allBox) {
    var specific = function () {
      return Array.prototype.slice.call(group.querySelectorAll('input[name="severity"]'));
    };
    allBox.addEventListener("change", function () {
      if (allBox.checked) specific().forEach(function (b) { b.checked = false; });
      else if (!specific().some(function (b) { return b.checked; })) allBox.checked = true; // can't have none
    });
    group.addEventListener("change", function (e) {
      if (e.target === allBox) return;
      var any = specific().some(function (b) { return b.checked; });
      allBox.checked = !any;
    });
  }
})();
