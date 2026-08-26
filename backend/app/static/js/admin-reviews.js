(() => {
  const drawer = document.querySelector("[data-review-drawer]");
  if (!drawer) return;

  const layer = document.querySelector("[data-review-drawer-layer]");
  const closeLinks = [...layer.querySelectorAll("[data-review-drawer-close]")];
  const focusableSelector = [
    "a[href]", "button:not([disabled])", "select:not([disabled])",
    "textarea:not([disabled])", "input:not([disabled])", "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  const previousFocus = document.activeElement;

  document.body.classList.add("has-review-drawer");
  requestAnimationFrame(() => drawer.focus());

  const close = (event) => {
    if (event) event.preventDefault();
    const href = closeLinks[0]?.href;
    document.body.classList.remove("has-review-drawer");
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
    if (href) window.location.assign(href);
  };

  closeLinks.forEach((link) => link.addEventListener("click", close));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      close(event);
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
