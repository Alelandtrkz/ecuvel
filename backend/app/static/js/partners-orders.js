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
  const feedback = root.querySelector("[data-orders-feedback]");
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
  const showFeedback = (message, tone = "success") => {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.className = `partner-orders-feedback partner-orders-feedback--${tone}`;
    feedback.hidden = false;
  };
  const renderPackages = (detail) => {
    const section = drawer.querySelector("[data-drawer-packages]");
    const container = drawer.querySelector("[data-drawer-package-list]");
    section.hidden = !(detail.workflow.stage === "PREPARATION" || detail.inbound_packages.length);
    container.replaceChildren();
    if (!detail.inbound_packages.length) {
      container.append(make("p", "partner-order-packages__empty", "Todavía no has creado paquetes para este pedido."));
      return;
    }
    detail.inbound_packages.forEach((item) => {
      const card = make("article", "partner-order-package-card");
      const info = make("div", "partner-order-package-card__info");
      info.append(
        make("strong", "", item.package_code),
        make("span", `partner-order-package-state partner-order-package-state--${item.status.toLowerCase()}`, item.status_label),
      );
      if (item.received_location) info.append(make("small", "", `Recibido en ${item.received_location}`));
      const actions = make("div", "partner-order-package-card__actions");
      if (item.can_print) {
        const print = make("a", "partner-order-package-link", item.status === "CREATED" ? "Imprimir etiqueta" : "Reimprimir etiqueta");
        print.href = item.label_url;
        print.target = "_blank";
        print.rel = "noopener";
        actions.append(print);
      }
      if (item.can_mark_ready) {
        const ready = make("button", "partner-order-package-ready", "Marcar listo para Drop-off");
        ready.type = "button";
        ready.dataset.packageReadyUrl = item.ready_url;
        actions.append(ready);
      }
      card.append(info, actions);
      container.append(card);
    });
  };
  const renderTimeline = (detail) => {
    const section = drawer.querySelector("[data-drawer-timeline]");
    const list = drawer.querySelector("[data-drawer-timeline-list]");
    section.hidden = !["LOGISTICS", "COMPLETED"].includes(detail.workflow.stage);
    list.replaceChildren();
    detail.timeline.forEach((step) => {
      const item = make("li", step.is_complete ? "is-complete" : "");
      const marker = make("span", "partner-order-timeline__marker", step.is_complete ? "✓" : "○");
      const info = make("div");
      info.append(make("strong", "", step.label));
      if (step.date_label) info.append(make("small", "", step.date_label));
      item.append(marker, info);
      list.append(item);
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
    setText("[data-drawer-delivery-method]", detail.buyer_delivery.method, drawer);
    setText("[data-drawer-pickup-name]", detail.buyer_delivery.name, drawer);
    setText("[data-drawer-pickup-address]", detail.buyer_delivery.address, drawer);
    setText("[data-drawer-dropoff-instruction]", detail.seller_dropoff.instruction, drawer);
    drawer.querySelector("[data-drawer-dropoff]").hidden = detail.workflow.stage !== "PREPARATION";
    renderPackages(detail);
    renderTimeline(detail);
    setText("[data-drawer-subtotal]", money(detail.financials.product_subtotal, detail.financials.currency), drawer);
    setText("[data-drawer-commission]", `− ${money(detail.financials.commission_total, detail.financials.currency)}`, drawer);
    setText("[data-drawer-net]", money(detail.financials.seller_net_total, detail.financials.currency), drawer);
    const rejection = drawer.querySelector("[data-drawer-rejection]");
    rejection.hidden = detail.decision.status !== "REJECTED";
    setText("[data-drawer-rejection-reason]", detail.decision.rejection_reason || "Motivo no registrado", drawer);
    setText("[data-drawer-rejection-comment]", detail.decision.rejection_comment || "Sin comentario", drawer);
    setText("[data-drawer-rejected-at]", detail.decision.rejected_at_label || "Fecha no registrada", drawer);
    setText("[data-drawer-rejected-by]", detail.decision.rejected_by_name || "Sistema Ecuvel", drawer);
    drawer.querySelector("[data-drawer-refund]").hidden = !detail.decision.requires_refund_resolution;
    drawer.querySelector("[data-drawer-approve]").hidden = !detail.decision.can_approve;
    drawer.querySelector("[data-drawer-reject]").hidden = !detail.decision.can_reject;
    const createPackage = drawer.querySelector("[data-drawer-create-package]");
    createPackage.hidden = !detail.workflow.can_prepare;
    drawerActions.hidden = !(
      detail.decision.can_approve
      || detail.decision.can_reject
      || detail.workflow.can_prepare
    );
    window.lucide?.createIcons();
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

  const refreshCurrentDetail = async () => {
    if (!currentDetail) return;
    const response = await fetch(`/partners/orders/${currentDetail.seller_order_id}/detail`, {
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) throw new Error(payload.error || "No fue posible actualizar el pedido.");
    renderDetail(payload.order);
    if (currentRow) {
      const status = currentRow.querySelector("[data-order-status]");
      if (status) status.className = `partner-orders-status partner-orders-status--${payload.order.workflow.tone}`;
      setText("[data-order-status-label]", payload.order.workflow.label, currentRow);
      const received = payload.order.inbound_packages.filter((item) => item.status === "RECEIVED_BY_ECUVEL").length;
      setText(
        "[data-order-logistics-label]",
        payload.order.inbound_packages.length
          ? `${received}/${payload.order.inbound_packages.length} paquetes recibidos`
          : payload.order.logistics.label,
        currentRow,
      );
      const preparationButton = currentRow.querySelector("[data-order-open-preparation]");
      if (preparationButton) preparationButton.textContent = "Ver paquetes";
    }
  };
  const postPackageAction = async (url, submitButton, successMessage) => {
    const errorNode = drawer.querySelector("[data-package-error]");
    submitButton.disabled = true;
    errorNode.hidden = true;
    try {
      const formData = new FormData();
      formData.set("csrf_token", csrfToken);
      const response = await fetch(url, {
        method: "POST",
        body: formData,
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) throw new Error(payload.error || "No fue posible actualizar el paquete.");
      await refreshCurrentDetail();
      showFeedback(successMessage);
    } catch (error) {
      errorNode.textContent = error.message;
      errorNode.hidden = false;
    } finally {
      submitButton.disabled = false;
    }
  };
  drawer.querySelector("[data-drawer-create-package]")?.addEventListener("click", (event) => {
    postPackageAction(
      `/partners/orders/${currentDetail.seller_order_id}/packages`,
      event.currentTarget,
      "Paquete creado. La etiqueta ya está disponible para imprimir.",
    );
  });
  drawer.querySelector("[data-drawer-package-list]")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-package-ready-url]");
    if (!button) return;
    postPackageAction(
      button.dataset.packageReadyUrl,
      button,
      "Paquete listo para entregarlo en cualquier punto Ecuvel Drop-off.",
    );
  });

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
    ["pending", "preparation", "logistics", "completed", "rejected", "total"].forEach((key) => {
      const tabCount = root.querySelector(`[data-orders-tab-count="${key === "total" ? "all" : key}"]`);
      if (tabCount) tabCount.textContent = String(metrics[key]);
    });
  };
  const applyDecision = (payload) => {
    updateMetrics(payload.metrics);
    if (!currentRow) return;
    const status = currentRow.querySelector("[data-order-status]");
    status.className = `partner-orders-status partner-orders-status--${payload.order.workflow_tone}`;
    setText("[data-order-status-label]", payload.order.workflow_label, currentRow);
    setText("[data-order-logistics-label]", payload.order.logistical_label, currentRow);
    currentRow.dataset.orderWorkflowStage = payload.order.workflow_stage;
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
    if (payload.order.workflow_stage === "PREPARATION") {
      showFeedback("Pedido aprobado. Ahora debes prepararlo para entregarlo a Ecuvel.");
    } else if (payload.order.workflow_stage === "REJECTED") {
      showFeedback("Pedido rechazado. Ecuvel revisará la resolución económica.", "warning");
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
