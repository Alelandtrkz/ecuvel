(() => {
  const userPages = Array.from(document.querySelectorAll(".admin-users-page"));
  let openSelect = null;

  const closeSelect = (control, restoreFocus = false) => {
    if (!control) return;
    control.classList.remove("is-open");
    const trigger = control.querySelector(".admin-user-select__button");
    trigger?.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger?.focus({ preventScroll: true });
    if (openSelect === control) openSelect = null;
  };

  const focusSelectOption = (options, index) => {
    const option = options[index];
    if (!option) return;
    option.focus({ preventScroll: true });
    option.scrollIntoView({ block: "nearest" });
  };

  const enhanceSelect = (nativeSelect, index) => {
    if (nativeSelect.multiple || nativeSelect.closest(".admin-user-select")) return;

    const control = document.createElement("div");
    control.className = "admin-user-select is-enhanced";
    nativeSelect.before(control);
    control.append(nativeSelect);
    nativeSelect.classList.add("admin-user-select__native");

    const trigger = document.createElement("button");
    trigger.className = "admin-user-select__button";
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.disabled = nativeSelect.disabled;

    const selectedLabel = document.createElement("span");
    selectedLabel.className = "admin-user-select__label";
    const chevron = document.createElement("span");
    chevron.className = "admin-user-select__chevron";
    chevron.setAttribute("aria-hidden", "true");
    trigger.append(selectedLabel, chevron);

    const menu = document.createElement("div");
    menu.className = "admin-user-select__menu";
    menu.id = `admin-user-select-menu-${index}`;
    menu.setAttribute("role", "listbox");
    trigger.setAttribute("aria-controls", menu.id);
    const accessibleName = nativeSelect.getAttribute("aria-label");
    if (accessibleName) trigger.setAttribute("aria-label", accessibleName);

    const optionButtons = Array.from(nativeSelect.options).map((nativeOption) => {
      const option = document.createElement("button");
      option.className = "admin-user-select__option";
      option.type = "button";
      option.setAttribute("role", "option");
      option.dataset.value = nativeOption.value;
      option.textContent = nativeOption.textContent.trim();
      option.disabled = nativeOption.disabled;
      menu.append(option);
      return option;
    });

    control.append(trigger, menu);

    const enabledOptions = () => optionButtons.filter((option) => !option.disabled);
    const selectedEnabledIndex = (options) => {
      const selected = options.findIndex((option) => option.getAttribute("aria-selected") === "true");
      return selected >= 0 ? selected : 0;
    };

    const syncFromNative = () => {
      const selected = nativeSelect.selectedOptions?.[0];
      selectedLabel.textContent = selected?.textContent?.trim() || "Seleccione una opción";
      optionButtons.forEach((option) => {
        option.setAttribute("aria-selected", String(option.dataset.value === nativeSelect.value));
      });
      trigger.disabled = nativeSelect.disabled;
    };

    const open = (preferredIndex) => {
      if (openSelect && openSelect !== control) closeSelect(openSelect);
      control.classList.add("is-open");
      trigger.setAttribute("aria-expanded", "true");
      openSelect = control;
      const options = enabledOptions();
      window.requestAnimationFrame(() => {
        focusSelectOption(options, preferredIndex ?? selectedEnabledIndex(options));
      });
    };

    const choose = (option) => {
      nativeSelect.value = option.dataset.value ?? "";
      syncFromNative();
      nativeSelect.dispatchEvent(new Event("input", { bubbles: true }));
      nativeSelect.dispatchEvent(new Event("change", { bubbles: true }));
      closeSelect(control, true);
    };

    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      if (control.classList.contains("is-open")) closeSelect(control);
      else open();
    });

    trigger.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const options = enabledOptions();
        open(event.key === "ArrowUp" ? options.length - 1 : selectedEnabledIndex(options));
      } else if (event.key === "Escape") {
        closeSelect(control);
      }
    });

    optionButtons.forEach((option) => {
      option.addEventListener("click", (event) => {
        event.preventDefault();
        choose(option);
      });
      option.addEventListener("keydown", (event) => {
        const options = enabledOptions();
        const optionIndex = options.indexOf(option);
        if (event.key === "ArrowDown") {
          event.preventDefault();
          focusSelectOption(options, Math.min(options.length - 1, optionIndex + 1));
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          focusSelectOption(options, Math.max(0, optionIndex - 1));
        } else if (event.key === "Home") {
          event.preventDefault();
          focusSelectOption(options, 0);
        } else if (event.key === "End") {
          event.preventDefault();
          focusSelectOption(options, options.length - 1);
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose(option);
        } else if (event.key === "Escape") {
          event.preventDefault();
          closeSelect(control, true);
        }
      });
    });

    control.addEventListener("focusout", () => {
      window.setTimeout(() => {
        if (!control.contains(document.activeElement)) closeSelect(control);
      }, 0);
    });
    nativeSelect.addEventListener("change", syncFromNative);
    syncFromNative();
  };

  userPages.forEach((page) => {
    page.querySelectorAll("select").forEach((select, index) => enhanceSelect(select, index));
  });

  document.addEventListener("click", (event) => {
    if (openSelect && !openSelect.contains(event.target)) closeSelect(openSelect);
  });

  document.querySelectorAll("[data-confirm-open]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.dataset.confirmOpen)?.showModal());
  });
  document.querySelectorAll("[data-confirm-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });
  document.querySelectorAll(".admin-confirm-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  });

  const cedulaValid = (value) => {
    const digits = value.replace(/\D/g, "");
    if (digits.length !== 10) return false;
    const province = Number(digits.slice(0, 2));
    if (province < 1 || province > 24 || Number(digits[2]) > 5) return false;
    let total = 0;
    for (let index = 0; index < 9; index += 1) {
      let item = Number(digits[index]) * (index % 2 === 0 ? 2 : 1);
      total += item > 9 ? item - 9 : item;
    }
    return (10 - (total % 10)) % 10 === Number(digits[9]);
  };
  document.querySelectorAll("[data-identification-number]").forEach((input) => {
    const feedback = input.parentElement.querySelector("[data-identification-feedback]");
    const update = () => {
      const kind = input.form.elements.identification_type?.value;
      if (kind !== "ECUADOR_CEDULA" || !input.value) {
        feedback.textContent = "La validación comprueba formato, no identidad oficial.";
        feedback.classList.remove("is-valid", "is-invalid");
        return;
      }
      const valid = cedulaValid(input.value);
      feedback.textContent = valid ? "Formato matemático válido." : "La cédula no supera la validación matemática.";
      feedback.classList.toggle("is-valid", valid);
      feedback.classList.toggle("is-invalid", !valid);
    };
    input.addEventListener("input", update);
    input.form.elements.identification_type?.addEventListener("change", update);
    update();
  });

  document.querySelectorAll("[data-staff-form]").forEach((form) => {
    const role = form.querySelector("[data-staff-role]");
    const preview = form.querySelector("[data-permission-preview]");
    if (!role || !preview) return;
    const matrix = JSON.parse(form.dataset.rolePermissions || "{}");
    const labels = JSON.parse(form.dataset.permissionLabels || "{}");
    const update = () => {
      preview.replaceChildren(...(matrix[role.value] || []).map((permission) => {
        const item = document.createElement("li");
        item.textContent = labels[permission] || permission;
        return item;
      }));
    };
    role.addEventListener("change", update);
    update();
  });
})();
