(function () {
  "use strict";

  document.querySelectorAll("[data-product-moderation-form]").forEach(function (form) {
    var checks = Array.from(form.querySelectorAll("[data-moderation-check]"));
    var approve = form.querySelector("[data-approve-product]");
    var dialog = document.querySelector("[data-moderation-dialog]");
    var lastTrigger = null;

    function updateApproval() {
      if (approve) approve.disabled = !checks.length || !checks.every(function (check) { return check.checked; });
    }
    checks.forEach(function (check) { check.addEventListener("change", updateApproval); });
    updateApproval();

    form.querySelectorAll("[data-moderation-open]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (!dialog) return;
        lastTrigger = button;
        dialog._moderationReturnFocus = button;
        dialog.showModal();
        var first = dialog.querySelector("input[name=reason_code]");
        if (first) first.focus();
      });
    });

    if (!dialog || dialog.dataset.bound === "true") return;
    dialog.dataset.bound = "true";
    dialog.querySelectorAll("[data-moderation-close]").forEach(function (button) {
      button.addEventListener("click", function () { dialog.close(); });
    });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", function () {
      var returnFocus = dialog._moderationReturnFocus || lastTrigger;
      if (returnFocus) returnFocus.focus();
    });
    var decisionForm = dialog.querySelector("[data-moderation-decision-form]");
    if (decisionForm) {
      decisionForm.addEventListener("submit", function () {
        decisionForm.querySelectorAll("[data-copied-check]").forEach(function (node) { node.remove(); });
        checks.forEach(function (check) {
          if (!check.checked) return;
          var hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = "checklist";
          hidden.value = check.value;
          hidden.dataset.copiedCheck = "true";
          decisionForm.appendChild(hidden);
        });
      });
    }
    form.addEventListener("submit", function (event) {
      if (!window.confirm("Aprobar y publicar producto\n\nConfirmo que revisé imágenes, información, especificaciones y variantes.")) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll("[data-preview-moderation-open]").forEach(function (button) {
    button.addEventListener("click", function () {
      var dialog = document.querySelector("[data-moderation-dialog]");
      if (!dialog) return;
      dialog._moderationReturnFocus = button;
      dialog.showModal();
      var first = dialog.querySelector("input[name=reason_code]");
      if (first) first.focus();
    });
  });
})();
