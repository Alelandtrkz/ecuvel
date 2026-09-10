(() => {
  const drawer = document.querySelector("[data-docs-menu]");
  const openButton = document.querySelector("[data-docs-menu-open]");
  const closeButton = document.querySelector("[data-docs-menu-close]");
  const overlay = document.querySelector("[data-docs-menu-overlay]");
  if (!drawer || !openButton || !closeButton || !overlay) return;

  const focusableSelector = [
    "a[href]",
    "button:not([disabled])",
    "summary",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  let returnFocus = null;

  const focusableItems = () => Array.from(
    drawer.querySelectorAll(focusableSelector),
  ).filter(
    (element) => (
      !element.hasAttribute("hidden") && element.getClientRects().length > 0
    ),
  );

  const closeDrawer = () => {
    if (!drawer.classList.contains("is-open")) return;
    drawer.classList.remove("is-open");
    drawer.setAttribute("aria-hidden", "true");
    drawer.inert = true;
    openButton.setAttribute("aria-expanded", "false");
    overlay.hidden = true;
    document.body.classList.remove("docs-nav-open");
    if (returnFocus instanceof HTMLElement) returnFocus.focus();
  };

  const openDrawer = () => {
    returnFocus = document.activeElement;
    drawer.classList.add("is-open");
    drawer.setAttribute("aria-hidden", "false");
    drawer.inert = false;
    openButton.setAttribute("aria-expanded", "true");
    overlay.hidden = false;
    document.body.classList.add("docs-nav-open");
    closeButton.focus();
  };

  openButton.addEventListener("click", openDrawer);
  closeButton.addEventListener("click", closeDrawer);
  overlay.addEventListener("click", closeDrawer);
  drawer.addEventListener("click", (event) => {
    if (event.target.closest("a[href]")) closeDrawer();
  });

  document.addEventListener("keydown", (event) => {
    if (!drawer.classList.contains("is-open")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeDrawer();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusableItems();
    if (!items.length) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth >= 1024) closeDrawer();
  });
})();
