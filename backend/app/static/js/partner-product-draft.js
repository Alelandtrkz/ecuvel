(() => {
  const root = document.querySelector("[data-product-draft]");
  if (!root) return;

  const form = document.getElementById("partner-product-draft-form");
  let dirty = false;
  let changeVersion = 0;
  let submitting = false;
  let refreshChecklist = null;
  let autosaveNow = null;
  const commissionPolicy = (() => {
    try {
      return JSON.parse(document.querySelector("[data-commission-policy]")?.textContent || "{}");
    } catch (_error) {
      return {};
    }
  })();

  const roundMoney = (value) => Math.round((Number(value) + Number.EPSILON) * 100) / 100;

  const commissionEstimate = (rawPrice) => {
    const parsedPrice = Number(rawPrice);
    const price = roundMoney(parsedPrice);
    const minimum = Number(commissionPolicy.minimum_price || 0.25);
    const threshold = Number(commissionPolicy.threshold || 3);
    const fixed = Number(commissionPolicy.fixed_amount || 0.25);
    if (!Number.isFinite(parsedPrice) || !Number.isFinite(price) || price <= minimum) return null;
    if (price < threshold) {
      return { mode: "FIXED", label: `$${fixed.toFixed(2)} fijo`, amount: fixed, net: roundMoney(price - fixed) };
    }
    const rate = Number(commissionPolicy.rate_percent);
    if (!commissionPolicy.available || !Number.isFinite(rate)) return { mode: "MISSING" };
    const amount = roundMoney(price * rate / 100);
    return { mode: "PERCENTAGE", label: `${rate.toFixed(2).replace(/\.00$/, "")}% / $${amount.toFixed(2)}`, amount, net: roundMoney(price - amount) };
  };

  const renderSingleCommission = () => {
    const summary = document.querySelector("[data-single-commission-summary]");
    const priceInput = document.querySelector('[name="price"]');
    if (!summary || !priceInput) return;
    const estimate = commissionEstimate(priceInput.value);
    const set = (selector, value) => { const node = summary.querySelector(selector); if (node) node.textContent = value; };
    if (!estimate) {
      set("[data-commission-label]", "Comisión ECUVEL");
      set("[data-commission-value]", "Pendiente");
      set("[data-commission-source]", commissionPolicy.minimum_price_message || "Define un precio válido.");
      set("[data-commission-amount]", "—");
      set("[data-commission-net]", "—");
      return;
    }
    if (estimate.mode === "MISSING") {
      set("[data-commission-value]", "Sin regla configurada");
      set("[data-commission-source]", "ECUVEL debe configurar la comisión de esta categoría antes del envío.");
      set("[data-commission-amount]", "—");
      set("[data-commission-net]", "—");
      return;
    }
    set("[data-commission-label]", estimate.mode === "FIXED" ? "Tarifa ECUVEL" : "Comisión ECUVEL");
    set("[data-commission-value]", estimate.mode === "FIXED" ? `$${estimate.amount.toFixed(2)}` : estimate.label.split(" /")[0]);
    set("[data-commission-source]", estimate.mode === "FIXED"
      ? "Tarifa fija para productos menores a USD 3.00."
      : `Determinada por: ${(commissionPolicy.category_path || []).join(" › ")}`);
    set("[data-commission-amount]", `$${estimate.amount.toFixed(2)}`);
    set("[data-commission-net]", `$${estimate.net.toFixed(2)}`);
  };

  document.querySelector('[name="price"]')?.addEventListener("input", renderSingleCommission);
  renderSingleCommission();

  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  document.querySelectorAll("[data-confirm-change]").forEach((link) => {
    link.addEventListener("click", (event) => {
      if (dirty && !window.confirm("Hay cambios sin guardar. ¿Quieres cambiar la categoría de todos modos?")) {
        event.preventDefault();
      }
    });
  });

  if (form) {
    // A nivel de documento para incluir los inputs asociados con form="..."
    // (precio, stock), cuyos eventos no burbujean a través del <form>.
    document.addEventListener("input", (event) => {
      if (event.target.form === form) {
        dirty = true;
        changeVersion += 1;
      }
    });

    window.addEventListener("beforeunload", (event) => {
      if (!dirty || submitting) return;
      event.preventDefault();
      event.returnValue = "";
    });

    form.addEventListener("submit", (event) => {
      const submitter = event.submitter;
      if (submitter?.hasAttribute("data-submit-review")) {
        const ok = window.confirm("Enviar este producto a revisión bloqueará la publicación para revisión manual. ¿Continuar?");
        if (!ok) {
          event.preventDefault();
          return;
        }
      }
      submitting = true;
      if (submitter) {
        submitter.disabled = true;
        submitter.dataset.originalText = submitter.textContent || "";
        submitter.textContent = "Procesando...";
      }
    });
  }

  function galleryMessage(gallery, message, kind = "info") {
    const target = gallery.querySelector("[data-gallery-message]");
    if (!target) return;
    target.textContent = message || "";
    target.dataset.kind = kind;
  }

  function setGalleryBusy(gallery, busy) {
    gallery.toggleAttribute("aria-busy", busy);
    gallery.querySelectorAll("button, input").forEach((control) => {
      control.disabled = Boolean(busy);
    });
  }

  function remainingSlots(gallery) {
    return Math.max(0, Number(gallery.dataset.maxImages || 0) - Number(gallery.dataset.count || 0));
  }

  function replaceGallery(gallery, html) {
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html.trim();
    const nextCollection = wrapper.querySelector("[data-gallery-collection]");
    const currentCollection = gallery.closest("[data-gallery-collection]");
    if (!nextCollection || !currentCollection) return gallery;
    currentCollection.replaceWith(nextCollection);
    nextCollection.querySelectorAll("[data-draft-gallery]").forEach(initGallery);
    refreshIcons();
    refreshChecklist?.();
    document.dispatchEvent(new CustomEvent("ecuvel:gallery-updated"));
    return nextCollection.querySelector("[data-draft-gallery]") || gallery;
  }

  async function postForm(gallery, url, formData) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-CSRFToken": gallery.dataset.csrfToken || "",
      },
      body: formData,
      credentials: "same-origin",
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.ok) {
      const errors = payload?.errors || {};
      throw new Error(Object.values(errors)[0] || "No se pudo completar la acción. Recarga la página e inténtalo otra vez.");
    }
    return payload;
  }

  async function postJson(gallery, url, data) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": gallery.dataset.csrfToken || "",
      },
      body: JSON.stringify(data),
      credentials: "same-origin",
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.ok) {
      const errors = payload?.errors || {};
      throw new Error(Object.values(errors)[0] || "No se pudo guardar el orden de las imágenes.");
    }
    return payload;
  }

  async function uploadFiles(gallery, files) {
    const fileList = Array.from(files || []);
    if (!fileList.length) return;
    const remaining = remainingSlots(gallery);
    if (fileList.length > remaining) {
      galleryMessage(gallery, `Solo puedes agregar ${remaining} imagen${remaining === 1 ? "" : "es"} más.`, "error");
      return;
    }
    const formData = new FormData();
    if (autosaveNow) {
      const saved = await autosaveNow(true);
      if (!saved) {
        galleryMessage(gallery, "Guarda la configuración de variantes antes de cargar imágenes.", "error");
        return;
      }
    }
    formData.append("csrf_token", gallery.dataset.csrfToken || "");
    formData.append("kind", "IMAGE");
    let variantAxisKey = gallery.dataset.variantAxisKey || "";
    let variantValueKey = gallery.dataset.variantValueKey || "";
    if (!variantAxisKey) {
      try {
        const config = JSON.parse(form?.querySelector("[data-variant-configuration]")?.value || "{}");
        const visual = (config.axes || []).find((axis) => axis.key === config.visual_axis_key);
        if (visual?.values?.length) {
          variantAxisKey = visual.key;
          variantValueKey = visual.values[0].key;
        }
      } catch (_error) { /* la validación del servidor mantiene el límite de confianza */ }
    }
    formData.append("variant_axis_key", variantAxisKey);
    formData.append("variant_value_key", variantValueKey);
    fileList.forEach((file) => formData.append("files", file));
    setGalleryBusy(gallery, true);
    galleryMessage(gallery, "Cargando imágenes...", "info");
    try {
      const payload = await postForm(gallery, gallery.dataset.uploadUrl, formData);
      replaceGallery(gallery, payload.gallery_html);
    } catch (error) {
      galleryMessage(gallery, error.message, "error");
      setGalleryBusy(gallery, false);
    }
  }

  function orderedIds(gallery) {
    return Array.from(gallery.querySelectorAll("[data-gallery-image]"))
      .map((item) => item.dataset.imageId)
      .filter(Boolean);
  }

  async function persistOrder(gallery, ids) {
    setGalleryBusy(gallery, true);
    galleryMessage(gallery, "Guardando orden...", "info");
    try {
      const payload = await postJson(gallery, gallery.dataset.reorderUrl, { ordered_image_ids: ids });
      replaceGallery(gallery, payload.gallery_html);
    } catch (error) {
      galleryMessage(gallery, error.message, "error");
      setGalleryBusy(gallery, false);
    }
  }

  async function submitActionForm(gallery, formElement) {
    setGalleryBusy(gallery, true);
    try {
      const payload = await postForm(gallery, formElement.action, new FormData(formElement));
      replaceGallery(gallery, payload.gallery_html);
    } catch (error) {
      galleryMessage(gallery, error.message, "error");
      setGalleryBusy(gallery, false);
    }
  }

  function bindMoveButton(gallery, button) {
    button.addEventListener("click", () => {
      const item = button.closest("[data-gallery-image]");
      const ids = orderedIds(gallery);
      const index = ids.indexOf(item?.dataset.imageId);
      if (index < 0) return;
      const direction = button.dataset.moveImage === "left" ? -1 : 1;
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= ids.length) return;
      [ids[index], ids[nextIndex]] = [ids[nextIndex], ids[index]];
      persistOrder(gallery, ids);
    });
  }

  function bindDragAndDrop(gallery) {
    let draggedId = null;
    gallery.querySelectorAll("[data-gallery-image]").forEach((item) => {
      item.addEventListener("dragstart", (event) => {
        draggedId = item.dataset.imageId;
        event.dataTransfer.effectAllowed = "move";
        item.classList.add("is-dragging");
      });
      item.addEventListener("dragend", () => {
        item.classList.remove("is-dragging");
        draggedId = null;
      });
      item.addEventListener("dragover", (event) => {
        if (draggedId) event.preventDefault();
      });
      item.addEventListener("drop", (event) => {
        event.preventDefault();
        const targetId = item.dataset.imageId;
        if (!draggedId || draggedId === targetId) return;
        const ids = orderedIds(gallery);
        const from = ids.indexOf(draggedId);
        const to = ids.indexOf(targetId);
        if (from < 0 || to < 0) return;
        ids.splice(to, 0, ids.splice(from, 1)[0]);
        persistOrder(gallery, ids);
      });
    });

    const dropzone = gallery.querySelector("[data-gallery-dropzone]");
    if (!dropzone) return;
    ["dragenter", "dragover"].forEach((name) => {
      dropzone.addEventListener(name, (event) => {
        event.preventDefault();
        dropzone.classList.add("is-dropping");
      });
    });
    ["dragleave", "drop"].forEach((name) => {
      dropzone.addEventListener(name, () => dropzone.classList.remove("is-dropping"));
    });
    dropzone.addEventListener("drop", (event) => {
      if (draggedId) return;
      event.preventDefault();
      uploadFiles(gallery, event.dataTransfer.files);
    });
  }

  function initGallery(gallery) {
    const input = gallery.querySelector("[data-gallery-input]");
    gallery.querySelectorAll("[data-open-gallery]").forEach((button) => {
      button.addEventListener("click", () => input?.click());
      button.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          input?.click();
        }
      });
    });
    input?.addEventListener("change", () => {
      uploadFiles(gallery, input.files);
      input.value = "";
    });
    gallery.querySelectorAll("[data-gallery-delete-form], [data-gallery-cover-form], [data-gallery-assign-form]").forEach((actionForm) => {
      actionForm.addEventListener("submit", (event) => {
        event.preventDefault();
        submitActionForm(gallery, actionForm);
      });
    });
    gallery.querySelectorAll("[data-move-image]").forEach((button) => bindMoveButton(gallery, button));
    bindDragAndDrop(gallery);
  }

  document.querySelectorAll("[data-draft-gallery]").forEach(initGallery);

  document.querySelectorAll("[data-copy-text]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.dataset.copyText || "";
      if (!value) return;
      const icon = button.querySelector("i[data-lucide]");
      const originalIcon = icon?.dataset.lucide;
      const originalLabel = button.getAttribute("aria-label");
      const originalTitle = button.getAttribute("title");
      try {
        await navigator.clipboard.writeText(value);
        if (icon) {
          icon.dataset.lucide = "copy-plus";
          refreshIcons();
        }
        button.setAttribute("aria-label", "Código copiado");
        button.setAttribute("title", "Código copiado");
      } catch (_error) {
        window.prompt("Copia el código del producto:", value);
      } finally {
        window.setTimeout(() => {
          if (icon) {
            icon.dataset.lucide = originalIcon;
            refreshIcons();
          }
          button.setAttribute("aria-label", originalLabel);
          button.setAttribute("title", originalTitle);
        }, 1600);
      }
    });
  });

  function initializePartnerSelects(rootEl) {
    const controls = Array.from(rootEl.querySelectorAll("[data-partner-select]"));
    let openControl = null;

    function close(control) {
      if (!control) return;
      control.classList.remove("is-open");
      control.querySelector("[data-partner-select-button]")?.setAttribute("aria-expanded", "false");
      if (openControl === control) openControl = null;
    }

    function focusOption(options, index) {
      const option = options[index];
      if (!option) return;
      option.focus({ preventScroll: true });
      option.scrollIntoView({ block: "nearest" });
    }

    function selectedIndex(options) {
      const index = options.findIndex((option) => option.getAttribute("aria-selected") === "true");
      return index >= 0 ? index : 0;
    }

    function open(control, options, preferredIndex) {
      if (openControl && openControl !== control) close(openControl);
      control.classList.add("is-open");
      control.querySelector("[data-partner-select-button]")?.setAttribute("aria-expanded", "true");
      openControl = control;
      window.requestAnimationFrame(() => focusOption(options, preferredIndex ?? selectedIndex(options)));
    }

    controls.forEach((control, index) => {
      const nativeSelect = control.querySelector("[data-partner-select-native]");
      const button = control.querySelector("[data-partner-select-button]");
      const label = control.querySelector("[data-partner-select-label]");
      const menu = control.querySelector("[data-partner-select-menu]");
      const options = Array.from(control.querySelectorAll(".partner-select__option"));
      if (!nativeSelect || !button || !label || !menu || !options.length) return;

      const menuId = menu.id || `partner-select-menu-${index}`;
      menu.id = menuId;
      button.setAttribute("aria-controls", menuId);
      control.classList.add("is-enhanced");

      function syncFromNative() {
        const selected = nativeSelect.selectedOptions?.[0];
        label.textContent = selected?.textContent?.trim() || "Seleccione una opción";
        options.forEach((option) => {
          option.setAttribute("aria-selected", String(option.dataset.value === nativeSelect.value));
        });
      }

      function choose(option) {
        nativeSelect.value = option.dataset.value || "";
        syncFromNative();
        nativeSelect.dispatchEvent(new Event("input", { bubbles: true }));
        nativeSelect.dispatchEvent(new Event("change", { bubbles: true }));
        close(control);
        button.focus({ preventScroll: true });
      }

      button.addEventListener("click", (event) => {
        event.preventDefault();
        if (control.classList.contains("is-open")) {
          close(control);
        } else {
          open(control, options);
        }
      });

      button.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          open(control, options, event.key === "ArrowUp" ? options.length - 1 : selectedIndex(options));
        }
        if (event.key === "Escape") close(control);
      });

      options.forEach((option, optionIndex) => {
        option.addEventListener("click", (event) => {
          event.preventDefault();
          choose(option);
        });
        option.addEventListener("keydown", (event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            focusOption(options, Math.min(options.length - 1, optionIndex + 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            focusOption(options, Math.max(0, optionIndex - 1));
          } else if (event.key === "Home") {
            event.preventDefault();
            focusOption(options, 0);
          } else if (event.key === "End") {
            event.preventDefault();
            focusOption(options, options.length - 1);
          } else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            choose(option);
          } else if (event.key === "Escape") {
            event.preventDefault();
            close(control);
            button.focus({ preventScroll: true });
          }
        });
      });

      nativeSelect.addEventListener("change", syncFromNative);
      syncFromNative();
    });

    document.addEventListener("click", (event) => {
      if (openControl && !openControl.contains(event.target)) close(openControl);
    });
  }

  initializePartnerSelects(root);

  function initializeAttributeQuickOptions(rootEl) {
    const chipSelector = ".partner-attribute-chip, .partner-variant-suggestion";
    rootEl.querySelectorAll("[data-quick-options], [data-field-quick-options]").forEach((container) => {
      const label = container.closest("label");
      const input = label?.querySelector("input, textarea") || label?.querySelector("select");
      const unitSelect = label?.querySelector("[data-unit-select]");
      if (!input) return;

      function syncChips() {
        container.querySelectorAll(chipSelector).forEach((chip) => {
          const valueMatches = chip.dataset.value === input.value;
          const unitMatches = !chip.dataset.unit || !unitSelect || chip.dataset.unit === unitSelect.value;
          chip.setAttribute("aria-pressed", String(valueMatches && unitMatches));
        });
      }

      container.querySelectorAll(chipSelector).forEach((chip) => {
        chip.addEventListener("click", () => {
          input.value = chip.dataset.value;
          if (chip.dataset.unit && unitSelect) {
            unitSelect.value = chip.dataset.unit;
            unitSelect.dispatchEvent(new Event("change", { bubbles: true }));
          }
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
          syncChips();
        });
      });

      input.addEventListener("input", syncChips);
      if (unitSelect) unitSelect.addEventListener("change", syncChips);
    });
  }

  initializeAttributeQuickOptions(root);

  function initializeConditionalFields(rootEl) {
    const condWrappers = rootEl.querySelectorAll("[data-condition-field]");
    if (!condWrappers.length) return;

    const triggerMap = new Map();
    condWrappers.forEach((wrapper) => {
      const key = wrapper.dataset.conditionField;
      if (!triggerMap.has(key)) triggerMap.set(key, []);
      triggerMap.get(key).push(wrapper);
    });

    triggerMap.forEach((wrappers, triggerKey) => {
      const triggerEl = rootEl.querySelector(`[name="attributes[${triggerKey}]"]`);
      if (!triggerEl) return;

      const evaluate = () => {
        const currentVal = triggerEl.value;
        wrappers.forEach((wrapper) => {
          const allowed = wrapper.dataset.conditionValues.split(",");
          wrapper.hidden = !allowed.includes(currentVal);
        });
      };

      triggerEl.addEventListener("change", evaluate);
      evaluate();
    });
  }

  initializeConditionalFields(root);

  function initializeVariantBuilder(rootEl) {
    const section = rootEl.querySelector("[data-variants-section]");
    if (!section) return;

    const toggle = section.querySelector("[data-variants-toggle]");
    const panel = section.querySelector("[data-variants-panel]");
    const axesContainer = section.querySelector("[data-variant-axes]");
    const generateBtn = section.querySelector("[data-generate-variants]");
    const table = section.querySelector("[data-variant-table]");
    const rowsContainer = section.querySelector("[data-variant-rows]");
    const rowTemplate = section.querySelector("[data-variant-row-template]");
    const message = section.querySelector("[data-variant-message]");
    const addRowBtn = section.querySelector("[data-add-variant-row]");
    const MAX_COMBINATIONS = 12;

    if (toggle && panel) {
      toggle.addEventListener("change", () => {
        panel.hidden = !toggle.checked;
      });
    }

    function showMessage(text, kind = "info") {
      if (!message) return;
      message.textContent = text || "";
      message.dataset.kind = kind;
      message.hidden = !text;
    }

    function axisValues(axis) {
      return Array.from(axis.querySelectorAll("[data-axis-value]")).map((chip) => chip.dataset.axisValue);
    }

    function addAxisChip(axis, rawValue) {
      const value = String(rawValue).trim();
      if (!value) return;
      if (axisValues(axis).includes(value)) return;
      const chips = axis.querySelector("[data-axis-chips]");
      const chip = document.createElement("span");
      chip.className = "partner-variant-chip";
      chip.dataset.axisValue = value;
      const unit = axis.dataset.axisUnit;
      chip.innerHTML = `<span>${value}${unit ? ` ${unit}` : ""}</span><button type="button" aria-label="Quitar ${value}">&times;</button>`;
      chip.querySelector("button").addEventListener("click", () => chip.remove());
      chips.appendChild(chip);
    }

    function clearAxis(axis) {
      axis.querySelectorAll("[data-axis-value]").forEach((chip) => chip.remove());
    }

    if (axesContainer) {
      axesContainer.querySelectorAll("[data-variant-axis]").forEach((axis) => {
        const input = axis.querySelector("[data-axis-input]");
        if (input) {
          input.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === ",") {
              event.preventDefault();
              addAxisChip(axis, input.value.replace(/,+$/, ""));
              input.value = "";
            }
          });
          input.addEventListener("blur", () => {
            if (input.value.trim()) {
              addAxisChip(axis, input.value);
              input.value = "";
            }
          });
        }
        axis.querySelectorAll("[data-axis-suggestion]").forEach((btn) => {
          btn.addEventListener("click", () => addAxisChip(axis, btn.dataset.axisSuggestion));
        });
      });

      // Al ocultarse un eje por condición (mismo mecanismo que los campos), limpiar sus valores
      const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          const axis = mutation.target;
          if (axis.hasAttribute("hidden")) clearAxis(axis);
        });
      });
      axesContainer.querySelectorAll("[data-variant-axis][data-condition-field]").forEach((axis) => {
        observer.observe(axis, { attributes: true, attributeFilter: ["hidden"] });
      });
    }

    function visibleAxesWithValues() {
      if (!axesContainer) return [];
      return Array.from(axesContainer.querySelectorAll("[data-variant-axis]"))
        .filter((axis) => !axis.hidden)
        .map((axis) => ({
          label: axis.dataset.axisLabel,
          unit: axis.dataset.axisUnit,
          values: axisValues(axis),
        }))
        .filter((axis) => axis.values.length > 0);
    }

    function cartesian(axes) {
      return axes.reduce(
        (acc, axis) => acc.flatMap((combo) => axis.values.map((value) => [...combo, { value, unit: axis.unit }])),
        [[]]
      );
    }

    function existingRowData() {
      const data = new Map();
      rowsContainer.querySelectorAll("[data-variant-row]").forEach((row) => {
        const name = row.querySelector("[name='variant_name[]']")?.value;
        if (!name) return;
        data.set(name, {
          price: row.querySelector("[name='variant_price[]']")?.value || "",
          stock: row.querySelector("[name='variant_stock[]']")?.value || "",
        });
      });
      return data;
    }

    function buildRow(name, price, stock, readonly) {
      const fragment = rowTemplate.content.cloneNode(true);
      const row = fragment.querySelector("[data-variant-row]");
      const nameInput = row.querySelector("[name='variant_name[]']");
      nameInput.value = name;
      if (readonly) nameInput.readOnly = true;
      row.querySelector("[name='variant_price[]']").value = price;
      row.querySelector("[name='variant_stock[]']").value = stock;
      return row;
    }

    if (generateBtn) {
      generateBtn.addEventListener("click", () => {
        const axes = visibleAxesWithValues();
        if (!axes.length) {
          showMessage("Agrega al menos un valor en algún eje para generar combinaciones.", "error");
          return;
        }
        const combos = cartesian(axes);
        if (combos.length > MAX_COMBINATIONS) {
          showMessage(`Demasiadas combinaciones (${combos.length}). El máximo es ${MAX_COMBINATIONS}; reduce los valores.`, "error");
          return;
        }
        const previous = existingRowData();
        rowsContainer.innerHTML = "";
        combos.forEach((combo) => {
          const name = combo.map((part) => (part.unit ? `${part.value} ${part.unit}` : part.value)).join(" / ");
          const prev = previous.get(name) || { price: "", stock: "" };
          rowsContainer.appendChild(buildRow(name, prev.price, prev.stock, true));
        });
        table.hidden = false;
        showMessage(`${combos.length} variante${combos.length === 1 ? "" : "s"} generada${combos.length === 1 ? "" : "s"}. Completa precio y stock.`);
        dirty = true;
        refreshChecklist?.();
      });
    }

    if (addRowBtn) {
      addRowBtn.addEventListener("click", () => {
        rowsContainer.appendChild(buildRow("", "", "", false));
        table.hidden = false;
        refreshChecklist?.();
      });
    }
  }

  function initializeVariantBuilderV2(rootEl) {
    const section = rootEl.querySelector("[data-variants-section]");
    if (!section) return;
    const toggle = section.querySelector("[data-variants-toggle]");
    const panel = section.querySelector("[data-variants-panel]");
    const axesContainer = section.querySelector("[data-variant-axes]");
    const picker = section.querySelector("[data-add-axis-select]");
    const addAxisButton = section.querySelector("[data-add-axis]");
    const generateButton = section.querySelector("[data-generate-variants]");
    const table = section.querySelector("[data-variant-table]");
    const rowsContainer = section.querySelector("[data-variant-rows]");
    const message = section.querySelector("[data-variant-message]");
    const configInput = section.querySelector("[data-variant-configuration]");
    const maxAxes = Number(section.dataset.maxAxes || 3);
    const maxValues = Number(section.dataset.maxValues || 12);
    const maxCombinations = Number(section.dataset.maxCombinations || 50);

    function parseJson(selector, fallback) {
      try {
        const node = section.querySelector(selector);
        return JSON.parse(node?.textContent || JSON.stringify(fallback));
      } catch (_error) {
        return fallback;
      }
    }

    const axisCatalog = parseJson("[data-available-variant-axes]", []);
    const existingVariants = parseJson("[data-existing-variants]", []);
    let configuration = {};
    try { configuration = JSON.parse(configInput?.value || "{}"); } catch (_error) { configuration = {}; }
    const state = {
      axes: Array.isArray(configuration.axes) ? configuration.axes.map((axis) => ({ ...axis, values: [...(axis.values || [])] })) : [],
      defaultCombinationKey: configuration.default_combination_key || "",
      nextSkuSequence: configuration.next_sku_sequence || 1,
    };

    const slug = (value) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80) || "valor";
    const productTypeInput = form?.querySelector('[name="attributes[tipo_producto]"]');
    const productType = () => productTypeInput?.value || "";
    let previousProductType = productType();
    const allowedAxes = () => axisCatalog.filter((axis) => !axis.allowed_product_types?.length || axis.allowed_product_types.includes(productType()));
    const axisDefinition = (key) => axisCatalog.find((axis) => axis.key === key);
    const sourceValue = (key) => String(form?.elements.namedItem(`attributes[${key}]`)?.value || "").trim();

    function showMessage(text, kind = "info") {
      if (!message) return;
      message.textContent = text;
      message.dataset.kind = kind;
      message.hidden = !text;
    }

    function notifyChange() {
      syncConfiguration();
      configInput?.dispatchEvent(new Event("input", { bubbles: true }));
      refreshChecklist?.();
    }

    function seedAxis(definition) {
      const initial = sourceValue(definition.source_field || definition.key);
      return {
        key: definition.key,
        label: definition.label,
        unit: definition.unit || "",
        value_type: definition.value_type || "text",
        source_field: definition.source_field || definition.key,
        is_visual: Boolean(definition.is_visual),
        values: initial ? [{ key: slug(initial), label: initial, swatch: null }] : [],
      };
    }

    function ensureDefaults() {
      if (state.axes.length || !toggle?.checked) return;
      allowedAxes().filter((axis) => axis.default_for?.includes(productType())).slice(0, maxAxes)
        .forEach((axis) => state.axes.push(seedAxis(axis)));
    }

    function syncConfiguration() {
      const checkedDefault = section.querySelector('[name="variant_default_choice"]:checked');
      if (checkedDefault) state.defaultCombinationKey = checkedDefault.value;
      const visual = state.axes.find((axis) => axis.is_visual);
      const payload = {
        version: 2,
        axes: state.axes,
        visual_axis_key: visual?.key || null,
        default_combination_key: state.defaultCombinationKey || null,
        next_sku_sequence: state.nextSkuSequence,
      };
      if (configInput) configInput.value = JSON.stringify(payload);
    }

    function syncTechnicalFields() {
      const managed = new Set(
        toggle?.checked ? state.axes.map((axis) => axis.source_field).filter(Boolean) : [],
      );
      form?.querySelectorAll("[data-attr-key]").forEach((field) => {
        const isManaged = managed.has(field.dataset.attrKey);
        field.classList.toggle("is-managed-by-variants", isManaged);
        field.querySelectorAll("input, textarea, select, [data-partner-select-button]").forEach((control) => {
          control.disabled = isManaged;
        });
        let note = field.querySelector("[data-variant-managed-note]");
        if (isManaged && !note) {
          note = document.createElement("small");
          note.dataset.variantManagedNote = "";
          note.textContent = "Este valor se define en cada combinación de variante.";
          field.appendChild(note);
        }
        if (note) note.hidden = !isManaged;
      });
    }

    function renderPicker() {
      if (!picker) return;
      const selected = new Set(state.axes.map((axis) => axis.key));
      const choices = allowedAxes().filter((axis) => !selected.has(axis.key));
      picker.innerHTML = '<option value="">Selecciona un campo</option>';
      choices.forEach((axis) => {
        const option = document.createElement("option");
        option.value = axis.key;
        option.textContent = `${axis.label}${axis.unit ? ` (${axis.unit})` : ""}`;
        picker.appendChild(option);
      });
      picker.disabled = state.axes.length >= maxAxes || !choices.length;
      if (addAxisButton) addAxisButton.disabled = picker.disabled;
    }

    function addValue(axis, rawValue, swatch = null) {
      const label = String(rawValue || "").trim();
      if (!label) return;
      const key = slug(label);
      if (axis.values.some((value) => value.key === key)) return;
      if (axis.values.length >= maxValues) {
        showMessage(`${axis.label} admite hasta ${maxValues} valores.`, "error");
        return;
      }
      axis.values.push({ key, label, swatch: axis.is_visual ? swatch : null });
      renderAxes();
      notifyChange();
    }

    function renderAxes() {
      if (!axesContainer) return;
      axesContainer.replaceChildren();
      state.axes.forEach((axis, axisIndex) => {
        const definition = axisDefinition(axis.key) || axis;
        const card = document.createElement("article");
        card.className = "partner-variant-axis";
        card.dataset.variantAxis = axis.key;
        const heading = document.createElement("div");
        heading.className = "partner-variant-axis__heading";
        heading.innerHTML = `<strong>${axis.label}${axis.unit ? ` <em>(${axis.unit})</em>` : ""}</strong>`;
        const headingActions = document.createElement("span");
        headingActions.className = "partner-variant-axis__heading-actions";
        [
          ["Subir", -1, "arrow-up"],
          ["Bajar", 1, "arrow-down"],
        ].forEach(([label, offset, icon]) => {
          const move = document.createElement("button");
          move.type = "button";
          move.className = "partner-variant-axis__move";
          move.disabled = axisIndex + offset < 0 || axisIndex + offset >= state.axes.length;
          move.setAttribute("aria-label", `${label} ${axis.label}`);
          move.innerHTML = `<i data-lucide="${icon}" aria-hidden="true"></i>`;
          move.addEventListener("click", () => {
            const nextIndex = axisIndex + offset;
            [state.axes[axisIndex], state.axes[nextIndex]] = [state.axes[nextIndex], state.axes[axisIndex]];
            state.defaultCombinationKey = "";
            renderAxes(); clearRows(); notifyChange(); refreshIcons();
          });
          headingActions.appendChild(move);
        });
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "partner-variant-axis__remove";
        remove.textContent = "Quitar";
        remove.addEventListener("click", () => {
          if (rowsContainer?.children.length && !window.confirm("Quitar este campo regenerará las combinaciones. Las imágenes no se eliminarán. ¿Continuar?")) return;
          state.axes.splice(axisIndex, 1);
          state.defaultCombinationKey = "";
          renderAxes(); renderPicker(); clearRows(); notifyChange();
        });
        headingActions.appendChild(remove);
        heading.appendChild(headingActions);
        card.appendChild(heading);

        const chips = document.createElement("div");
        chips.className = "partner-variant-axis__chips";
        axis.values.forEach((value, valueIndex) => {
          const chip = document.createElement("span");
          chip.className = "partner-variant-chip";
          if (value.swatch) chip.style.setProperty("--variant-swatch", value.swatch);
          chip.innerHTML = `<span>${value.label}${axis.unit ? ` ${axis.unit}` : ""}</span>`;
          const removeValue = document.createElement("button");
          removeValue.type = "button"; removeValue.textContent = "×";
          removeValue.setAttribute("aria-label", `Quitar ${value.label}`);
          removeValue.addEventListener("click", () => {
            axis.values.splice(valueIndex, 1);
            state.defaultCombinationKey = "";
            renderAxes(); clearRows(); notifyChange();
          });
          chip.appendChild(removeValue); chips.appendChild(chip);
        });
        card.appendChild(chips);

        const entry = document.createElement("div");
        entry.className = "partner-variant-axis__entry";
        const input = document.createElement("input");
        input.type = axis.value_type === "integer" || axis.value_type === "decimal" ? "number" : "text";
        input.step = axis.value_type === "integer" ? "1" : axis.value_type === "decimal" ? "0.01" : "";
        input.placeholder = "Escribe un valor y presiona Enter";
        let colorInput = null;
        if (axis.is_visual) {
          colorInput = document.createElement("input");
          colorInput.type = "color"; colorInput.value = "#111827"; colorInput.setAttribute("aria-label", "Color visual");
          entry.appendChild(colorInput);
        }
        input.addEventListener("keydown", (event) => {
          if (event.key !== "Enter") return;
          event.preventDefault(); addValue(axis, input.value, colorInput?.value || null);
        });
        entry.appendChild(input); card.appendChild(entry);

        if (definition.suggestions?.length) {
          const suggestions = document.createElement("div");
          suggestions.className = "partner-variant-axis__suggestions";
          definition.suggestions.forEach((suggestion) => {
            const button = document.createElement("button");
            button.type = "button"; button.className = "partner-variant-suggestion";
            button.textContent = `${suggestion}${axis.unit ? ` ${axis.unit}` : ""}`;
            button.addEventListener("click", () => addValue(axis, suggestion, null));
            suggestions.appendChild(button);
          });
          card.appendChild(suggestions);
        }
        axesContainer.appendChild(card);
      });
      renderPicker(); syncConfiguration(); syncTechnicalFields();
    }

    function combinations() {
      if (!state.axes.length || state.axes.some((axis) => !axis.values.length)) return [];
      return state.axes.reduce(
        (result, axis) => result.flatMap((combo) => axis.values.map((value) => [...combo, { axis, value }])),
        [[]],
      );
    }

    function combinationData(combo) {
      return {
        key: combo.map(({ axis, value }) => `${axis.key}=${value.key}`).join("|"),
        name: combo.map(({ axis, value }) => `${value.label}${axis.unit ? ` ${axis.unit}` : ""}`).join(" / "),
      };
    }

    function currentRows() {
      const map = new Map();
      rowsContainer?.querySelectorAll("[data-variant-row]").forEach((row) => {
        const key = row.querySelector('[name="variant_combination_key[]"]')?.value;
        if (!key) return;
        map.set(key, {
          name: row.querySelector(".partner-variant-row__identity strong")?.textContent || "",
          sku: row.querySelector(".partner-variant-row__identity small")?.textContent || "",
          price: row.querySelector('[name="variant_price[]"]')?.value || "",
          stock: row.querySelector('[name="variant_stock[]"]')?.value || "",
          enabled: row.querySelector("[data-variant-enabled]")?.checked !== false,
        });
      });
      existingVariants.forEach((row) => {
        if (row.combination_key && !map.has(row.combination_key)) map.set(row.combination_key, row);
      });
      return map;
    }

    function buildRow(data, previous, makeDefault) {
      const row = document.createElement("div"); row.className = "partner-variant-row"; row.dataset.variantRow = "";
      const controls = document.createElement("span"); controls.className = "partner-variant-row__controls";
      const enabledValue = document.createElement("input"); enabledValue.type = "hidden"; enabledValue.name = "variant_enabled[]"; enabledValue.dataset.variantEnabledValue = "";
      const enabled = document.createElement("input"); enabled.type = "checkbox"; enabled.dataset.variantEnabled = ""; enabled.checked = previous?.enabled !== false; enabled.setAttribute("aria-label", "Usar variante");
      enabledValue.value = enabled.checked ? "1" : "0";
      const defaultChoice = document.createElement("input"); defaultChoice.type = "radio"; defaultChoice.name = "variant_default_choice"; defaultChoice.value = data.key; defaultChoice.checked = makeDefault; defaultChoice.setAttribute("aria-label", "Variante predeterminada");
      enabled.addEventListener("change", () => { enabledValue.value = enabled.checked ? "1" : "0"; if (!enabled.checked && defaultChoice.checked) defaultChoice.checked = false; notifyChange(); });
      defaultChoice.addEventListener("change", notifyChange);
      controls.append(enabledValue, enabled, defaultChoice);
      const identity = document.createElement("span"); identity.className = "partner-variant-row__identity";
      const keyInput = document.createElement("input"); keyInput.type = "hidden"; keyInput.name = "variant_combination_key[]"; keyInput.value = data.key;
      const name = document.createElement("strong"); name.textContent = data.name;
      const sku = document.createElement("small"); sku.textContent = previous?.sku || "SKU al guardar";
      identity.append(keyInput, name, sku);
      const price = document.createElement("input"); price.name = "variant_price[]"; price.inputMode = "decimal"; price.placeholder = "0.00"; price.value = previous?.price || String(form?.elements.namedItem("price")?.value || "");
      const stock = document.createElement("input"); stock.name = "variant_stock[]"; stock.inputMode = "numeric"; stock.placeholder = "0"; stock.value = previous?.stock || String(form?.elements.namedItem("stock_quantity")?.value || "");
      row.append(controls, identity, price, stock); return row;
    }

    function clearRows() {
      rowsContainer?.replaceChildren(); if (table) table.hidden = true;
    }

    function generateRows() {
      const combos = combinations();
      if (!state.axes.length) return showMessage("Agrega al menos un campo de variante.", "error");
      if (state.axes.some((axis) => !axis.values.length)) return showMessage("Cada campo necesita al menos un valor.", "error");
      if (combos.length > maxCombinations) {
        const detail = state.axes.map((axis) => `${axis.label} (${axis.values.length})`).join(" × ");
        return showMessage(`La selección ${detail} genera ${combos.length} combinaciones; el máximo es ${maxCombinations}.`, "error");
      }
      const previous = currentRows(); clearRows();
      combos.forEach((combo, index) => {
        const data = combinationData(combo);
        const old = previous.get(data.key) || existingVariants.find((row) => String(row.name || "").toLowerCase() === data.name.toLowerCase());
        const isDefault = state.defaultCombinationKey ? state.defaultCombinationKey === data.key : index === 0;
        rowsContainer?.appendChild(buildRow(data, old, isDefault));
        if (isDefault) state.defaultCombinationKey = data.key;
      });
      if (table) table.hidden = false;
      showMessage(`${combos.length} combinación${combos.length === 1 ? "" : "es"} generada${combos.length === 1 ? "" : "s"}.`, "success");
      notifyChange();
    }

    toggle?.addEventListener("change", () => {
      if (panel) panel.hidden = !toggle.checked;
      if (toggle.checked) { ensureDefaults(); renderAxes(); }
      else { state.axes = []; state.defaultCombinationKey = ""; clearRows(); syncTechnicalFields(); notifyChange(); }
    });
    addAxisButton?.addEventListener("click", () => {
      const definition = axisDefinition(picker?.value || "");
      if (!definition || state.axes.length >= maxAxes) return;
      state.axes.push(seedAxis(definition)); picker.value = ""; renderAxes(); notifyChange();
    });
    generateButton?.addEventListener("click", generateRows);
    productTypeInput?.addEventListener("change", () => {
      const invalid = state.axes.filter((axis) => !allowedAxes().some((candidate) => candidate.key === axis.key));
      if (invalid.length && !window.confirm("El tipo de producto cambió. Se quitarán campos de variante incompatibles y deberás regenerar. ¿Continuar?")) {
        productTypeInput.value = previousProductType;
        const select = productTypeInput.closest("[data-partner-select]");
        const label = select?.querySelector("[data-partner-select-label]");
        if (label) label.textContent = previousProductType || "Seleccione una opción";
        select?.querySelectorAll(".partner-select__option[data-value]").forEach((option) => {
          option.setAttribute("aria-selected", String(option.dataset.value === previousProductType));
        });
        return;
      }
      previousProductType = productType();
      state.axes = state.axes.filter((axis) => allowedAxes().some((candidate) => candidate.key === axis.key));
      state.defaultCombinationKey = ""; clearRows(); ensureDefaults(); renderAxes(); notifyChange();
    });
    form?.addEventListener("formdata", syncConfiguration);
    ensureDefaults(); renderAxes(); renderPicker(); syncConfiguration();
  }

  function initializeManualVariantBuilder(rootEl) {
    const section = rootEl.querySelector("[data-variants-section]");
    if (!section || !form) return;
    const toggle = section.querySelector("[data-variants-toggle]");
    const panel = section.querySelector("[data-variants-panel]");
    const configInput = section.querySelector("[data-variant-configuration]");
    const picker = section.querySelector("[data-add-axis-select]");
    const pickerControl = section.querySelector("[data-variant-axis-select]");
    const pickerButton = section.querySelector("[data-variant-axis-select-button]");
    const pickerLabel = section.querySelector("[data-variant-axis-select-label]");
    const pickerMenu = section.querySelector("[data-variant-axis-select-menu]");
    const addFieldButton = section.querySelector("[data-add-axis]");
    const selectedFields = section.querySelector("[data-selected-variant-fields]");
    const editorOptions = section.querySelector("[data-variant-option-fields]");
    const editorTitle = section.querySelector("[data-variant-editor-title]");
    const editorPrice = section.querySelector("[data-new-variant-price]");
    const editorComparePrice = section.querySelector("[data-new-variant-compare-price]");
    const editorStock = section.querySelector("[data-new-variant-stock]");
    const saveVariantButton = section.querySelector("[data-save-manual-variant]");
    const cancelEditButton = section.querySelector("[data-cancel-variant-edit]");
    const rowsContainer = section.querySelector("[data-variant-rows]");
    const table = section.querySelector("[data-variant-table]");
    const countLabel = section.querySelector("[data-manual-variant-count]");
    const message = section.querySelector("[data-variant-message]");
    const editor = section.querySelector("[data-variant-editor]");
    const drawerBackdrop = section.querySelector("[data-variant-drawer-backdrop]");
    const openEditorButton = section.querySelector("[data-open-variant-editor]");
    const batchEditor = section.querySelector("[data-variant-batch]");
    const activation = section.querySelector("[data-variant-activation]");
    const startEmptyButton = section.querySelector("[data-start-empty-variants]");
    const convertBaseButton = section.querySelector("[data-convert-base-variant]");
    const convertBaseInput = section.querySelector("[data-variant-convert-base]");
    const generalGalleryHost = rootEl.querySelector("[data-general-gallery-host]");
    const variantGalleryHost = section.querySelector("[data-variant-gallery-host]");
    const commercialSummary = rootEl.querySelector("[data-family-commercial-summary]");
    const singleCommercialFields = rootEl.querySelector("[data-single-commercial-fields]");
    const maxAxes = Number(section.dataset.maxAxes || 3);
    const maxValues = Number(section.dataset.maxValues || 12);
    const maxVariants = Number(section.dataset.maxCombinations || 50);

    const parseJson = (selector, fallback) => {
      try { return JSON.parse(section.querySelector(selector)?.textContent || JSON.stringify(fallback)); }
      catch (_error) { return fallback; }
    };
    const catalog = parseJson("[data-available-variant-axes]", []);
    const existing = parseJson("[data-existing-variants]", []);
    let savedConfig = {};
    try { savedConfig = JSON.parse(configInput?.value || "{}"); } catch (_error) { savedConfig = {}; }
    const productTypeInput = form.querySelector('[name="attributes[tipo_producto]"]');
    let previousProductType = productTypeInput?.value || "";
    const productType = () => productTypeInput?.value || "";
    const allowedFields = () => catalog.filter(
      (field) => !field.allowed_product_types?.length || field.allowed_product_types.includes(productType()),
    );
    const definition = (key) => catalog.find((field) => field.key === key);
    const technicalValue = (source) => String(form.elements.namedItem(`attributes[${source}]`)?.value || "").trim();
    const newId = () => `v-${window.crypto?.randomUUID?.().replaceAll("-", "") || `${Date.now()}${Math.random().toString(16).slice(2)}`}`;
    const slug = (value) => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

    const state = {
      fields: Array.isArray(savedConfig.axes)
        ? savedConfig.axes.map((axis) => axis.key).filter(Boolean).slice(0, maxAxes)
        : [],
      variants: [],
      defaultId: savedConfig.default_variant_id || "",
      editingId: null,
      pendingField: null,
      sourceSnapshot: savedConfig.source_snapshot || null,
      activationPending: false,
    };

    function ensureDefaultFields() {
      if (state.fields.length || !toggle?.checked) return;
      allowedFields().filter((field) => field.default_for?.includes(productType()))
        .slice(0, Math.min(2, maxAxes)).forEach((field) => state.fields.push(field.key));
    }
    ensureDefaultFields();

    function optionFromExisting(row, fieldKey) {
      const label = row.attributes?.[fieldKey] ?? row.options?.[fieldKey] ?? "";
      const configuredAxis = (savedConfig.axes || []).find((axis) => axis.key === fieldKey);
      const configuredValue = (configuredAxis?.values || []).find(
        (value) => value.key === row.options?.[fieldKey] || value.label === label,
      );
      return {
        label: String(label || ""),
        swatch: row.swatches?.[fieldKey] || configuredValue?.swatch || null,
      };
    }

    existing.forEach((row) => {
      const options = Object.fromEntries(state.fields.map((key) => [key, optionFromExisting(row, key)]));
      state.variants.push({
        variantId: row.variant_id || newId(),
        previousCombinationKey: row.combination_key || "",
        options,
        sku: row.sku || "",
        price: String(row.price || ""),
        compareAtPrice: String(row.compare_at_price || ""),
        stock: String(row.stock || ""),
        enabled: row.enabled !== false,
      });
    });
    if (!state.defaultId && savedConfig.default_combination_key) {
      state.defaultId = state.variants.find(
        (row) => row.previousCombinationKey === savedConfig.default_combination_key,
      )?.variantId || "";
    }
    if (!state.defaultId) state.defaultId = state.variants.find((row) => row.enabled)?.variantId || "";

    function showMessage(textValue, kind = "info") {
      if (!message) return;
      message.textContent = textValue || "";
      message.dataset.kind = kind;
      message.hidden = !textValue;
    }

    const rowKey = (options, fields = state.fields) => fields.map(
      (key) => `${key}=${slug(options[key]?.label)}`,
    ).join("|");
    const rowName = (row) => state.fields.map((key) => {
      const field = definition(key) || {};
      const label = row.options[key]?.label || "Sin definir";
      return field.unit ? `${label} ${field.unit}` : label;
    }).join(" / ");

    function distinctValues(fieldKey) {
      const values = new Map();
      state.variants.forEach((row) => {
        const option = row.options[fieldKey];
        if (option?.label) values.set(slug(option.label), option);
      });
      return [...values.values()];
    }

    function syncConfiguration() {
      const axes = state.fields.map((key) => {
        const field = definition(key) || {};
        return {
          key,
          label: field.label || key,
          unit: field.unit || "",
          value_type: field.value_type || "text",
          source_field: field.source_field || key,
          is_visual: Boolean(field.is_visual),
          values: distinctValues(key).map((option) => ({
            key: slug(option.label), label: option.label, swatch: option.swatch || null,
          })),
        };
      });
      const defaultRow = state.variants.find((row) => row.variantId === state.defaultId);
      if (configInput) configInput.value = JSON.stringify({
        version: 4,
        enabled: Boolean(toggle?.checked),
        mode: toggle?.checked ? "family" : "single",
        axes,
        visual_axis_key: axes.find((axis) => axis.is_visual)?.key || null,
        default_variant_id: state.defaultId || null,
        default_combination_key: defaultRow ? rowKey(defaultRow.options) : null,
        next_sku_sequence: savedConfig.next_sku_sequence || 1,
        source_snapshot: state.sourceSnapshot || {},
        archived_family: !toggle?.checked && state.variants.length > 0,
      });
    }

    function notifyChange() {
      syncConfiguration();
      configInput?.dispatchEvent(new Event("input", { bubbles: true }));
      refreshChecklist?.();
    }

    function renderTechnicalBadges() {
      const sources = new Set(state.fields.map((key) => definition(key)?.source_field || key));
      form.querySelectorAll("[data-attr-key]").forEach((field) => {
        const active = sources.has(field.dataset.attrKey) && toggle?.checked;
        field.classList.toggle("is-variant-source", active);
        let badge = field.querySelector("[data-variant-source-badge]");
        if (active && !badge) {
          badge = document.createElement("span");
          badge.className = "partner-variant-source-badge";
          badge.dataset.variantSourceBadge = "";
          badge.textContent = "Se define por variante";
          field.querySelector(".partner-draft-field__label-text")?.appendChild(badge);
        }
        if (badge) badge.hidden = !active;
        let note = field.querySelector("[data-family-field-note]");
        if (active && !note) {
          note = document.createElement("span");
          note.className = "partner-family-field-note";
          note.dataset.familyFieldNote = "";
          note.innerHTML = '<i data-lucide="layers-3" aria-hidden="true"></i><span><strong>Se define en cada variante</strong><small>Completa este dato al crear cada presentación.</small></span>';
          field.appendChild(note);
        }
        if (note) note.hidden = !active;
        const sourceControl = form.elements.namedItem(`attributes[${field.dataset.attrKey}]`);
        if (sourceControl && !(sourceControl instanceof RadioNodeList)) sourceControl.disabled = active;
      });
    }

    function syncDefaultTechnicalValues() {
      // V4: la principal solo decide la presentación pública inicial.
    }

    function closeFieldPicker() {
      pickerControl?.classList.remove("is-open");
      pickerButton?.setAttribute("aria-expanded", "false");
    }

    function syncFieldPicker() {
      const selected = picker?.selectedOptions?.[0];
      if (pickerLabel) pickerLabel.textContent = selected?.textContent?.trim() || "Selecciona un campo";
      pickerMenu?.querySelectorAll("[data-variant-axis-value]").forEach((option) => {
        option.setAttribute("aria-selected", String(option.dataset.variantAxisValue === picker?.value));
      });
      if (addFieldButton) addFieldButton.disabled = Boolean(picker?.disabled || !picker?.value);
    }

    function renderFieldBar() {
      selectedFields?.replaceChildren();
      state.fields.forEach((key) => {
        const field = definition(key) || { label: key };
        const chip = document.createElement("span");
        chip.className = "partner-variant-field-chip";
        chip.append(`${field.label}${field.unit ? ` (${field.unit})` : ""}`);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `Quitar ${field.label}`);
        remove.addEventListener("click", () => removeField(key));
        chip.appendChild(remove);
        selectedFields?.appendChild(chip);
      });
      if (picker) {
        const choices = allowedFields().filter((field) => !state.fields.includes(field.key));
        picker.replaceChildren(new Option("Selecciona un campo", ""));
        choices.forEach((field) => picker.add(new Option(`${field.label}${field.unit ? ` (${field.unit})` : ""}`, field.key)));
        picker.disabled = state.fields.length >= maxAxes || !choices.length;
        picker.value = "";
        if (pickerButton) pickerButton.disabled = picker.disabled;
        pickerMenu?.replaceChildren();
        choices.forEach((field) => {
          const option = document.createElement("button");
          option.type = "button";
          option.className = "partner-select__option";
          option.dataset.variantAxisValue = field.key;
          option.setAttribute("role", "option");
          option.setAttribute("aria-selected", "false");
          option.textContent = `${field.label}${field.unit ? ` (${field.unit})` : ""}`;
          option.addEventListener("click", () => {
            picker.value = field.key;
            picker.dispatchEvent(new Event("change", { bubbles: true }));
            closeFieldPicker();
            pickerButton?.focus({ preventScroll: true });
          });
          pickerMenu?.appendChild(option);
        });
      }
      closeFieldPicker();
      syncFieldPicker();
      renderTechnicalBadges();
    }

    async function removeField(key) {
      const visual = Boolean(definition(key)?.is_visual);
      const galleries = visual
        ? [...rootEl.querySelectorAll(`[data-draft-gallery][data-variant-axis-key="${CSS.escape(key)}"]`)]
        : [];
      const imageCount = galleries.reduce((total, gallery) => total + Number(gallery.dataset.count || 0), 0);
      const warning = imageCount
        ? `Quitar este campo borrará permanentemente ${imageCount} imágenes. Esta acción no se puede deshacer. ¿Continuar?`
        : "Este campo se quitará de todas las variantes. ¿Continuar?";
      if (state.variants.length && !window.confirm(warning)) return;
      const nextFields = state.fields.filter((fieldKey) => fieldKey !== key);
      const keys = state.variants.map((row) => rowKey(row.options, nextFields));
      if (new Set(keys).size !== keys.length) {
        showMessage("No puedes quitar ese campo porque produciría variantes duplicadas.", "error");
        return;
      }
      let latestPayload = null;
      for (const gallery of galleries.filter((item) => Number(item.dataset.count || 0) > 0)) {
        const body = new FormData();
        body.append("csrf_token", gallery.dataset.csrfToken || "");
        body.append("variant_axis_key", key);
        body.append("variant_value_key", gallery.dataset.variantValueKey || "");
        const response = await fetch(section.dataset.deleteColorMediaUrl, {
          method: "POST", headers: { Accept: "application/json" }, body, credentials: "same-origin",
        });
        latestPayload = await response.json().catch(() => null);
        if (!response.ok || !latestPayload?.ok) {
          showMessage(Object.values(latestPayload?.errors || {})[0] || "No se pudieron eliminar las imágenes.", "error");
          return;
        }
      }
      const currentGallery = rootEl.querySelector("[data-draft-gallery]");
      if (latestPayload?.gallery_html && currentGallery) replaceGallery(currentGallery, latestPayload.gallery_html);
      state.fields = nextFields;
      state.variants.forEach((row) => delete row.options[key]);
      cancelEditing(); renderAll(); notifyChange();
    }

    function renderEditor(seedRow = null) {
      editorOptions?.replaceChildren();
      state.fields.forEach((key) => {
        const field = definition(key) || { label: key, suggestions: [] };
        const control = document.createElement("label");
        control.className = "partner-manual-option";
        const title = document.createElement("span");
        title.className = "partner-manual-option__label";
        title.textContent = `${field.label}${field.unit ? ` (${field.unit})` : ""}`;
        const inputRow = document.createElement("span");
        inputRow.className = "partner-manual-option__input";
        let color = null;
        if (field.is_visual) {
          color = document.createElement("input"); color.type = "color"; color.dataset.manualOptionSwatch = key;
          color.value = seedRow?.options[key]?.swatch || "#111827"; color.setAttribute("aria-label", `Muestra de ${field.label}`);
          inputRow.appendChild(color);
        }
        const input = document.createElement("input");
        input.type = ["integer", "decimal"].includes(field.value_type) ? "number" : "text";
        input.step = field.value_type === "integer" ? "1" : field.value_type === "decimal" ? "0.01" : "";
        input.dataset.manualOptionInput = key;
        input.value = seedRow?.options[key]?.label || "";
        input.placeholder = `Selecciona o escribe ${field.label.toLowerCase()}`;
        inputRow.appendChild(input);
        const suggestions = document.createElement("span");
        suggestions.className = "partner-manual-option__suggestions";
        const choices = [...new Set([...(field.suggestions || []), ...distinctValues(key).map((item) => item.label)])];
        choices.forEach((choice) => {
          const button = document.createElement("button"); button.type = "button";
          button.className = "partner-attribute-chip"; button.textContent = `${choice}${field.unit ? ` ${field.unit}` : ""}`;
          button.addEventListener("click", () => {
            input.value = choice;
            suggestions.querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
          });
          suggestions.appendChild(button);
        });
        control.append(title, inputRow, suggestions); editorOptions?.appendChild(control);
      });
      if (editorPrice) editorPrice.value = seedRow?.price || "";
      if (editorComparePrice) editorComparePrice.value = seedRow?.compareAtPrice || "";
      if (editorStock) editorStock.value = seedRow?.stock || "";
    }

    function collectEditorOptions() {
      const result = {};
      let complete = true;
      state.fields.forEach((key) => {
        const input = editorOptions?.querySelector(`[data-manual-option-input="${CSS.escape(key)}"]`);
        const label = String(input?.value || "").trim();
        if (!label) complete = false;
        result[key] = {
          label,
          swatch: editorOptions?.querySelector(`[data-manual-option-swatch="${CSS.escape(key)}"]`)?.value || null,
        };
      });
      return complete ? result : null;
    }

    function openDrawer(seedRow = null, title = "Nueva variante") {
      if (editor) editor.hidden = false;
      if (drawerBackdrop) drawerBackdrop.hidden = false;
      document.body.classList.add("partner-variant-drawer-open");
      if (editorTitle) editorTitle.textContent = title;
      if (batchEditor) batchEditor.hidden = true;
      if (editorOptions) editorOptions.hidden = false;
      const commercial = editor?.querySelector(".partner-variant-editor__commercial");
      commercial?.removeAttribute("hidden");
      commercial?.querySelectorAll("label").forEach((label) => { label.hidden = false; });
      renderEditor(seedRow);
      window.setTimeout(() => editorOptions?.querySelector("input")?.focus(), 0);
    }

    function closeDrawer() {
      if (editor) editor.hidden = true;
      if (drawerBackdrop) drawerBackdrop.hidden = true;
      document.body.classList.remove("partner-variant-drawer-open");
      state.pendingField = null;
    }

    function captureSourceSnapshot() {
      if (state.sourceSnapshot && Object.keys(state.sourceSnapshot).length) return;
      state.sourceSnapshot = {
        attributes: Object.fromEntries(
          allowedFields().map((field) => [field.source_field || field.key, technicalValue(field.source_field || field.key)]),
        ),
        price: String(form.elements.namedItem("price")?.value || ""),
        compare_at_price: String(form.elements.namedItem("compare_at_price")?.value || ""),
        stock: String(form.elements.namedItem("stock_quantity")?.value || ""),
      };
    }

    function moveGalleryForMode() {
      const collection = rootEl.querySelector("[data-gallery-collection]");
      if (!collection) return;
      const destination = toggle?.checked ? variantGalleryHost : generalGalleryHost;
      if (destination && collection.parentElement !== destination) destination.appendChild(collection);
    }

    function openBatchField(key) {
      const field = definition(key);
      if (!field || !batchEditor) return;
      state.pendingField = key;
      batchEditor.replaceChildren();
      const intro = document.createElement("p");
      intro.textContent = `Asigna ${field.label} a cada variante antes de agregar el campo.`;
      batchEditor.appendChild(intro);
      state.variants.forEach((row) => {
        const label = document.createElement("label");
        const name = document.createElement("span"); name.textContent = rowName(row);
        const input = document.createElement("input"); input.dataset.batchVariantId = row.variantId; input.placeholder = field.label;
        label.append(name, input); batchEditor.appendChild(label);
      });
      if (editorOptions) editorOptions.hidden = true;
      batchEditor.hidden = false;
      editor?.querySelectorAll(".partner-variant-editor__commercial label").forEach((label) => { label.hidden = true; });
      if (saveVariantButton) saveVariantButton.textContent = "Confirmar campo";
      openDrawer(null, `Agregar ${field.label}`);
      if (editorOptions) editorOptions.hidden = true;
      batchEditor.hidden = false;
      editor?.querySelectorAll(".partner-variant-editor__commercial label").forEach((label) => { label.hidden = true; });
    }

    function saveBatchField() {
      const key = state.pendingField;
      if (!key) return false;
      const inputs = [...batchEditor.querySelectorAll("[data-batch-variant-id]")];
      if (inputs.some((input) => !String(input.value || "").trim())) {
        showMessage("Completa el nuevo campo en todas las variantes.", "error");
        return true;
      }
      state.fields.push(key);
      inputs.forEach((input) => {
        const row = state.variants.find((item) => item.variantId === input.dataset.batchVariantId);
        if (row) row.options[key] = { label: String(input.value).trim(), swatch: null };
      });
      state.pendingField = null;
      closeDrawer(); renderAll(); notifyChange();
      return true;
    }

    async function saveManualVariant() {
      if (state.pendingField) return saveBatchField();
      if (!state.fields.length) return showMessage("Agrega al menos un campo de variante.", "error");
      const options = collectEditorOptions();
      if (!options) return showMessage("Selecciona un valor para cada campo.", "error");
      const rawPriceNumber = Number(editorPrice?.value || "");
      const priceNumber = roundMoney(rawPriceNumber);
      const rawCompareNumber = String(editorComparePrice?.value || "").trim()
        ? Number(editorComparePrice.value) : null;
      const compareNumber = rawCompareNumber === null ? null : roundMoney(rawCompareNumber);
      const stockNumber = Number(editorStock?.value || "");
      if (!Number.isFinite(rawPriceNumber) || !Number.isFinite(priceNumber) || priceNumber <= Number(commissionPolicy.minimum_price || 0.25)) return showMessage(commissionPolicy.minimum_price_message || "El precio debe ser mayor a USD 0.25.", "error");
      const estimate = commissionEstimate(priceNumber);
      if (estimate?.mode === "MISSING") return showMessage(estimate.label, "error");
      if (compareNumber !== null && (!Number.isFinite(rawCompareNumber) || !Number.isFinite(compareNumber) || compareNumber <= priceNumber)) return showMessage("El precio anterior debe ser mayor al precio actual.", "error");
      if (!Number.isInteger(stockNumber) || stockNumber < 0) return showMessage("El stock debe ser un entero no negativo.", "error");
      const duplicate = state.variants.find((row) => row.variantId !== state.editingId && rowKey(row.options) === rowKey(options));
      if (duplicate) return showMessage(`La variante ${rowName(duplicate)} ya existe.`, "error");
      const excessiveField = state.fields.find((key) => {
        const current = new Set(
          state.variants
            .filter((row) => row.variantId !== state.editingId)
            .map((row) => slug(row.options[key]?.label))
            .filter(Boolean),
        );
        current.add(slug(options[key]?.label));
        return current.size > maxValues;
      });
      if (excessiveField) {
        return showMessage(`${definition(excessiveField)?.label || excessiveField} admite hasta ${maxValues} valores distintos.`, "error");
      }
      if (!state.editingId && state.variants.length >= maxVariants) return showMessage(`Puedes crear hasta ${maxVariants} variantes.`, "error");
      if (state.editingId) {
        const row = state.variants.find((variant) => variant.variantId === state.editingId);
        const visualField = state.fields.find((key) => definition(key)?.is_visual);
        const oldVisual = visualField ? slug(row?.options[visualField]?.label) : "";
        const newVisual = visualField ? slug(options[visualField]?.label) : "";
        const oldStillUsed = visualField && state.variants.some(
          (item) => item.variantId !== state.editingId && slug(item.options[visualField]?.label) === oldVisual,
        );
        const oldGallery = oldVisual
          ? rootEl.querySelector(`[data-draft-gallery][data-variant-value-key="${CSS.escape(oldVisual)}"]`)
          : null;
        const oldImageCount = Number(oldGallery?.dataset.count || 0);
        if (oldVisual && oldVisual !== newVisual && !oldStillUsed && oldImageCount) {
          if (!window.confirm(`Cambiar este color borrará permanentemente ${oldImageCount} imagen${oldImageCount === 1 ? "" : "es"}. ¿Continuar?`)) return;
          const body = new FormData();
          body.append("csrf_token", oldGallery.dataset.csrfToken || "");
          body.append("variant_axis_key", visualField);
          body.append("variant_value_key", oldVisual);
          const response = await fetch(section.dataset.deleteColorMediaUrl, {
            method: "POST", headers: { Accept: "application/json" }, body, credentials: "same-origin",
          });
          const payload = await response.json().catch(() => null);
          if (!response.ok || !payload?.ok) return showMessage("No se pudieron eliminar las imágenes del color anterior.", "error");
          replaceGallery(oldGallery, payload.gallery_html);
        }
        if (row) {
          row.options = options;
          row.price = priceNumber.toFixed(2);
          row.compareAtPrice = compareNumber === null ? "" : compareNumber.toFixed(2);
          row.stock = editorStock?.value || "";
        }
      } else {
        const row = {
          variantId: newId(), previousCombinationKey: "", options, sku: "",
          price: priceNumber.toFixed(2), compareAtPrice: compareNumber === null ? "" : compareNumber.toFixed(2),
          stock: editorStock?.value || "", enabled: true,
        };
        state.variants.push(row);
        if (!state.defaultId) state.defaultId = row.variantId;
      }
      showMessage(state.editingId ? "Variante actualizada." : "Variante agregada.", "success");
      state.editingId = null; closeDrawer(); renderAll(); notifyChange();
    }

    function editVariant(id) {
      const row = state.variants.find((variant) => variant.variantId === id); if (!row) return;
      state.editingId = id;
      if (editorTitle) editorTitle.textContent = "Editar variante";
      if (saveVariantButton) saveVariantButton.textContent = "Guardar cambios";
      if (cancelEditButton) cancelEditButton.hidden = false;
      openDrawer(row, "Editar variante");
    }
    function duplicateVariant(id) {
      const row = state.variants.find((variant) => variant.variantId === id); if (!row) return;
      state.editingId = null;
      if (editorTitle) editorTitle.textContent = "Duplicar como nueva variante";
      if (saveVariantButton) saveVariantButton.textContent = "Agregar variante";
      if (cancelEditButton) cancelEditButton.hidden = false;
      openDrawer(row, "Duplicar como nueva variante"); showMessage("Cambia al menos un valor antes de agregarla.", "info");
    }
    function cancelEditing() {
      state.editingId = null;
      if (editorTitle) editorTitle.textContent = "Nueva variante";
      if (saveVariantButton) saveVariantButton.textContent = "Agregar variante";
      closeDrawer();
    }

    async function deleteVariant(row) {
      const visualField = state.fields.find((key) => definition(key)?.is_visual);
      const visualValue = visualField ? slug(row.options[visualField]?.label) : "";
      const isLastColorUse = visualField && !state.variants.some(
        (item) => item.variantId !== row.variantId && slug(item.options[visualField]?.label) === visualValue,
      );
      const gallery = visualValue
        ? rootEl.querySelector(`[data-draft-gallery][data-variant-value-key="${CSS.escape(visualValue)}"]`)
        : null;
      const imageCount = Number(gallery?.dataset.count || 0);
      const warning = isLastColorUse && imageCount
        ? `Eliminar ${rowName(row)} también borrará permanentemente ${imageCount} imagen${imageCount === 1 ? "" : "es"} de este color. Esta acción no se puede deshacer.`
        : `Eliminar ${rowName(row)}?`;
      if (!window.confirm(warning)) return;
      if (isLastColorUse && imageCount && gallery) {
        const body = new FormData();
        body.append("csrf_token", gallery.dataset.csrfToken || "");
        body.append("variant_axis_key", visualField);
        body.append("variant_value_key", visualValue);
        const response = await fetch(section.dataset.deleteColorMediaUrl, {
          method: "POST", headers: { Accept: "application/json" }, body, credentials: "same-origin",
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload?.ok) {
          showMessage(Object.values(payload?.errors || {})[0] || "No se pudieron eliminar las imágenes.", "error");
          return;
        }
        replaceGallery(gallery, payload.gallery_html);
      }
      state.variants = state.variants.filter((item) => item.variantId !== row.variantId);
      if (state.defaultId === row.variantId) state.defaultId = state.variants.find((item) => item.enabled)?.variantId || "";
      cancelEditing(); renderAll(); notifyChange();
    }

    function renderRows() {
      rowsContainer?.replaceChildren();
      state.variants.forEach((row) => {
        const element = document.createElement("div"); element.className = "partner-variant-row"; element.dataset.variantRow = "";
        const hiddenId = document.createElement("input"); hiddenId.type = "hidden"; hiddenId.name = "variant_id[]"; hiddenId.value = row.variantId;
        const hiddenOptions = document.createElement("input"); hiddenOptions.type = "hidden"; hiddenOptions.name = "variant_options[]"; hiddenOptions.value = JSON.stringify(row.options);
        const hiddenKey = document.createElement("input"); hiddenKey.type = "hidden"; hiddenKey.name = "variant_combination_key[]"; hiddenKey.value = row.previousCombinationKey || rowKey(row.options);
        const enabledCell = document.createElement("span"); enabledCell.className = "partner-variant-row__status";
        const hiddenEnabled = document.createElement("input"); hiddenEnabled.type = "hidden"; hiddenEnabled.name = "variant_enabled[]"; hiddenEnabled.value = row.enabled ? "1" : "0"; hiddenEnabled.dataset.variantEnabledValue = "";
        const enabled = document.createElement("input"); enabled.type = "checkbox"; enabled.checked = row.enabled; enabled.dataset.variantEnabled = ""; enabled.setAttribute("aria-label", "Activar variante");
        enabled.addEventListener("change", () => {
          row.enabled = enabled.checked;
          hiddenEnabled.value = row.enabled ? "1" : "0";
          if (row.enabled && !state.defaultId) state.defaultId = row.variantId;
          if (!row.enabled && state.defaultId === row.variantId) {
            state.defaultId = state.variants.find((item) => item.enabled)?.variantId || "";
          }
          renderRows(); syncDefaultTechnicalValues(); notifyChange();
        });
        const enabledLabel = document.createElement("label"); enabledLabel.className = "partner-variant-row__enabled";
        const enabledTrack = document.createElement("span"); enabledTrack.className = "partner-variant-row__enabled-track"; enabledTrack.setAttribute("aria-hidden", "true");
        const enabledText = document.createElement("span"); enabledText.textContent = row.enabled ? "Activa" : "Inactiva";
        enabledLabel.append(enabled, enabledTrack, enabledText);
        const radio = document.createElement("input"); radio.type = "radio"; radio.name = "variant_default_choice"; radio.value = row.variantId; radio.checked = state.defaultId === row.variantId; radio.disabled = !row.enabled; radio.setAttribute("aria-label", "Variante principal");
        radio.addEventListener("change", () => { state.defaultId = row.variantId; syncDefaultTechnicalValues(); syncConfiguration(); renderRows(); notifyChange(); });
        const defaultLabel = document.createElement("label"); defaultLabel.className = "partner-variant-row__default"; defaultLabel.classList.toggle("is-selected", radio.checked); defaultLabel.title = radio.checked ? "Variante principal" : "Marcar como principal";
        const defaultIcon = document.createElement("i"); defaultIcon.dataset.lucide = "star"; defaultIcon.setAttribute("aria-hidden", "true");
        const defaultText = document.createElement("span"); defaultText.textContent = radio.checked ? "Principal" : "";
        defaultLabel.append(radio, defaultIcon, defaultText);
        enabledCell.append(hiddenEnabled, enabledLabel, defaultLabel);
        const identity = document.createElement("span"); identity.className = "partner-variant-row__identity";
        const strong = document.createElement("strong");
        strong.textContent = rowName(row);
        const visualField = state.fields.find((key) => definition(key)?.is_visual);
        if (visualField) {
          const swatch = document.createElement("span"); swatch.className = "partner-variant-row__swatch";
          swatch.style.backgroundColor = row.options[visualField]?.swatch || "#cbd5e1";
          identity.appendChild(swatch);
        }
        identity.appendChild(strong);
        const skuCell = document.createElement("code"); skuCell.className = "partner-variant-row__sku"; skuCell.textContent = row.sku || "SKU al guardar";
        const price = document.createElement("input"); price.name = "variant_price[]"; price.value = row.price; price.inputMode = "decimal"; price.placeholder = "0.00";
        price.addEventListener("input", () => { row.price = price.value; renderCommercialSummary(); renderRowCommission(); });
        const comparePrice = document.createElement("input"); comparePrice.name = "variant_compare_at_price[]"; comparePrice.value = row.compareAtPrice || ""; comparePrice.inputMode = "decimal"; comparePrice.placeholder = "Opcional";
        comparePrice.addEventListener("input", () => { row.compareAtPrice = comparePrice.value; });
        const stock = document.createElement("input"); stock.name = "variant_stock[]"; stock.value = row.stock; stock.inputMode = "numeric"; stock.placeholder = "0";
        const stockCell = document.createElement("span"); stockCell.className = "partner-variant-row__stock";
        const stockState = document.createElement("small"); stockState.textContent = Number(row.stock) > 0 ? "En stock" : "Agotada"; stockState.classList.toggle("is-empty", Number(row.stock) <= 0);
        stock.addEventListener("input", () => {
          row.stock = stock.value;
          const hasStock = Number(row.stock) > 0;
          stockState.textContent = hasStock ? "En stock" : "Agotada";
          stockState.classList.toggle("is-empty", !hasStock);
          renderCommercialSummary();
        });
        stockCell.append(stock, stockState);
        const priceCell = document.createElement("span"); priceCell.className = "partner-variant-row__price";
        const priceLabel = document.createElement("label"); priceLabel.title = "Precio actual"; priceLabel.append(price);
        const compareLabel = document.createElement("label"); compareLabel.title = "Precio anterior"; compareLabel.append(comparePrice);
        priceCell.append(priceLabel, compareLabel);
        const commissionCell = document.createElement("span"); commissionCell.className = "partner-variant-row__commission";
        const netCell = document.createElement("strong"); netCell.className = "partner-variant-row__net";
        const renderRowCommission = () => {
          const estimate = commissionEstimate(row.price);
          commissionCell.textContent = estimate && estimate.mode !== "MISSING" ? estimate.label : (estimate ? "Sin regla" : "Pendiente");
          netCell.textContent = estimate && estimate.mode !== "MISSING" ? `$${estimate.net.toFixed(2)}` : "—";
        };
        renderRowCommission();
        const mediaState = document.createElement("span"); mediaState.className = "partner-variant-row__media";
        const visualValue = visualField ? slug(row.options[visualField]?.label) : "";
        const imageCount = visualValue
          ? Number(rootEl.querySelector(`[data-draft-gallery][data-variant-value-key="${CSS.escape(visualValue)}"]`)?.dataset.count || 0)
          : 0;
        const mediaIcon = document.createElement("i"); mediaIcon.dataset.lucide = imageCount > 0 || !visualField ? "image-check" : "image-off"; mediaIcon.setAttribute("aria-hidden", "true");
        const mediaText = document.createElement("span"); mediaText.textContent = visualField ? `${imageCount} foto${imageCount === 1 ? "" : "s"}` : "General";
        mediaState.append(mediaIcon, mediaText);
        mediaState.classList.toggle("is-complete", !visualField || imageCount > 0);
        const actions = document.createElement("span"); actions.className = "partner-variant-row__actions";
        const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Editar"; edit.addEventListener("click", () => editVariant(row.variantId));
        const duplicate = document.createElement("button"); duplicate.type = "button"; duplicate.setAttribute("aria-label", "Duplicar variante"); duplicate.title = "Duplicar"; duplicate.innerHTML = '<i data-lucide="copy" aria-hidden="true"></i>'; duplicate.addEventListener("click", () => duplicateVariant(row.variantId));
        const remove = document.createElement("button"); remove.type = "button"; remove.className = "is-danger"; remove.setAttribute("aria-label", "Eliminar variante"); remove.innerHTML = '<i data-lucide="trash-2" aria-hidden="true"></i>';
        remove.addEventListener("click", () => deleteVariant(row));
        actions.append(edit, duplicate, remove);
        element.append(hiddenId, hiddenOptions, hiddenKey, enabledCell, identity, skuCell, stockCell, priceCell, commissionCell, netCell, mediaState, actions);
        rowsContainer?.appendChild(element);
      });
      if (table) table.hidden = !state.variants.length;
      if (countLabel) countLabel.textContent = `${state.variants.length} variante${state.variants.length === 1 ? "" : "s"}`;
      renderCommercialSummary();
      refreshIcons();
    }

    function renderCommercialSummary() {
      const enabledRows = state.variants.filter((row) => row.enabled);
      const prices = enabledRows.map((row) => Number(row.price)).filter((value) => Number.isFinite(value) && value > 0);
      const minPrice = prices.length ? Math.min(...prices) : null;
      const maxPrice = prices.length ? Math.max(...prices) : null;
      const range = minPrice === null
        ? "Sin precios"
        : `$${minPrice.toFixed(2)}${minPrice === maxPrice ? "" : ` – $${maxPrice.toFixed(2)}`}`;
      const stock = enabledRows.reduce((total, row) => total + Math.max(0, Number(row.stock) || 0), 0);
      commercialSummary?.querySelector("[data-family-active-count]")?.replaceChildren(String(enabledRows.length));
      commercialSummary?.querySelector("[data-family-price-range]")?.replaceChildren(range);
      commercialSummary?.querySelector("[data-family-stock-total]")?.replaceChildren(String(stock));
      commercialSummary?.querySelector("[data-family-sold-out]")?.replaceChildren(String(enabledRows.filter((row) => Number(row.stock) === 0).length));
      if (commercialSummary) commercialSummary.hidden = !toggle?.checked;
      if (singleCommercialFields) singleCommercialFields.hidden = Boolean(toggle?.checked);
    }

    function renderAll() {
      renderFieldBar(); renderRows(); cancelEditing(); syncConfiguration(); moveGalleryForMode();
    }

    toggle?.addEventListener("change", () => {
      if (toggle.checked) {
        captureSourceSnapshot();
        ensureDefaultFields();
        if (state.variants.length) {
          if (panel) panel.hidden = false;
          if (activation) activation.hidden = true;
          renderAll(); notifyChange();
        } else {
          state.activationPending = true;
          if (panel) panel.hidden = true;
          if (activation) activation.hidden = false;
          renderTechnicalBadges(); renderCommercialSummary(); moveGalleryForMode();
        }
      } else {
        const principal = state.variants.find((row) => row.variantId === state.defaultId);
        if (principal && !window.confirm("Las variantes se archivarÃ¡n durante este borrador y la principal se convertirÃ¡ en producto simple. Â¿Continuar?")) {
          toggle.checked = true; return;
        }
        if (principal) {
          state.fields.forEach((key) => {
            const source = definition(key)?.source_field || key;
            const input = form.elements.namedItem(`attributes[${source}]`);
            if (input && !(input instanceof RadioNodeList)) { input.disabled = false; input.value = principal.options[key]?.label || ""; }
          });
          const priceInput = form.elements.namedItem("price"); if (priceInput) priceInput.value = principal.price || "";
          const compareInput = form.elements.namedItem("compare_at_price"); if (compareInput) compareInput.value = principal.compareAtPrice || "";
          const stockInput = form.elements.namedItem("stock_quantity"); if (stockInput) stockInput.value = principal.stock || "";
        }
        if (panel) panel.hidden = true;
        if (activation) activation.hidden = true;
        renderAll(); notifyChange();
      }
    });
    pickerButton?.addEventListener("click", (event) => {
      event.preventDefault();
      if (pickerButton.disabled) return;
      const opening = !pickerControl?.classList.contains("is-open");
      closeFieldPicker();
      if (opening) {
        pickerControl?.classList.add("is-open");
        pickerButton.setAttribute("aria-expanded", "true");
        pickerMenu?.querySelector("[data-variant-axis-value]")?.focus({ preventScroll: true });
      }
    });
    pickerButton?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeFieldPicker();
      if (event.key === "ArrowDown" && !pickerButton.disabled) {
        event.preventDefault();
        pickerControl?.classList.add("is-open");
        pickerButton.setAttribute("aria-expanded", "true");
        pickerMenu?.querySelector("[data-variant-axis-value]")?.focus({ preventScroll: true });
      }
    });
    pickerMenu?.addEventListener("keydown", (event) => {
      const options = [...pickerMenu.querySelectorAll("[data-variant-axis-value]")];
      const current = options.indexOf(document.activeElement);
      if (event.key === "Escape") {
        event.preventDefault(); closeFieldPicker(); pickerButton?.focus({ preventScroll: true });
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        const next = Math.min(options.length - 1, Math.max(0, current + offset));
        options[next]?.focus({ preventScroll: true });
      }
    });
    picker?.addEventListener("change", syncFieldPicker);
    document.addEventListener("click", (event) => {
      if (pickerControl && !pickerControl.contains(event.target)) closeFieldPicker();
    });
    addFieldButton?.addEventListener("click", () => {
      const key = picker?.value || ""; const field = definition(key);
      if (!field || state.fields.length >= maxAxes || state.fields.includes(key)) return;
      if (state.variants.length) return openBatchField(key);
      state.fields.push(key); renderAll(); notifyChange();
    });
    openEditorButton?.addEventListener("click", () => { state.editingId = null; openDrawer(); });
    saveVariantButton?.addEventListener("click", saveManualVariant);
    cancelEditButton?.addEventListener("click", cancelEditing);
    drawerBackdrop?.addEventListener("click", cancelEditing);
    startEmptyButton?.addEventListener("click", () => {
      state.activationPending = false;
      if (activation) activation.hidden = true;
      if (panel) panel.hidden = false;
      renderAll(); notifyChange();
    });
    convertBaseButton?.addEventListener("click", () => {
      const snapshot = state.sourceSnapshot || {};
      const options = {};
      const missing = [];
      state.fields.forEach((key) => {
        const field = definition(key) || {};
        const label = String(snapshot.attributes?.[field.source_field || key] || "").trim();
        if (!label) missing.push(field.label || key);
        options[key] = { label, swatch: field.is_visual ? "#111827" : null };
      });
      const converted = {
        variantId: newId(), previousCombinationKey: "", options, sku: "",
        price: String(snapshot.price || ""), compareAtPrice: String(snapshot.compare_at_price || ""),
        stock: String(snapshot.stock || ""), enabled: true,
      };
      state.activationPending = false;
      if (convertBaseInput) convertBaseInput.value = "1";
      if (activation) activation.hidden = true;
      if (panel) panel.hidden = false;
      if (missing.length) {
        state.editingId = null;
        openDrawer(converted, "Completar primera variante");
        showMessage(`Completa: ${missing.join(", ")}.`, "warning");
        return;
      }
      state.variants.push(converted); state.defaultId = converted.variantId;
      renderAll(); notifyChange();
    });
    productTypeInput?.addEventListener("change", () => {
      const valid = new Set(allowedFields().map((field) => field.key));
      const invalid = state.fields.filter((key) => !valid.has(key));
      const invalidLabels = invalid.map((key) => definition(key)?.label || key).join(", ");
      if (invalid.length && !window.confirm(`El nuevo tipo no admite: ${invalidLabels}. Esos campos se eliminarán y las variantes duplicadas se reducirán conservando la principal o la más antigua. ¿Continuar?`)) {
        productTypeInput.value = previousProductType; return;
      }
      previousProductType = productType();
      invalid.forEach((key) => state.variants.forEach((row) => delete row.options[key]));
      state.fields = state.fields.filter((key) => valid.has(key));
      const uniqueRows = new Map();
      const principal = state.variants.find((row) => row.variantId === state.defaultId);
      [principal, ...state.variants].filter(Boolean).forEach((row) => {
        const key = rowKey(row.options, state.fields);
        if (!uniqueRows.has(key)) uniqueRows.set(key, row);
      });
      state.variants = [...uniqueRows.values()];
      if (!state.variants.some((row) => row.variantId === state.defaultId)) {
        state.defaultId = state.variants.find((row) => row.enabled)?.variantId || "";
      }
      ensureDefaultFields(); renderAll(); notifyChange();
    });
    form.addEventListener("formdata", syncConfiguration);
    document.addEventListener("ecuvel:gallery-updated", renderRows);
    if (toggle?.checked) captureSourceSnapshot();
    renderAll();
  }

  initializeManualVariantBuilder(root);

  function initializeChecklist() {
    const panel = document.querySelector("[data-checklist]");
    if (!panel || !form) return;

    const items = new Map();
    panel.querySelectorAll("[data-checklist-item]").forEach((li) => {
      items.set(li.dataset.checklistItem, li);
    });
    const percentEl = panel.querySelector("[data-checklist-percent]");
    const progressEl = panel.querySelector("[data-checklist-progress]");

    function fieldValue(name) {
      // form.elements evita colisiones con elementos de fuera del formulario
      // (p. ej. <meta name="description">) e incluye los inputs con form="...".
      const el = form.elements.namedItem(name);
      return String(el?.value || "").trim();
    }

    function parseNumber(raw) {
      const cleaned = raw.replace(/[$\s]/g, "").replace(",", ".");
      if (!cleaned) return null;
      const number = Number(cleaned);
      return Number.isFinite(number) ? number : null;
    }

    function setItem(key, complete, message) {
      const li = items.get(key);
      if (!li) return false;
      const changed = li.classList.contains("is-complete") !== complete;
      li.classList.toggle("is-complete", complete);
      if (message) {
        const small = li.querySelector("small");
        if (small) small.textContent = message;
      }
      if (changed) {
        const icon = li.querySelector("svg, i[data-lucide]");
        if (icon) {
          const holder = document.createElement("i");
          holder.dataset.lucide = complete ? "check-circle-2" : "circle";
          holder.setAttribute("aria-hidden", "true");
          icon.replaceWith(holder);
        }
      }
      return changed;
    }

    function attributesComplete() {
      let ok = true;
      form.querySelectorAll("[data-attr-required='1']").forEach((label) => {
        if (label.classList.contains("is-variant-source")) return;
        const wrapper = label.closest("[data-condition-field]");
        if (wrapper && wrapper.hidden) return;
        const field = label.querySelector("input, select, textarea");
        if (!field || field.type === "checkbox") return;
        if (!String(field.value || "").trim()) ok = false;
      });
      return ok;
    }

    function variantsComplete() {
      const toggle = document.querySelector("[data-variants-toggle]");
      if (!toggle?.checked) return true;
      const rows = Array.from(document.querySelectorAll("[data-variant-row]"));
      const activeRows = rows.filter((row) => row.querySelector("[data-variant-enabled]")?.checked !== false);
      if (!activeRows.length) return false;
      return activeRows.every((row) => {
        const key = String(row.querySelector("[name='variant_combination_key[]']")?.value || "").trim();
        const parsedPrice = parseNumber(String(row.querySelector("[name='variant_price[]']")?.value || "").trim());
        const price = parsedPrice === null ? null : roundMoney(parsedPrice);
        const compareRaw = String(row.querySelector("[name='variant_compare_at_price[]']")?.value || "").trim();
        const parsedCompare = compareRaw ? parseNumber(compareRaw) : null;
        const compare = parsedCompare === null ? null : roundMoney(parsedCompare);
        const stockRaw = String(row.querySelector("[name='variant_stock[]']")?.value || "").trim();
        const stock = /^\d+$/.test(stockRaw) ? Number(stockRaw) : null;
        const estimate = price === null ? null : commissionEstimate(price);
        return Boolean(key) && price !== null && price > Number(commissionPolicy.minimum_price || 0.25)
          && (compare === null || compare > price)
          && stock !== null && stock >= 0
          && estimate?.mode !== "MISSING";
      });
    }

    function evaluate() {
      let changed = false;
      changed = setItem("title", Boolean(fieldValue("title"))) || changed;

      const collection = document.querySelector("[data-gallery-collection]");
      const galleries = Array.from(collection?.querySelectorAll("[data-draft-gallery]") || []);
      const count = Number(collection?.dataset.totalCount || 0);
      const minImages = Number(collection?.dataset.minImages || 3);
      const assignedGalleries = galleries.filter((gallery) => gallery.dataset.variantValueKey);
      const unassigned = galleries.some((gallery) => !gallery.dataset.variantValueKey && Number(gallery.dataset.count || 0) > 0 && assignedGalleries.length);
      const groupsComplete = assignedGalleries.length
        ? assignedGalleries.every((gallery) => Number(gallery.dataset.count || 0) >= 1 && Number(gallery.dataset.count || 0) <= Number(gallery.dataset.maxImages || 6)) && !unassigned
        : galleries.every((gallery) => Number(gallery.dataset.count || 0) <= Number(gallery.dataset.maxImages || 6));
      changed = setItem("gallery", count >= minImages && groupsComplete,
        assignedGalleries.length ? `${count} imágenes distribuidas por color.` : `${count}/${minImages} imágenes mínimas.`) || changed;

      changed = setItem("description", fieldValue("description").length >= 20) || changed;
      changed = setItem("attributes", attributesComplete()) || changed;
      changed = setItem("variants", variantsComplete()) || changed;

      const parsedPrice = parseNumber(fieldValue("price"));
      const price = parsedPrice === null ? null : roundMoney(parsedPrice);
      const priceEstimate = price === null ? null : commissionEstimate(price);
      const familyEnabled = Boolean(document.querySelector("[data-variants-toggle]")?.checked);
      changed = setItem("price", familyEnabled ? variantsComplete() : price !== null
        && price > Number(commissionPolicy.minimum_price || 0.25)
        && priceEstimate?.mode !== "MISSING") || changed;

      const stockRaw = fieldValue("stock_quantity");
      const stock = /^\d+$/.test(stockRaw) ? Number(stockRaw) : null;
      changed = setItem("stock", familyEnabled ? variantsComplete() : stock !== null && stock >= 0) || changed;

      const weight = fieldValue("product_weight_value") || fieldValue("product_weight_kg");
      changed = setItem("dimensions", Boolean(weight)) || changed;

      let requiredTotal = 0;
      let requiredComplete = 0;
      items.forEach((li) => {
        if (li.dataset.checklistOptional === "1") return;
        requiredTotal += 1;
        if (li.classList.contains("is-complete")) requiredComplete += 1;
      });
      const percent = requiredTotal ? Math.round((100 * requiredComplete) / requiredTotal) : 0;
      if (percentEl) percentEl.textContent = `${percent}%`;
      if (progressEl) progressEl.value = percent;

      if (changed) refreshIcons();
    }

    document.addEventListener("input", evaluate);
    document.addEventListener("change", evaluate);
    refreshChecklist = evaluate;
    evaluate();
  }

  initializeChecklist();

  function initializeAutosave() {
    const saveUrl = form?.dataset.autosaveUrl;
    if (!saveUrl) return;

    const indicator = document.querySelector("[data-autosave-status]");
    let timer = null;
    let saving = false;

    function setStatus(text, kind = "info") {
      if (!indicator) return;
      indicator.textContent = text || "";
      indicator.dataset.kind = kind;
    }

    async function autosave(_force = false) {
      if (submitting) return false;
      if (saving) return false;
      if (!dirty) return true;
      saving = true;
      const savedVersion = changeVersion;
      setStatus("Guardando…");
      try {
        const response = await fetch(saveUrl, {
          method: "POST",
          headers: { Accept: "application/json" },
          body: new FormData(form),
          credentials: "same-origin",
        });
        const isJson = (response.headers.get("content-type") || "").includes("application/json");
        if (response.ok && isJson) {
          const payload = await response.json();
          const currentGallery = document.querySelector("[data-draft-gallery]");
          // Durante una carga se conserva el nodo actual hasta que la propia
          // respuesta del upload reemplace la galería; así no quedan referencias obsoletas.
          if (payload.gallery_html && currentGallery && !_force) {
            replaceGallery(currentGallery, payload.gallery_html);
          }
          const conversionFlag = form.querySelector("[data-variant-convert-base]");
          if (conversionFlag) conversionFlag.value = "0";
          if (changeVersion === savedVersion) dirty = false;
          const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
          setStatus(`Guardado automáticamente (${time})`, "success");
          return true;
        } else if (response.ok && !isJson) {
          // Respuesta HTML: probablemente la sesión expiró y se siguió el redirect al login.
          setStatus("Sesión expirada: guarda manualmente para no perder cambios.", "error");
          return false;
        } else if (response.status === 422) {
          let detail = "hay campos por corregir.";
          try {
            const payload = await response.json();
            const messages = Object.values(payload.errors || {});
            if (messages.length) {
              detail = messages[0];
              if (messages.length > 1) detail += ` (+${messages.length - 1} más)`;
            }
          } catch (_parseError) {
            // Sin detalle: se mantiene el mensaje genérico.
          }
          setStatus(`Autoguardado pendiente: ${detail}`, "warning");
          return false;
        } else {
          setStatus("No se pudo autoguardar. Guarda manualmente.", "error");
          return false;
        }
      } catch (_error) {
        setStatus("Sin conexión: hay cambios sin guardar.", "error");
        return false;
      } finally {
        saving = false;
      }
    }
    autosaveNow = autosave;

    function schedule(event) {
      if (event.target.form !== form) return;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(autosave, 3000);
    }

    document.addEventListener("input", schedule);
    document.addEventListener("change", schedule);
    window.setInterval(autosave, 45000);
  }

  initializeAutosave();

  document.querySelectorAll(".partner-draft-upload--document input[type='file']").forEach((input) => {
    input.addEventListener("change", () => {
      const label = input.closest("label");
      const name = input.files?.[0]?.name;
      if (label && name) {
        const target = label.querySelector("span");
        if (target) target.textContent = name;
      }
    });
  });
})();
