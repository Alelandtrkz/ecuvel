(() => {
  const advancedTrigger = document.querySelector("[data-payment-advanced-trigger]");
  const advancedPanel = document.querySelector("[data-payment-advanced-panel]");
  if (advancedTrigger && advancedPanel) {
    advancedTrigger.addEventListener("click", () => {
      const expanded = advancedTrigger.getAttribute("aria-expanded") === "true";
      advancedTrigger.setAttribute("aria-expanded", String(!expanded));
      advancedPanel.hidden = expanded;
    });
  }

  document.querySelectorAll("[data-copy-payment-reference]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.dataset.copyPaymentReference || "";
      if (!value || !navigator.clipboard) return;
      try {
        await navigator.clipboard.writeText(value);
        button.classList.add("is-copied");
        window.setTimeout(() => button.classList.remove("is-copied"), 1200);
      } catch (_error) {
        button.classList.remove("is-copied");
      }
    });
  });

  const drawer = document.querySelector("[data-payment-drawer]");
  const layer = document.querySelector("[data-payment-drawer-layer]");
  if (!drawer || !layer) return;

  const closeLinks = [...layer.querySelectorAll("[data-payment-drawer-close]")];
  const focusableSelector = [
    "a[href]", "button:not([disabled])", "select:not([disabled])",
    "textarea:not([disabled])", "input:not([disabled])", "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  const previousFocus = document.activeElement;
  document.body.classList.add("has-payment-drawer");
  requestAnimationFrame(() => drawer.focus());

  const closeDrawer = (event) => {
    if (event) event.preventDefault();
    const href = closeLinks[0]?.href;
    document.body.classList.remove("has-payment-drawer");
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
    if (href) window.location.assign(href);
  };

  closeLinks.forEach((link) => link.addEventListener("click", closeDrawer));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDrawer(event);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...drawer.querySelectorAll(focusableSelector)]
      .filter((node) => node.getClientRects().length > 0);
    if (!focusable.length) {
      event.preventDefault();
      drawer.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
})();
