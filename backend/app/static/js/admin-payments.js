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
  const modalOpeners = [...drawer.querySelectorAll("[data-payment-modal-open]")];
  const modalLayers = [...drawer.querySelectorAll("[data-payment-modal]")];
  let activeModalLayer = null;
  let activeModalOpener = null;
  document.body.classList.add("has-payment-drawer");
  requestAnimationFrame(() => drawer.focus());

  const visibleFocusable = (container) => [...container.querySelectorAll(focusableSelector)]
    .filter((node) => node.getClientRects().length > 0);

  const closeModal = ({ restoreFocus = true } = {}) => {
    if (!activeModalLayer) return;
    const opener = activeModalOpener;
    activeModalLayer.hidden = true;
    activeModalLayer = null;
    activeModalOpener = null;
    document.body.classList.remove("has-payment-modal");
    modalOpeners.forEach((button) => button.setAttribute("aria-expanded", "false"));
    if (restoreFocus && opener instanceof HTMLElement) opener.focus();
  };

  const openModal = (opener) => {
    const name = opener.dataset.paymentModalOpen;
    const modalLayer = modalLayers.find((candidate) => candidate.dataset.paymentModal === name);
    if (!modalLayer) return;
    closeModal({ restoreFocus: false });
    activeModalLayer = modalLayer;
    activeModalOpener = opener;
    modalLayer.hidden = false;
    opener.setAttribute("aria-expanded", "true");
    document.body.classList.add("has-payment-modal");
    const dialog = modalLayer.querySelector("[role='dialog']");
    requestAnimationFrame(() => {
      const target = visibleFocusable(dialog || modalLayer)[0] || dialog;
      if (target instanceof HTMLElement) target.focus();
    });
  };

  modalOpeners.forEach((opener) => opener.addEventListener("click", () => openModal(opener)));
  modalLayers.forEach((modalLayer) => {
    modalLayer.querySelectorAll("[data-payment-modal-close]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!modalLayer.querySelector("[data-payment-decision-form][aria-busy='true']")) {
          closeModal();
        }
      });
    });
  });

  const rejectForm = drawer.querySelector("[data-payment-reject-form]");
  const rejectReason = rejectForm?.querySelector("[data-payment-reject-reason]");
  const publicReason = rejectForm?.querySelector("[data-payment-public-reason]");
  const publicReasonText = publicReason?.querySelector("p");
  const customReasonField = rejectForm?.querySelector("[data-payment-custom-reason]");
  const customReasonInput = customReasonField?.querySelector("textarea");
  const updateRejectReason = () => {
    if (!rejectReason) return;
    const selected = rejectReason.selectedOptions[0];
    const isOther = rejectReason.value === "OTHER";
    const message = selected?.dataset.publicReason || "";
    if (customReasonField && customReasonInput) {
      customReasonField.hidden = !isOther;
      customReasonInput.disabled = !isOther;
      customReasonInput.required = isOther;
    }
    if (publicReason && publicReasonText) {
      publicReason.hidden = !message || isOther;
      publicReasonText.textContent = isOther ? "" : message;
    }
  };
  rejectReason?.addEventListener("change", updateRejectReason);
  updateRejectReason();

  drawer.querySelectorAll("[data-payment-decision-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      if (!form.checkValidity()) return;
      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll("button").forEach((button) => {
        button.disabled = true;
      });
    });
  });

  const closeDrawer = (event) => {
    if (event) event.preventDefault();
    if (activeModalLayer) closeModal({ restoreFocus: false });
    const href = closeLinks[0]?.href;
    document.body.classList.remove("has-payment-drawer");
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
    if (href) window.location.assign(href);
  };

  closeLinks.forEach((link) => link.addEventListener("click", closeDrawer));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (activeModalLayer) {
        event.preventDefault();
        closeModal();
        return;
      }
      closeDrawer(event);
      return;
    }
    if (event.key !== "Tab") return;
    const focusScope = activeModalLayer?.querySelector("[role='dialog']") || drawer;
    const focusable = visibleFocusable(focusScope);
    if (!focusable.length) {
      event.preventDefault();
      focusScope.focus();
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
