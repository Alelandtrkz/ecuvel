(() => {
  const focusableSelector = [
    "a[href]", "button:not([disabled])", "input:not([disabled])",
    "select:not([disabled])", "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  const visibleFocusable = (container) => [...container.querySelectorAll(focusableSelector)]
    .filter((node) => node.getClientRects().length > 0);

  document.querySelectorAll("[data-href]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("a, button, input, select")) return;
      window.location.assign(row.dataset.href);
    });
  });

  const sort = document.querySelector("[data-payout-sort]");
  sort?.addEventListener("change", () => {
    const [sortBy, direction] = sort.value.split(":");
    const url = new URL(window.location.href);
    url.searchParams.set("sort_by", sortBy);
    url.searchParams.set("sort_direction", direction);
    url.searchParams.delete("page");
    url.searchParams.delete("detail");
    window.location.assign(url.toString());
  });

  const drawer = document.querySelector("[data-payout-drawer]");
  const drawerLayer = document.querySelector("[data-payout-drawer-layer]");
  const pageModal = document.querySelector("[data-payout-page-modal]");
  const initialDialog = drawer || pageModal?.querySelector("[role='dialog']");
  if (!initialDialog) return;

  const previousFocus = document.activeElement;
  let activeModal = null;
  let activeOpener = null;
  document.body.classList.add("has-payout-dialog");
  requestAnimationFrame(() => initialDialog.focus());

  const leavePageDialog = () => {
    const close = drawerLayer?.querySelector("[data-payout-drawer-close]")
      || pageModal?.querySelector(".payout-modal-backdrop");
    if (close?.href) window.location.assign(close.href);
  };

  const closeModal = () => {
    if (!activeModal) return;
    const opener = activeOpener;
    activeModal.hidden = true;
    activeModal = null;
    activeOpener = null;
    if (opener instanceof HTMLElement) opener.focus();
  };

  drawer?.querySelectorAll("[data-payout-modal-open]").forEach((opener) => {
    opener.addEventListener("click", () => {
      const target = drawer.querySelector(`[data-payout-modal="${opener.dataset.payoutModalOpen}"]`);
      if (!target) return;
      activeModal = target;
      activeOpener = opener;
      target.hidden = false;
      const dialog = target.querySelector("[role='dialog']");
      requestAnimationFrame(() => (visibleFocusable(dialog)[0] || dialog)?.focus());
    });
  });
  drawer?.querySelectorAll("[data-payout-modal-close]").forEach((button) => {
    button.addEventListener("click", closeModal);
  });
  pageModal?.querySelectorAll("[data-payout-page-close]").forEach((button) => {
    button.addEventListener("click", leavePageDialog);
  });
  document.querySelectorAll(".payout-modal form, .payout-drawer-actions form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      if (!form.checkValidity()) return;
      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (activeModal) closeModal(); else leavePageDialog();
      return;
    }
    if (event.key !== "Tab") return;
    const scope = activeModal?.querySelector("[role='dialog']") || initialDialog;
    const nodes = visibleFocusable(scope);
    if (!nodes.length) {
      event.preventDefault();
      scope.focus();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  });

  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form[data-submitting='true']").forEach((form) => {
      form.dataset.submitting = "false";
      form.removeAttribute("aria-busy");
      form.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    });
  });
  window.addEventListener("pagehide", () => {
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  });
})();
