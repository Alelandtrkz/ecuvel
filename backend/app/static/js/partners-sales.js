(() => {
  const page = document.querySelector("[data-sales-page]");
  if (!page) return;

  const refreshIcons = () => window.lucide?.createIcons?.();
  const money = (value, currency = "USD") => {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "—";
    return new Intl.NumberFormat("es-EC", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
    }).format(amount);
  };

  const selectRoot = page.querySelector("[data-sales-select]");
  if (selectRoot) {
    const native = selectRoot.querySelector("[data-sales-select-native]");
    const trigger = selectRoot.querySelector("[data-sales-select-button]");
    const menu = selectRoot.querySelector("[data-sales-select-menu]");
    const options = [...menu.querySelectorAll("[role='option']")];
    const close = (restoreFocus = false) => {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (restoreFocus) trigger.focus();
    };
    const open = () => {
      menu.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      (options.find((option) => option.getAttribute("aria-selected") === "true") || options[0])?.focus();
    };
    trigger.addEventListener("click", () => menu.hidden ? open() : close());
    options.forEach((option) => option.addEventListener("click", () => {
      native.value = option.dataset.value;
      const url = new URL(window.location.href);
      url.searchParams.set("period", native.value);
      window.location.assign(url.toString());
    }));
    selectRoot.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(true);
      }
      if (!menu.hidden && ["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        const current = options.indexOf(document.activeElement);
        const delta = event.key === "ArrowDown" ? 1 : -1;
        options[(current + delta + options.length) % options.length]?.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (!selectRoot.contains(event.target)) close();
    });
  }

  const dataElement = page.querySelector("[data-sales-chart-data]");
  const chartRoot = page.querySelector("[data-sales-chart]");
  if (dataElement && chartRoot) {
    let data = {};
    try { data = JSON.parse(dataElement.textContent || "{}"); } catch (_error) { data = {}; }
    let series = "gross";
    let granularity = "day";
    const svg = chartRoot.querySelector("[data-sales-chart-svg]");
    const empty = chartRoot.querySelector("[data-sales-chart-empty]");
    const ns = "http://www.w3.org/2000/svg";
    const element = (name, attributes = {}) => {
      const node = document.createElementNS(ns, name);
      Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
      return node;
    };
    const render = () => {
      const points = Array.isArray(data[granularity]) ? data[granularity] : [];
      svg.replaceChildren();
      svg.hidden = !points.length;
      empty.hidden = Boolean(points.length);
      if (!points.length) return;
      const width = 900;
      const height = 320;
      const padding = { top: 24, right: 18, bottom: 52, left: 68 };
      const plotWidth = width - padding.left - padding.right;
      const plotHeight = height - padding.top - padding.bottom;
      const values = points.map((point) => Number(point[series]) || 0);
      const maximum = Math.max(...values, 1);
      for (let index = 0; index <= 4; index += 1) {
        const y = padding.top + (plotHeight * index / 4);
        svg.append(element("line", { x1: padding.left, y1: y, x2: width - padding.right, y2: y, class: "sales-chart-grid" }));
        const label = element("text", { x: padding.left - 10, y: y + 4, "text-anchor": "end", class: "sales-chart-label" });
        label.textContent = money(maximum * (1 - index / 4)).replace(/\.00$/, "");
        svg.append(label);
      }
      const slot = plotWidth / points.length;
      const barWidth = Math.max(3, Math.min(34, slot * .66));
      const labelEvery = Math.max(1, Math.ceil(points.length / 7));
      points.forEach((point, index) => {
        const value = values[index];
        const barHeight = value > 0 ? Math.max(2, value / maximum * plotHeight) : 0;
        const x = padding.left + index * slot + (slot - barWidth) / 2;
        const y = padding.top + plotHeight - barHeight;
        const bar = element("rect", { x, y, width: barWidth, height: barHeight, rx: Math.min(5, barWidth / 3), class: "sales-chart-bar", tabindex: "0" });
        const title = element("title");
        title.textContent = `${point.label}: ${money(point[series])}`;
        bar.append(title);
        svg.append(bar);
        if (index % labelEvery === 0 || index === points.length - 1) {
          const label = element("text", { x: x + barWidth / 2, y: height - 20, "text-anchor": "middle", class: "sales-chart-label" });
          label.textContent = point.label;
          svg.append(label);
        }
      });
    };
    page.querySelectorAll("[data-chart-series]").forEach((button) => button.addEventListener("click", () => {
      series = button.dataset.chartSeries;
      page.querySelectorAll("[data-chart-series]").forEach((item) => item.classList.toggle("is-active", item === button));
      render();
    }));
    page.querySelectorAll("[data-chart-granularity]").forEach((button) => button.addEventListener("click", () => {
      granularity = button.dataset.chartGranularity;
      page.querySelectorAll("[data-chart-granularity]").forEach((item) => item.classList.toggle("is-active", item === button));
      render();
    }));
    render();
  }

  page.querySelectorAll("[data-top-products]").forEach((button) => button.addEventListener("click", () => {
    const selected = button.dataset.topProducts;
    page.querySelectorAll("[data-top-products]").forEach((item) => item.classList.toggle("is-active", item === button));
    page.querySelectorAll("[data-top-products-list]").forEach((list) => { list.hidden = list.dataset.topProductsList !== selected; });
  }));

  const filterEmpty = page.querySelector("[data-payout-filter-empty]");
  page.querySelectorAll("[data-payout-tab]").forEach((button) => button.addEventListener("click", () => {
    const selected = button.dataset.payoutTab;
    let visible = 0;
    page.querySelectorAll("[data-payout-tab]").forEach((item) => item.classList.toggle("is-active", item === button));
    page.querySelectorAll("[data-payout-row]").forEach((row) => {
      const show = selected === "all" || row.dataset.payoutRow === selected;
      row.hidden = !show;
      if (show) visible += 1;
    });
    if (filterEmpty) filterEmpty.hidden = visible > 0;
  }));

  const drawer = page.querySelector("[data-sales-drawer]");
  const backdrop = page.querySelector("[data-sales-drawer-backdrop]");
  if (drawer && backdrop) {
    const loading = drawer.querySelector("[data-sales-drawer-loading]");
    const content = drawer.querySelector("[data-sales-drawer-content]");
    const error = drawer.querySelector("[data-sales-drawer-error]");
    const footer = drawer.querySelector("[data-sales-drawer-footer]");
    const receipt = drawer.querySelector("[data-payout-receipt]");
    const noReceipt = drawer.querySelector("[data-payout-no-receipt]");
    let returnFocus = null;
    const fill = (selector, value) => {
      const target = drawer.querySelector(selector);
      if (target) target.textContent = value;
    };
    const close = () => {
      drawer.classList.remove("is-open");
      drawer.setAttribute("aria-hidden", "true");
      backdrop.hidden = true;
      document.body.style.overflow = "";
      returnFocus?.focus();
    };
    const open = async (button) => {
      returnFocus = button;
      backdrop.hidden = false;
      drawer.classList.add("is-open");
      drawer.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
      loading.hidden = false;
      content.hidden = true;
      error.hidden = true;
      footer.hidden = true;
      drawer.querySelector("[data-sales-drawer-close]")?.focus();
      try {
        const response = await fetch(button.dataset.payoutDetailUrl, { headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" } });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) throw new Error(payload.error || "No fue posible cargar la liquidación.");
        const payout = payload.payout;
        fill("[data-payout-status]", payout.status_label);
        fill("[data-payout-reference]", payout.reference);
        fill("[data-payout-date]", payout.date_label);
        fill("[data-payout-destination]", payout.destination_label);
        fill("[data-payout-orders]", `${payout.order_count} ${payout.order_count === 1 ? "pedido" : "pedidos"}`);
        fill("[data-payout-gross]", money(payout.gross_sales_total, payout.currency));
        fill("[data-payout-discounts]", `−${money(payout.discount_total, payout.currency)}`);
        fill("[data-payout-commission]", `−${money(payout.commission_total, payout.currency)}`);
        fill("[data-payout-net]", money(payout.net_total, payout.currency));
        receipt.hidden = !payout.receipt_available;
        noReceipt.hidden = payout.receipt_available;
        if (payout.receipt_available) receipt.href = payout.receipt_url;
        loading.hidden = true;
        content.hidden = false;
        footer.hidden = false;
        refreshIcons();
      } catch (requestError) {
        loading.hidden = true;
        error.textContent = requestError.message;
        error.hidden = false;
      }
    };
    page.querySelectorAll("[data-payout-detail-url]").forEach((button) => button.addEventListener("click", () => open(button)));
    drawer.querySelectorAll("[data-sales-drawer-close]").forEach((button) => button.addEventListener("click", close));
    backdrop.addEventListener("click", close);
    document.addEventListener("keydown", (event) => {
      if (!drawer.classList.contains("is-open")) return;
      if (event.key === "Escape") close();
      if (event.key === "Tab") {
        const focusable = [...drawer.querySelectorAll("button:not([disabled]), a[href]:not([hidden])")].filter((node) => !node.hidden);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
  }
})();
