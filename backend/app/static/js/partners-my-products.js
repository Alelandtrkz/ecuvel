(() => {
  const filters = document.querySelector("[data-catalog-filters]");
  if (filters) {
    filters.querySelectorAll("[data-catalog-filter]").forEach((control) => {
      control.addEventListener("change", () => filters.requestSubmit());
    });
  }

  const catalogSelects = Array.from(document.querySelectorAll("[data-catalog-select]"));
  let openCatalogSelect = null;

  const catalogSelectOptions = (control) => Array.from(
    control.querySelectorAll(".partner-select__option"),
  );
  const closeCatalogSelect = (control, restoreFocus = false) => {
    if (!control) return;
    const button = control.querySelector("[data-catalog-select-button]");
    control.classList.remove("is-open");
    button?.setAttribute("aria-expanded", "false");
    if (openCatalogSelect === control) openCatalogSelect = null;
    if (restoreFocus) button?.focus({ preventScroll: true });
  };
  const focusCatalogOption = (options, index) => {
    const option = options[index];
    if (!option) return;
    option.focus({ preventScroll: true });
    option.scrollIntoView({ block: "nearest" });
  };
  const selectedCatalogOptionIndex = (options) => {
    const selected = options.findIndex((option) => option.getAttribute("aria-selected") === "true");
    return selected >= 0 ? selected : 0;
  };
  const openSelect = (control, preferredIndex) => {
    if (openCatalogSelect && openCatalogSelect !== control) closeCatalogSelect(openCatalogSelect);
    const button = control.querySelector("[data-catalog-select-button]");
    const options = catalogSelectOptions(control);
    control.classList.add("is-open");
    button?.setAttribute("aria-expanded", "true");
    openCatalogSelect = control;
    window.requestAnimationFrame(() => {
      focusCatalogOption(options, preferredIndex ?? selectedCatalogOptionIndex(options));
    });
  };

  catalogSelects.forEach((control, index) => {
    const nativeSelect = control.querySelector("[data-catalog-select-native]");
    const button = control.querySelector("[data-catalog-select-button]");
    const label = control.querySelector("[data-catalog-select-label]");
    const menu = control.querySelector("[data-catalog-select-menu]");
    const options = catalogSelectOptions(control);
    if (!nativeSelect || !button || !label || !menu || !options.length) return;

    menu.id ||= `partner-catalog-select-menu-${index}`;
    button.setAttribute("aria-controls", menu.id);
    control.classList.add("is-enhanced");

    const syncFromNative = () => {
      label.textContent = nativeSelect.selectedOptions?.[0]?.textContent?.trim() || "Seleccione una opción";
      options.forEach((option) => {
        option.setAttribute("aria-selected", String(option.dataset.value === nativeSelect.value));
      });
    };
    const chooseOption = (option) => {
      nativeSelect.value = option.dataset.value || "";
      syncFromNative();
      closeCatalogSelect(control);
      nativeSelect.dispatchEvent(new Event("change", { bubbles: true }));
    };

    button.addEventListener("click", () => {
      if (control.classList.contains("is-open")) closeCatalogSelect(control);
      else openSelect(control);
    });
    button.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const preferred = event.key === "ArrowUp"
          ? Math.max(0, options.length - 1)
          : selectedCatalogOptionIndex(options);
        openSelect(control, preferred);
      } else if (event.key === "Escape") {
        closeCatalogSelect(control);
      }
    });

    options.forEach((option, optionIndex) => {
      option.addEventListener("click", () => chooseOption(option));
      option.addEventListener("keydown", (event) => {
        let targetIndex = null;
        if (event.key === "ArrowDown") targetIndex = Math.min(options.length - 1, optionIndex + 1);
        if (event.key === "ArrowUp") targetIndex = Math.max(0, optionIndex - 1);
        if (event.key === "Home") targetIndex = 0;
        if (event.key === "End") targetIndex = options.length - 1;
        if (targetIndex !== null) {
          event.preventDefault();
          focusCatalogOption(options, targetIndex);
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          chooseOption(option);
        } else if (event.key === "Escape") {
          event.preventDefault();
          closeCatalogSelect(control, true);
        }
      });
    });

    nativeSelect.addEventListener("change", syncFromNative);
    syncFromNative();
  });

  document.addEventListener("click", (event) => {
    if (openCatalogSelect && !openCatalogSelect.contains(event.target)) {
      closeCatalogSelect(openCatalogSelect);
    }
  });

  const rowMenus = Array.from(document.querySelectorAll("[data-catalog-row-menu]"));
  const menuItems = (menu) => Array.from(
    menu.querySelectorAll('[role="menuitem"]:not([disabled])'),
  );
  const closeMenu = (menu, restoreFocus = false) => {
    const trigger = menu?.querySelector("[data-catalog-menu-trigger]");
    const panel = menu?.querySelector("[data-catalog-menu-panel]");
    if (!trigger || !panel || panel.hidden) return;
    panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger.focus();
  };
  const closeOtherMenus = (current) => {
    rowMenus.forEach((menu) => {
      if (menu !== current) closeMenu(menu);
    });
  };
  const positionMenu = (trigger, panel) => {
    const triggerRect = trigger.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const gutter = 8;
    const left = Math.min(
      window.innerWidth - panelRect.width - gutter,
      Math.max(gutter, triggerRect.right - panelRect.width),
    );
    const spaceBelow = window.innerHeight - triggerRect.bottom;
    const top = spaceBelow >= panelRect.height + gutter
      ? triggerRect.bottom + 6
      : Math.max(gutter, triggerRect.top - panelRect.height - 6);
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  };

  rowMenus.forEach((menu) => {
    const trigger = menu.querySelector("[data-catalog-menu-trigger]");
    const panel = menu.querySelector("[data-catalog-menu-panel]");
    if (!trigger || !panel) return;
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const willOpen = panel.hidden;
      closeOtherMenus(menu);
      panel.hidden = !willOpen;
      trigger.setAttribute("aria-expanded", String(willOpen));
      if (willOpen) {
        positionMenu(trigger, panel);
        menuItems(panel)[0]?.focus();
      }
    });
    panel.addEventListener("keydown", (event) => {
      const items = menuItems(panel);
      const currentIndex = items.indexOf(document.activeElement);
      let targetIndex = null;
      if (event.key === "ArrowDown") targetIndex = (currentIndex + 1) % items.length;
      if (event.key === "ArrowUp") targetIndex = (currentIndex - 1 + items.length) % items.length;
      if (event.key === "Home") targetIndex = 0;
      if (event.key === "End") targetIndex = items.length - 1;
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu(menu, true);
        return;
      }
      if (targetIndex !== null && items.length) {
        event.preventDefault();
        items[targetIndex].focus();
      }
    });
  });
  document.addEventListener("click", (event) => {
    rowMenus.forEach((menu) => {
      if (!menu.contains(event.target)) closeMenu(menu);
    });
  });
  window.addEventListener("resize", () => rowMenus.forEach((menu) => closeMenu(menu)));
  window.addEventListener("scroll", () => rowMenus.forEach((menu) => closeMenu(menu)), true);

  const selectAll = document.querySelector("[data-catalog-select-all]");
  const rowSelections = Array.from(document.querySelectorAll("[data-catalog-row-select]"));
  const selectableRows = rowSelections.filter((checkbox) => !checkbox.disabled);
  const selection = document.querySelector("[data-catalog-selection]");
  const selectedCount = document.querySelector("[data-catalog-selected-count]");
  const selectedLabel = document.querySelector("[data-catalog-selected-label]");
  const selectedDrafts = document.querySelector("[data-catalog-selected-drafts]");
  const selectedDraftsLabel = document.querySelector("[data-catalog-selected-drafts-label]");
  const bulkSubmitButton = document.querySelector("[data-catalog-bulk-submit]");
  const bulkDeleteButton = document.querySelector("[data-catalog-bulk-delete]");
  const bulkSubmitForm = document.querySelector("[data-catalog-bulk-submit-form]");

  const selectedDraftMap = () => {
    const drafts = new Map();
    selectableRows.filter((checkbox) => checkbox.checked).forEach((checkbox) => {
      if (!drafts.has(checkbox.dataset.draftId)) {
        drafts.set(checkbox.dataset.draftId, {
          id: checkbox.dataset.draftId,
          title: checkbox.dataset.draftTitle,
          variants: Number.parseInt(checkbox.dataset.variantCount || "0", 10),
          images: Number.parseInt(checkbox.dataset.imageCount || "0", 10),
          documents: Number.parseInt(checkbox.dataset.documentCount || "0", 10),
        });
      }
    });
    return drafts;
  };

  const appendDraftIds = (container, drafts) => {
    if (!container) return;
    container.replaceChildren();
    drafts.forEach((draft) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "draft_ids";
      input.value = draft.id;
      container.append(input);
    });
  };

  const updateSelection = () => {
    const count = selectableRows.filter((checkbox) => checkbox.checked).length;
    const drafts = selectedDraftMap();
    if (selectAll) {
      selectAll.disabled = selectableRows.length === 0;
      selectAll.checked = selectableRows.length > 0 && count === selectableRows.length;
      selectAll.indeterminate = count > 0 && count < selectableRows.length;
    }
    if (selection) selection.hidden = count === 0;
    if (selectedCount) selectedCount.textContent = String(count);
    if (selectedLabel) {
      selectedLabel.textContent = count === 1
        ? "presentación seleccionada"
        : "presentaciones seleccionadas";
    }
    if (selectedDrafts) selectedDrafts.textContent = String(drafts.size);
    if (selectedDraftsLabel) {
      selectedDraftsLabel.textContent = drafts.size === 1 ? "publicación" : "publicaciones";
    }
  };

  if (selectAll) {
    selectAll.addEventListener("change", () => {
      selectableRows.forEach((checkbox) => {
        checkbox.checked = selectAll.checked;
      });
      updateSelection();
    });
  }
  selectableRows.forEach((checkbox) => checkbox.addEventListener("change", updateSelection));

  bulkSubmitButton?.addEventListener("click", () => {
    const drafts = selectedDraftMap();
    if (!drafts.size || !bulkSubmitForm) return;
    appendDraftIds(bulkSubmitForm.querySelector("[data-catalog-form-ids]"), drafts);
    bulkSubmitButton.disabled = true;
    bulkSubmitForm.requestSubmit();
  });

  const deleteDialog = document.querySelector("[data-catalog-delete-dialog]");
  const deleteForm = deleteDialog?.querySelector("[data-catalog-delete-form]");
  const deleteIds = deleteDialog?.querySelector("[data-catalog-delete-ids]");
  const deleteMessage = deleteDialog?.querySelector("[data-catalog-delete-message]");
  const deleteDrafts = deleteDialog?.querySelector("[data-delete-drafts]");
  const deleteVariants = deleteDialog?.querySelector("[data-delete-variants]");
  const deleteImages = deleteDialog?.querySelector("[data-delete-images]");
  const deleteDocuments = deleteDialog?.querySelector("[data-delete-documents]");
  let deleteOrigin = null;

  const openDeleteDialog = ({ drafts, action, origin, includeIds }) => {
    if (!deleteDialog || !deleteForm || !drafts.size) return;
    deleteOrigin = origin;
    deleteForm.action = action;
    appendDraftIds(deleteIds, includeIds ? drafts : new Map());
    const records = Array.from(drafts.values());
    const total = (key) => records.reduce((sum, record) => sum + record[key], 0);
    if (deleteMessage) {
      deleteMessage.textContent = drafts.size === 1
        ? `Se eliminará “${records[0].title}” y todos sus datos. Esta acción no se puede deshacer.`
        : `Se eliminarán ${drafts.size} publicaciones y todos sus datos. Esta acción no se puede deshacer.`;
    }
    if (deleteDrafts) deleteDrafts.textContent = String(drafts.size);
    if (deleteVariants) deleteVariants.textContent = String(total("variants"));
    if (deleteImages) deleteImages.textContent = String(total("images"));
    if (deleteDocuments) deleteDocuments.textContent = String(total("documents"));
    deleteDialog.showModal();
  };

  document.querySelectorAll("[data-catalog-delete-single]").forEach((button) => {
    button.addEventListener("click", () => {
      closeMenu(button.closest("[data-catalog-row-menu]"));
      const record = {
        id: button.dataset.draftId,
        title: button.dataset.draftTitle,
        variants: Number.parseInt(button.dataset.variantCount || "0", 10),
        images: Number.parseInt(button.dataset.imageCount || "0", 10),
        documents: Number.parseInt(button.dataset.documentCount || "0", 10),
      };
      openDeleteDialog({
        drafts: new Map([[record.id, record]]),
        action: button.dataset.deleteUrl,
        origin: button,
        includeIds: false,
      });
    });
  });

  bulkDeleteButton?.addEventListener("click", () => {
    openDeleteDialog({
      drafts: selectedDraftMap(),
      action: bulkDeleteButton.dataset.deleteUrl,
      origin: bulkDeleteButton,
      includeIds: true,
    });
  });
  deleteDialog?.querySelector("[data-catalog-delete-cancel]")?.addEventListener("click", () => {
    deleteDialog.close();
  });
  deleteDialog?.addEventListener("click", (event) => {
    if (event.target === deleteDialog) deleteDialog.close();
  });
  deleteDialog?.addEventListener("close", () => deleteOrigin?.focus());
  deleteForm?.addEventListener("submit", () => {
    const submit = deleteForm.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
  });

  updateSelection();
})();
