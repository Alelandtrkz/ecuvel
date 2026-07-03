(() => {
  const openButton = document.querySelector("[data-products-guide-open]");
  const dialog = document.querySelector("[data-products-guide-dialog]");

  if (!openButton || !dialog || typeof dialog.showModal !== "function") {
    return;
  }

  let lastFocusedElement = null;

  function refreshIcons() {
    window.lucide?.createIcons?.();
  }

  function closeGuide() {
    if (dialog.open) {
      dialog.close();
    }
  }

  function openGuide() {
    lastFocusedElement = document.activeElement;
    dialog.showModal();
    document.body.classList.add("partner-products-guide-open");
    refreshIcons();
    const closeButton = dialog.querySelector("[data-products-guide-close]");
    closeButton?.focus({ preventScroll: true });
  }

  openButton.addEventListener("click", openGuide);

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      closeGuide();
    }
  });

  dialog.addEventListener("cancel", () => {
    document.body.classList.remove("partner-products-guide-open");
  });

  dialog.addEventListener("close", () => {
    document.body.classList.remove("partner-products-guide-open");
    if (lastFocusedElement && typeof lastFocusedElement.focus === "function") {
      lastFocusedElement.focus({ preventScroll: true });
    }
  });

  dialog.querySelectorAll("[data-products-guide-close]").forEach((button) => {
    button.addEventListener("click", closeGuide);
  });
})();
