document.addEventListener("DOMContentLoaded", () => {
  const dialog = document.querySelector("[data-review-dialog]");
  const dialogBody = dialog?.querySelector("[data-review-dialog-body]");
  const closeButton = dialog?.querySelector("[data-review-dialog-close]");
  const liveRegion = document.querySelector("[data-flash-region]") || createLiveRegion();
  let activeTrigger = null;
  let returnFocusTo = null;
  let formIsDirty = false;
  let lastFocusedInside = null;

  document.querySelectorAll("[data-review-form]").forEach((form) => {
    setupReviewForm(form);
  });

  document.querySelectorAll(".js-review-modal-trigger").forEach((trigger) => {
    trigger.addEventListener("click", async (event) => {
      if (!dialog || !dialogBody || !window.HTMLDialogElement) {
        return;
      }
      event.preventDefault();
      activeTrigger = trigger;
      returnFocusTo = document.activeElement instanceof HTMLElement ? document.activeElement : trigger;
      await openReviewDialog(trigger);
    });
  });

  closeButton?.addEventListener("click", () => {
    requestCloseDialog();
  });

  dialog?.addEventListener("cancel", (event) => {
    if (!canCloseDirtyForm()) {
      event.preventDefault();
      return;
    }
    closeReviewDialog();
  });

  dialog?.addEventListener("close", () => {
    closeReviewDialog(false);
  });

  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) {
      requestCloseDialog();
    }
  });

  dialog?.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") {
      return;
    }
    trapDialogFocus(event);
  });

  async function openReviewDialog(trigger) {
    const reviewUrl = trigger.dataset.reviewUrl || trigger.href;
    dialogBody.classList.add("is-loading");
    dialogBody.classList.remove("is-error");
    dialogBody.textContent = "Cargando formulario…";
    document.body.classList.add("review-dialog-open");
    dialog.showModal();

    try {
      const response = await fetch(reviewUrl, {
        headers: {
          Accept: "text/html",
          "X-Requested-With": "fetch",
        },
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error("No se pudo cargar el formulario.");
      }
      dialogBody.classList.remove("is-loading");
      dialogBody.innerHTML = await response.text();
      const form = dialogBody.querySelector("[data-review-form]");
      if (!form) {
        throw new Error("El formulario no está disponible.");
      }
      setupReviewForm(form);
      window.lucide?.createIcons?.();
      const title = form.querySelector("h1[id]");
      if (title) {
        dialog.setAttribute("aria-labelledby", title.id);
      }
      focusFirstField(form);
    } catch (error) {
      dialogBody.classList.remove("is-loading");
      dialogBody.classList.add("is-error");
      dialogBody.textContent = "No pudimos abrir el formulario. Te llevaremos a la página completa.";
      setTimeout(() => {
        window.location.href = trigger.href;
      }, 450);
    }
  }

  function requestCloseDialog() {
    if (!dialog?.open) {
      return;
    }
    if (!canCloseDirtyForm()) {
      return;
    }
    dialog.close();
  }

  function closeReviewDialog(cleanBody = true) {
    document.body.classList.remove("review-dialog-open");
    formIsDirty = false;
    activeTrigger = null;
    lastFocusedInside = null;
    dialog?.setAttribute("aria-labelledby", "product-review-dialog-title");
    if (cleanBody && dialogBody) {
      dialogBody.replaceChildren();
      dialogBody.classList.remove("is-loading", "is-error");
    }
    if (returnFocusTo instanceof HTMLElement) {
      returnFocusTo.focus({ preventScroll: true });
    }
    returnFocusTo = null;
  }

  function canCloseDirtyForm() {
    if (!formIsDirty) {
      return true;
    }
    return window.confirm("Hay una reseña sin enviar. ¿Quieres cerrar el formulario?");
  }

  function trapDialogFocus(event) {
    if (!dialog?.open) {
      return;
    }
    const focusables = Array.from(
      dialog.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((element) => element instanceof HTMLElement && element.offsetParent !== null);

    if (focusables.length === 0) {
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function focusFirstField(container) {
    const target =
      container.querySelector("input[name='rating']") ||
      container.querySelector("textarea") ||
      container.querySelector("button");
    if (target instanceof HTMLElement) {
      target.focus({ preventScroll: true });
      lastFocusedInside = target;
    }
  }

  function setupReviewForm(form) {
    if (form.dataset.reviewReady === "1") {
      return;
    }
    form.dataset.reviewReady = "1";
    const fileInput = form.querySelector("[data-review-images]");
    const previewGrid = form.querySelector("[data-review-preview-grid]");
    const imageCount = form.querySelector("[data-review-image-count]");
    const errors = form.querySelector("[data-review-errors]");
    const body = form.querySelector("[data-review-body]");
    const charCount = form.querySelector("[data-review-character-count]");
    const submit = form.querySelector("[data-review-submit]");
    const maxFiles = Number(form.dataset.maxImages || 5);

    form.addEventListener("input", () => {
      formIsDirty = true;
      updateCharacterCount(body, charCount);
    });

    form.addEventListener("change", () => {
      formIsDirty = true;
    });

    body?.addEventListener("input", () => updateCharacterCount(body, charCount));
    updateCharacterCount(body, charCount);

    fileInput?.addEventListener("change", () => {
      hideFormError(errors);
      if (fileInput.files.length > maxFiles) {
        fileInput.value = "";
        renderImagePreviews(fileInput, previewGrid, imageCount, maxFiles);
        showFormError(errors, `Puedes subir como máximo ${maxFiles} fotos.`);
        return;
      }
      renderImagePreviews(fileInput, previewGrid, imageCount, maxFiles);
    });

    previewGrid?.addEventListener("click", (event) => {
      const removeButton = event.target.closest("[data-review-remove-image]");
      if (!removeButton || !fileInput) {
        return;
      }
      removeSelectedFile(fileInput, Number(removeButton.dataset.reviewRemoveImage));
      renderImagePreviews(fileInput, previewGrid, imageCount, maxFiles);
      formIsDirty = true;
    });

    form.addEventListener("submit", async (event) => {
      if (!dialog?.open || !dialogBody?.contains(form)) {
        disableSubmit(submit);
        return;
      }
      event.preventDefault();
      hideFormError(errors);
      disableSubmit(submit);

      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "fetch",
          },
          body: new FormData(form),
          credentials: "same-origin",
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
          throw new Error(data.message || "No pudimos guardar la reseña.");
        }
        formIsDirty = false;
        markTriggerAsPending(activeTrigger);
        announce(data.message || "Reseña enviada. Está pendiente de revisión.");
        dialog.close();
      } catch (error) {
        showFormError(errors, error.message || "No pudimos guardar la reseña.");
        enableSubmit(submit);
      }
    });
  }

  function updateCharacterCount(textarea, output) {
    if (!textarea || !output) {
      return;
    }
    output.textContent = String(textarea.value.length);
  }

  function renderImagePreviews(input, grid, output, maxFiles) {
    if (!grid) {
      return;
    }
    grid.replaceChildren();
    const files = Array.from(input.files || []).slice(0, maxFiles);
    if (output) {
      output.textContent = String(files.length);
    }
    grid.hidden = files.length === 0;
    files.forEach((file, index) => {
      const item = document.createElement("div");
      item.className = "review-upload__preview";
      const image = document.createElement("img");
      image.alt = file.name;
      image.src = URL.createObjectURL(file);
      image.addEventListener("load", () => URL.revokeObjectURL(image.src), { once: true });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "review-upload__remove";
      remove.dataset.reviewRemoveImage = String(index);
      remove.setAttribute("aria-label", `Quitar ${file.name}`);
      remove.textContent = "×";
      item.append(image, remove);
      grid.append(item);
    });
  }

  function removeSelectedFile(input, removeIndex) {
    const files = Array.from(input.files || []).filter((_, index) => index !== removeIndex);
    if (typeof DataTransfer === "undefined") {
      input.value = "";
      return;
    }
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
  }

  function showFormError(target, message) {
    if (!target) {
      announce(message);
      return;
    }
    target.textContent = message;
    target.hidden = false;
    target.focus?.();
  }

  function hideFormError(target) {
    if (!target) {
      return;
    }
    target.hidden = true;
    target.textContent = "";
  }

  function disableSubmit(submit) {
    if (!submit) {
      return;
    }
    submit.disabled = true;
    submit.setAttribute("aria-disabled", "true");
    submit.dataset.originalText ||= submit.textContent.trim();
    submit.textContent = "Enviando…";
  }

  function enableSubmit(submit) {
    if (!submit) {
      return;
    }
    submit.disabled = false;
    submit.removeAttribute("aria-disabled");
    submit.textContent = submit.dataset.originalText || "Enviar comentario";
  }

  function markTriggerAsPending(trigger) {
    if (!(trigger instanceof HTMLElement)) {
      return;
    }
    trigger.textContent = "Reseña en revisión";
    trigger.classList.remove("js-review-modal-trigger", "button--primary");
    trigger.classList.add("button--secondary", "is-disabled");
    trigger.removeAttribute("href");
    trigger.removeAttribute("data-review-url");
    trigger.setAttribute("aria-disabled", "true");
    trigger.addEventListener("click", (event) => event.preventDefault());
  }

  function announce(message) {
    liveRegion.textContent = "";
    window.setTimeout(() => {
      liveRegion.textContent = message;
    }, 20);
  }

  function createLiveRegion() {
    const region = document.createElement("div");
    region.className = "visually-hidden";
    region.setAttribute("aria-live", "polite");
    region.setAttribute("aria-atomic", "true");
    document.body.append(region);
    return region;
  }
});
