(() => {
  const root = document.querySelector("[data-product-draft]");
  if (!root) return;

  const form = document.getElementById("partner-product-draft-form");
  let dirty = false;

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
    form.addEventListener("input", () => {
      dirty = true;
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
    const nextGallery = wrapper.querySelector("[data-draft-gallery]");
    if (!nextGallery) return gallery;
    gallery.replaceWith(nextGallery);
    initGallery(nextGallery);
    refreshIcons();
    return nextGallery;
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
    formData.append("csrf_token", gallery.dataset.csrfToken || "");
    formData.append("kind", "IMAGE");
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
    gallery.querySelectorAll("[data-gallery-delete-form], [data-gallery-cover-form]").forEach((actionForm) => {
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
      });
    }

    if (addRowBtn) {
      addRowBtn.addEventListener("click", () => {
        rowsContainer.appendChild(buildRow("", "", "", false));
        table.hidden = false;
      });
    }
  }

  initializeVariantBuilder(root);

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
