document.documentElement.classList.add("js");

document.addEventListener("DOMContentLoaded", () => {
  const toast = document.querySelector("[data-notice-toast]");
  const toastMessage = toast?.querySelector("[data-notice-message]");
  const closeButton = toast?.querySelector("[data-notice-close]");
  let closeTimer;

  const closeToast = () => {
    if (!toast) return;
    toast.classList.remove("is-visible");
    window.setTimeout(() => { toast.hidden = true; }, 180);
  };
  const showNotice = (message) => {
    if (!toast || !toastMessage) return;
    window.clearTimeout(closeTimer);
    toastMessage.textContent = message;
    toast.hidden = false;
    window.requestAnimationFrame(() => toast.classList.add("is-visible"));
    closeTimer = window.setTimeout(closeToast, 5000);
  };
  closeButton?.addEventListener("click", closeToast);
  document.querySelectorAll("[data-notice]").forEach((button) => {
    button.addEventListener("click", () => showNotice(button.dataset.notice));
  });

  const itemCheckboxes = Array.from(
    document.querySelectorAll("[data-cart-item-checkbox]:not(:disabled)"),
  );
  const selectAll = document.querySelector("[data-cart-select-all]");
  const syncSelectAll = () => {
    if (!selectAll) return;
    const selectedCount = itemCheckboxes.filter((item) => item.checked).length;
    selectAll.checked = Boolean(itemCheckboxes.length)
      && selectedCount === itemCheckboxes.length;
    selectAll.indeterminate = selectedCount > 0
      && selectedCount < itemCheckboxes.length;
  };
  itemCheckboxes.forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      syncSelectAll();
      checkbox.form?.requestSubmit();
    });
  });
  selectAll?.addEventListener("change", () => selectAll.form?.requestSubmit());
  syncSelectAll();

  document.querySelectorAll("[data-cart-quantity-form]").forEach((form) => {
    const input = form.querySelector("input[name='quantity']");
    const decrease = form.querySelector("[data-cart-quantity-decrease]");
    const increase = form.querySelector("[data-cart-quantity-increase]");
    if (!input || !decrease || !increase || input.disabled) return;

    let confirmedQuantity = Number(form.dataset.currentQuantity) || 1;
    let maximum = Number(form.dataset.maxQuantity) || 1;
    const syncControls = () => {
      const quantity = Number(input.value) || 1;
      input.max = String(maximum);
      input.dataset.maxQuantity = String(maximum);
      decrease.disabled = quantity <= 1;
      decrease.setAttribute("aria-disabled", String(decrease.disabled));
      increase.disabled = quantity >= maximum;
      increase.setAttribute("aria-disabled", String(increase.disabled));
      increase.setAttribute(
        "aria-label",
        increase.disabled
          ? "Cantidad máxima disponible alcanzada"
          : "Aumentar cantidad",
      );
    };
    const requestQuantity = (quantity) => {
      if (quantity > maximum) {
        input.value = String(maximum);
        syncControls();
        showNotice(`Solo quedan ${maximum} unidades disponibles.`);
        return;
      }
      input.value = String(Math.max(1, quantity));
      syncControls();
      if (Number(input.value) !== confirmedQuantity) form.requestSubmit();
    };
    decrease.addEventListener("click", () => requestQuantity(Number(input.value) - 1));
    increase.addEventListener("click", () => requestQuantity(Number(input.value) + 1));
    input.addEventListener("change", () => requestQuantity(Number(input.value) || 1));
    syncControls();

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
        });
        const payload = await response.json();
        if (!response.ok) {
          confirmedQuantity = Number(payload.current_cart_quantity)
            || confirmedQuantity;
          maximum = Number(payload.max_quantity) || maximum;
          input.value = String(confirmedQuantity);
          syncControls();
          showNotice(payload.message || "No fue posible actualizar la cantidad.");
          return;
        }
        window.location.assign(payload.redirect_url || window.location.href);
      } catch (_error) {
        HTMLFormElement.prototype.submit.call(form);
      }
    });
  });

  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
});
