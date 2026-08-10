(() => {
  "use strict";

  const clean = (value) => String(value || "").trim().replace(/\s+/g, " ").slice(0, 120);
  const comparable = (value) => clean(value).toUpperCase();

  document.querySelectorAll("[data-scanner-primary]").forEach((input) => {
    if (document.visibilityState === "visible" && !input.value) {
      window.requestAnimationFrame(() => input.focus({ preventScroll: true }));
    }
  });

  document.querySelectorAll("[data-scan-collector]").forEach((form) => {
    const input = form.querySelector("[data-collector-input]");
    const feedback = form.querySelector("[data-scan-feedback]");
    const progressText = form.querySelector("[data-scan-progress]");
    const progressBar = form.querySelector("[data-scan-progress-bar]");
    const hiddenFields = form.querySelector("[data-collected-fields]");
    const confirmButton = form.querySelector("[data-confirm]");
    const identity = form.querySelector("[data-identity-confirm]");
    const fieldName = form.dataset.fieldName || "scanned_codes";
    const serverAllowed = form.dataset.serverAllowed === "1";
    const rows = Array.from(form.querySelectorAll("[data-expected-code]")).map((element) => ({
      element,
      code: clean(element.dataset.expectedCode),
      barcode: clean(element.dataset.expectedBarcode),
      required: Number.parseInt(element.dataset.expectedQuantity || "1", 10),
      count: 0,
      countElement: element.querySelector("[data-scan-count]"),
    }));
    const requiredTotal = rows.reduce((total, row) => total + row.required, 0);
    let scannedTotal = 0;

    const announce = (message, tone = "") => {
      if (!feedback) return;
      feedback.textContent = message;
      feedback.dataset.tone = tone;
    };

    const refresh = () => {
      rows.forEach((row) => {
        if (row.countElement) row.countElement.textContent = String(row.count);
        row.element.classList.toggle("is-complete", row.count === row.required);
      });
      if (progressText) progressText.textContent = `${scannedTotal}/${requiredTotal}`;
      if (progressBar) progressBar.style.width = `${requiredTotal ? Math.min(100, (scannedTotal / requiredTotal) * 100) : 0}%`;
      const identityReady = !form.hasAttribute("data-identity-required") || Boolean(identity?.checked);
      if (confirmButton) confirmButton.disabled = !(serverAllowed && requiredTotal > 0 && scannedTotal === requiredTotal && identityReady);
    };

    const collect = (rawCode) => {
      const code = clean(rawCode);
      const comparableCode = comparable(code);
      if (!code) return;
      const row = rows.find((candidate) => comparable(candidate.code) === comparableCode || (candidate.barcode && comparable(candidate.barcode) === comparableCode));
      if (!row) {
        announce(`El código ${code} no pertenece a esta operación.`, "error");
        return;
      }
      if (row.count >= row.required) {
        announce(`${row.code} ya fue verificado en la cantidad requerida.`, "warning");
        return;
      }
      row.count += 1;
      scannedTotal += 1;
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = fieldName;
      hidden.value = row.barcode && comparable(row.barcode) === comparableCode ? row.barcode : row.code;
      hiddenFields?.appendChild(hidden);
      announce(`${row.code} verificado.`, "success");
      refresh();
    };

    if (input) {
      input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        collect(input.value);
        input.value = "";
        input.focus();
      });
    }
    identity?.addEventListener("change", refresh);
    form.addEventListener("submit", (event) => {
      if (confirmButton?.disabled) {
        event.preventDefault();
        announce("Completa todas las verificaciones antes de confirmar.", "error");
        input?.focus();
      }
    });
    refresh();
  });
})();
