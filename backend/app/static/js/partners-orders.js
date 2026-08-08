(() => {
  const root = document.querySelector("[data-orders-page]");
  if (!root) return;

  const csrfToken = root.dataset.csrfToken || "";
  const filters = root.querySelector("[data-orders-filters]");
  const drawer = root.querySelector("[data-order-drawer]");
  const backdrop = root.querySelector("[data-order-drawer-backdrop]");
  const drawerLoading = root.querySelector("[data-order-drawer-loading]");
  const drawerError = root.querySelector("[data-order-drawer-error]");
  const drawerContent = root.querySelector("[data-order-drawer-content]");
  const drawerActions = root.querySelector("[data-order-drawer-actions]");
  const approveDialog = root.querySelector("[data-order-approve-dialog]");
  const rejectDialog = root.querySelector("[data-order-reject-dialog]");
  const commissionDialog = root.querySelector("[data-commission-dialog]");
  let currentDetail = null;
  let currentRow = null;
  let drawerOrigin = null;

  const money = (value, currency = "USD") => {
    const amount = Number.parseFloat(value || "0");
    return new Intl.NumberFormat("es-EC", {
      style: "currency", currency, minimumFractionDigits: 2,
    }).format(Number.isFinite(amount) ? amount : 0);
  };
  const setText = (selector, value, container = root) => {
    const node = container.querySelector(selector);
    if (node) node.textContent = value ?? "";
  };
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const closeSelect = (control, restoreFocus = false) => {
    if (!control) return;
    control.classList.remove("is-open");
    const button = control.querySelector("[data-order-select-button]");
    button?.setAttribute("aria-expanded", "false");
    if (restoreFocus) button?.focus();
  };
  const selects = Array.from(root.querySelectorAll("[data-order-select]"));
  selects.forEach((control, selectIndex) => {
    const native = control.querySelector("[data-order-select-native]");
    const button = control.querySelector("[data-order-select-button]");
    const label = control.querySelector("[data-order-select-label]");
    const menu = control.querySelector("[data-order-select-menu]");
    const options = Array.from(menu?.querySelectorAll('[role="option"]') || []);
    if (!native || !button || !menu || !label) return;
    menu.id ||= `partner-order-select-${selectIndex}`;
    button.setAttribute("aria-controls", menu.id);
    control.classList.add("is-enhanced");
    const sync = () => {
      label.textContent = native.selectedOptions[0]?.textContent?.trim() || "Seleccione";
      options.forEach((option) => option.setAttribute("aria-selected", String(option.dataset.value === native.value)));
    };
    const choose = (option) => {
      native.value = option.dataset.value || "";
      sync();
      closeSelect(control);
      native.dispatchEvent(new Event("change", { bubbles: true }));
    };
    button.addEventListener("click", () => {
      const opening = !control.classList.contains("is-open");
      selects.forEach((item) => closeSelect(item));
      if (opening) {
        control.classList.add("is-open");
        button.setAttribute("aria-expanded", "true");
        (options.find((item) => item.getAttribute("aria-selected") === "true") || options[0])?.focus();
      }
    });
    options.forEach((option, index) => {
      option.addEventListener("click", () => choose(option));
      option.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const offset = event.key === "ArrowDown" ? 1 : -1;
          options[(index + offset + options.length) % options.length]?.focus();
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault(); choose(option);
        } else if (event.key === "Escape") {
          event.preventDefault(); closeSelect(control, true);
        }
      });
    });
    native.addEventListener("change", sync);
    sync();
  });
  filters?.querySelectorAll("[data-order-filter]").forEach((control) => {
    control.addEventListener("change", () => filters.requestSubmit());
  });
  document.addEventListener("click", (event) => {
    selects.forEach((control) => { if (!control.contains(event.target)) closeSelect(control); });
  });

  const openDrawer = () => {
    backdrop.hidden = false;
    drawer.setAttribute("aria-hidden", "false");
    document.body.classList.add("has-partner-order-drawer");
    drawer.querySelector("[data-order-drawer-close]")?.focus();
  };
  const closeDrawer = () => {
    drawer.setAttribute("aria-hidden", "true");
    backdrop.hidden = true;
    document.body.classList.remove("has-partner-order-drawer");
    drawerOrigin?.focus();
  };
  drawer.querySelector("[data-order-drawer-close]")?.addEventListener("click", closeDrawer);
  backdrop?.addEventListener("click", closeDrawer);

  const renderLines = (detail) => {
    const container = drawer.querySelector("[data-drawer-lines]");
    container.replaceChildren();
    detail.lines.forEach((line) => {
      const card = make("article", "partner-order-line");
      const image = document.createElement("img");
      image.src = line.image_url || "";
      image.alt = "";
      image.loading = "lazy";
      const info = make("div");
      info.append(make("strong", "", line.product_name));
      if (line.variant_name) info.append(make("span", "", line.variant_name));
      info.append(make("small", "", `SKU: ${line.sku} · Cantidad: ${line.quantity}`));
      const price = make("strong", "partner-order-line__price", money(line.line_total, detail.financials.currency));
      card.append(image, info, price);
      container.append(card);
    });
  };
  const renderDetail = (detail) => {
    currentDetail = detail;
    setText("[data-drawer-order-number]", `Orden #${detail.seller_order_number}`, drawer);
    renderLines(detail);
    setText("[data-drawer-payment-date]", detail.payment_confirmed_label, drawer);
    setText("[data-drawer-ship-by]", detail.ship_by_label || "Pendiente", drawer);
    setText("[data-drawer-delivery-window]", detail.delivery_window_label || "Pendiente", drawer);
    drawer.querySelector("[data-drawer-overdue]").hidden = !detail.is_dispatch_overdue;
    setText("[data-drawer-buyer]", detail.buyer.name, drawer);
    setText("[data-drawer-phone]", detail.buyer.phone || "Teléfono no registrado", drawer);
    setText("[data-drawer-delivery-method]", detail.delivery.method, drawer);
    setText("[data-drawer-pickup-name]", detail.delivery.name, drawer);
    setText("[data-drawer-pickup-address]", detail.delivery.address, drawer);
    setText("[data-drawer-subtotal]", money(detail.financials.product_subtotal, detail.financials.currency), drawer);
    setText("[data-drawer-commission]", `− ${money(detail.financials.commission_total, detail.financials.currency)}`, drawer);
    setText("[data-drawer-net]", money(detail.financials.seller_net_total, detail.financials.currency), drawer);
    drawer.querySelector("[data-drawer-refund]").hidden = !detail.decision.requires_refund_resolution;
    drawer.querySelector("[data-drawer-approve]").hidden = !detail.decision.can_approve;
    drawer.querySelector("[data-drawer-reject]").hidden = !detail.decision.can_reject;
    drawerActions.hidden = !(detail.decision.can_approve || detail.decision.can_reject);
  };
  const loadDetail = async (button) => {
    drawerOrigin = button;
    currentRow = button.closest("[data-order-row]");
    openDrawer();
    drawerLoading.hidden = false;
    drawerError.hidden = true;
    drawerContent.hidden = true;
    drawerActions.hidden = true;
    try {
      const response = await fetch(button.dataset.orderDetailUrl, { headers: { Accept: "application/json" } });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) throw new Error(payload.error || "No fue posible cargar el pedido.");
      renderDetail(payload.order);
      drawerContent.hidden = false;
    } catch (error) {
      drawerError.textContent = error.message;
      drawerError.hidden = false;
    } finally {
      drawerLoading.hidden = true;
    }
  };
  root.querySelectorAll("[data-order-detail-url]").forEach((button) => button.addEventListener("click", () => loadDetail(button)));

  const closeDialog = (dialog) => { if (dialog?.open) dialog.close(); };
  root.querySelectorAll("[data-dialog-close]").forEach((button) => button.addEventListener("click", () => closeDialog(button.closest("dialog"))));
  [approveDialog, rejectDialog, commissionDialog].forEach((dialog) => dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog(dialog);
  }));

  const ensureDetailForRow = async (button) => {
    const row = button.closest("[data-order-row]");
    const detailButton = row?.querySelector("[data-order-detail-url]");
    if (currentDetail?.seller_order_id === row?.dataset.orderId) {
      currentRow = row;
      return currentDetail;
    }
    const response = await fetch(detailButton.dataset.orderDetailUrl, { headers: { Accept: "application/json" } });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "No fue posible cargar el pedido.");
    currentRow = row;
    currentDetail = payload.order;
    return currentDetail;
  };
  const fillApprove = (detail) => {
    setText("[data-approve-number]", detail.seller_order_number, approveDialog);
    setText("[data-approve-subtotal]", money(detail.financials.product_subtotal, detail.financials.currency), approveDialog);
    setText("[data-approve-commission]", `− ${money(detail.financials.commission_total, detail.financials.currency)}`, approveDialog);
    setText("[data-approve-net]", money(detail.financials.seller_net_total, detail.financials.currency), approveDialog);
    approveDialog.querySelector("[data-approve-error]").hidden = true;
    approveDialog.showModal();
  };
  const openApprove = async (button) => {
    try { fillApprove(await ensureDetailForRow(button)); }
    catch (error) { window.alert(error.message); }
  };
  root.querySelectorAll("[data-order-quick-approve]").forEach((button) => button.addEventListener("click", () => openApprove(button)));
  drawer.querySelector("[data-drawer-approve]")?.addEventListener("click", () => fillApprove(currentDetail));

  const openReject = async (button) => {
    try {
      const detail = button.closest("[data-order-drawer]") ? currentDetail : await ensureDetailForRow(button);
      setText("[data-reject-number]", `Orden #${detail.seller_order_number}`, rejectDialog);
      rejectDialog.querySelector("[data-reject-comment]").value = "";
      setText("[data-reject-counter]", "0/300", rejectDialog);
      rejectDialog.querySelector("[data-reject-error]").hidden = true;
      rejectDialog.showModal();
    } catch (error) { window.alert(error.message); }
  };
  root.querySelectorAll("[data-order-quick-reject]").forEach((button) => button.addEventListener("click", () => openReject(button)));
  drawer.querySelector("[data-drawer-reject]")?.addEventListener("click", (event) => openReject(event.currentTarget));
  rejectDialog.querySelector("[data-reject-comment]")?.addEventListener("input", (event) => {
    setText("[data-reject-counter]", `${event.target.value.length}/300`, rejectDialog);
  });

  const updateMetrics = (metrics) => {
    setText("[data-orders-pending-count]", String(metrics.pending));
    const tabCount = root.querySelector("[data-orders-tab-pending]");
    if (tabCount) tabCount.textContent = String(metrics.pending);
  };
  const applyDecision = (payload) => {
    updateMetrics(payload.metrics);
    if (!currentRow) return;
    const rejected = payload.order.decision_status === "REJECTED";
    const status = currentRow.querySelector("[data-order-status]");
    status.className = `partner-orders-status partner-orders-status--${rejected ? "rejected" : "approved"}`;
    setText("[data-order-status-label]", payload.order.decision_label, currentRow);
    setText("[data-order-logistics-label]", payload.order.logistical_label, currentRow);
    currentRow.querySelectorAll("[data-order-quick-approve], [data-order-quick-reject]").forEach((button) => button.remove());
    if (root.dataset.activeTab === "pending") {
      currentRow.remove();
      currentRow = null;
    }
    if (currentDetail) {
      currentDetail.decision.status = payload.order.decision_status;
      currentDetail.decision.can_approve = false;
      currentDetail.decision.can_reject = false;
    }
  };
  const postDecision = async (kind, formData, errorNode, submitButton) => {
    formData.set("csrf_token", csrfToken);
    submitButton.disabled = true;
    errorNode.hidden = true;
    try {
      const response = await fetch(`/partners/orders/${currentDetail.seller_order_id}/${kind}`, {
        method: "POST", body: formData,
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) throw new Error(payload.error || "No fue posible guardar la decisión.");
      applyDecision(payload);
      closeDialog(kind === "approve" ? approveDialog : rejectDialog);
      closeDrawer();
    } catch (error) {
      errorNode.textContent = error.message;
      errorNode.hidden = false;
    } finally { submitButton.disabled = false; }
  };
  approveDialog.querySelector("[data-approve-confirm]")?.addEventListener("click", (event) => {
    postDecision("approve", new FormData(), approveDialog.querySelector("[data-approve-error]"), event.currentTarget);
  });
  rejectDialog.querySelector("[data-reject-form]")?.addEventListener("submit", (event) => {
    event.preventDefault();
    postDecision("reject", new FormData(event.currentTarget), rejectDialog.querySelector("[data-reject-error]"), event.submitter);
  });

  root.querySelector("[data-commission-detail]")?.addEventListener("click", () => {
    const container = commissionDialog.querySelector("[data-commission-lines]");
    const unavailable = commissionDialog.querySelector("[data-commission-unavailable]");
    container.replaceChildren();
    unavailable.hidden = currentDetail.financials.breakdown_available;
    if (currentDetail.financials.breakdown_available) {
      currentDetail.financials.commission_lines.forEach((line) => {
        const row = make("div", "partner-order-commission-line");
        const info = make("div");
        info.append(make("strong", "", line.product_name), make("span", "", `${line.category_name || "Categoría"} · ${line.rate}%`));
        row.append(info, make("strong", "", `− ${money(line.amount, currentDetail.financials.currency)}`));
        container.append(row);
      });
    }
    commissionDialog.showModal();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && drawer.getAttribute("aria-hidden") === "false" && !document.querySelector("dialog[open]")) closeDrawer();
  });
})();
