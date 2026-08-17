document.addEventListener("DOMContentLoaded", () => {
  const dialog = document.querySelector("[data-store-dialog]");
  const opener = document.querySelector("[data-store-dialog-open]");
  const closers = dialog?.querySelectorAll("[data-store-dialog-close]") || [];
  let submitting = false;
  const close = () => {
    if (!dialog?.open || submitting) return;
    dialog.close();
    opener?.focus();
  };
  opener?.addEventListener("click", () => {
    if (!dialog) return;
    dialog.showModal();
    dialog.querySelector("input, select, textarea, button")?.focus();
  });
  closers.forEach((button) => button.addEventListener("click", close));
  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  dialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  dialog?.querySelector("form")?.addEventListener("submit", () => {
    submitting = true;
  });
});
