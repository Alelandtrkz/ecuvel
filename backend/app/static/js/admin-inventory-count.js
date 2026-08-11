(() => {
  "use strict";

  const root = document.querySelector("[data-inventory-count]");
  if (!root) return;

  const input = root.querySelector("[data-count-code]");
  if (!input) return;

  input.focus({ preventScroll: true });
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
    if (event.key.length === 1) {
      input.focus();
      input.value += event.key;
      event.preventDefault();
    }
  });
})();
