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
      const original = button.textContent;
      try {
        await navigator.clipboard.writeText(value);
        button.textContent = "Copiado";
      } catch (_error) {
        window.prompt("Copia el código del producto:", value);
      } finally {
        window.setTimeout(() => {
          button.textContent = original;
        }, 1600);
      }
    });
  });

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
