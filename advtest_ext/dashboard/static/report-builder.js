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
})();
