(() => {
  const root = document.querySelector("[data-partner-reviews]");
  if (!root) return;

  const refreshIcons = () => {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  };

  const filterForm = root.querySelector("[data-review-filter-form]");
  filterForm?.querySelectorAll("[data-review-filter]").forEach((control) => {
    control.addEventListener("change", () => filterForm.requestSubmit());
  });

  const filterToggle = root.querySelector("[data-review-filter-toggle]");
  const filters = root.querySelector("[data-review-filters]");
  filterToggle?.addEventListener("click", () => {
    const expanded = filterToggle.getAttribute("aria-expanded") === "true";
    filterToggle.setAttribute("aria-expanded", String(!expanded));
    filters?.classList.toggle("is-open", !expanded);
  });

  const reviewSelects = Array.from(root.querySelectorAll("[data-review-select]"));
  let openSelect = null;
  const closeSelect = (control, restoreFocus = false) => {
    if (!control) return;
    const button = control.querySelector("[data-review-select-button]");
    control.classList.remove("is-open");
    button?.setAttribute("aria-expanded", "false");
    if (openSelect === control) openSelect = null;
    if (restoreFocus) button?.focus({ preventScroll: true });
  };
  const optionsFor = (control) => Array.from(control.querySelectorAll(".partner-select__option"));
  const selectedIndex = (options) => Math.max(0, options.findIndex((option) => option.getAttribute("aria-selected") === "true"));
  const focusOption = (options, index) => {
    options[index]?.focus({ preventScroll: true });
    options[index]?.scrollIntoView({ block: "nearest" });
  };

  reviewSelects.forEach((control, controlIndex) => {
    const nativeSelect = control.querySelector("[data-review-select-native]");
    const button = control.querySelector("[data-review-select-button]");
    const label = control.querySelector("[data-review-select-label]");
    const menu = control.querySelector("[data-review-select-menu]");
    const options = optionsFor(control);
    if (!nativeSelect || !button || !label || !menu || !options.length) return;

    menu.id ||= `partner-review-select-${controlIndex}`;
    button.setAttribute("aria-controls", menu.id);
    control.classList.add("is-enhanced");

    const sync = () => {
      label.textContent = nativeSelect.selectedOptions?.[0]?.textContent?.trim() || "Más recientes";
      options.forEach((option) => {
        option.setAttribute("aria-selected", String(option.dataset.value === nativeSelect.value));
      });
    };
    const open = (preferredIndex) => {
      if (openSelect && openSelect !== control) closeSelect(openSelect);
      control.classList.add("is-open");
      button.setAttribute("aria-expanded", "true");
      openSelect = control;
      window.requestAnimationFrame(() => focusOption(options, preferredIndex ?? selectedIndex(options)));
    };
    const choose = (option) => {
      nativeSelect.value = option.dataset.value || "newest";
      sync();
      closeSelect(control);
      nativeSelect.dispatchEvent(new Event("change", { bubbles: true }));
    };

    button.addEventListener("click", () => {
      if (control.classList.contains("is-open")) closeSelect(control);
      else open();
    });
    button.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        open(event.key === "ArrowUp" ? options.length - 1 : selectedIndex(options));
      } else if (event.key === "Escape") {
        closeSelect(control);
      }
    });
    options.forEach((option, optionIndex) => {
      option.addEventListener("click", () => choose(option));
      option.addEventListener("keydown", (event) => {
        let targetIndex = null;
        if (event.key === "ArrowDown") targetIndex = Math.min(options.length - 1, optionIndex + 1);
        if (event.key === "ArrowUp") targetIndex = Math.max(0, optionIndex - 1);
        if (event.key === "Home") targetIndex = 0;
        if (event.key === "End") targetIndex = options.length - 1;
        if (targetIndex !== null) {
          event.preventDefault();
          focusOption(options, targetIndex);
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose(option);
        } else if (event.key === "Escape") {
          event.preventDefault();
          closeSelect(control, true);
        }
      });
    });
    nativeSelect.addEventListener("change", () => {
      sync();
      nativeSelect.form?.requestSubmit();
    });
    sync();
  });

  document.addEventListener("click", (event) => {
    if (openSelect && !openSelect.contains(event.target)) closeSelect(openSelect);
  });

  const updateMetrics = (metrics) => {
    const rate = root.querySelector("[data-review-rate]");
    const answered = root.querySelector("[data-review-answered]");
    const unansweredCount = root.querySelector("[data-review-unanswered-count]");
    const answeredCount = root.querySelector("[data-review-answered-count]");
    const progress = root.querySelector("[data-review-rate-progress]");
    const bar = root.querySelector("[data-review-rate-bar]");
    if (rate) rate.textContent = String(metrics.response_rate);
    if (answered) answered.textContent = String(metrics.answered_reviews);
    if (unansweredCount) unansweredCount.textContent = String(metrics.unanswered_reviews);
    if (answeredCount) answeredCount.textContent = String(metrics.answered_reviews);
    progress?.setAttribute("aria-valuenow", String(metrics.response_rate));
    if (bar) bar.style.width = `${metrics.response_rate}%`;
  };

  root.querySelectorAll("[data-review-reply-form]").forEach((form) => {
    const card = form.closest("[data-review-card]");
    const input = form.querySelector("[data-review-reply-input]");
    const counter = form.querySelector("[data-review-character-count]");
    const error = form.querySelector("[data-review-reply-error]");
    const submit = form.querySelector("[data-review-submit]");
    const cancel = form.querySelector("[data-review-cancel]");
    const version = form.querySelector("[data-review-version]");
    const replyView = card?.querySelector("[data-review-reply-view]");
    const replyBody = card?.querySelector("[data-review-reply-body]");
    const replyDate = card?.querySelector("[data-review-reply-date]");
    const edit = card?.querySelector("[data-review-edit]");
    if (!card || !input || !counter || !error || !submit || !version || !replyView) return;

    form.dataset.savedBody = input.value;
    const updateCount = () => { counter.textContent = String(input.value.length); };
    input.addEventListener("input", updateCount);

    form.querySelectorAll("[data-review-quick-reply]").forEach((quickReply) => {
      quickReply.addEventListener("click", () => {
        const nextText = quickReply.dataset.quickText || "";
        const hasUnsavedText = input.value.trim() && input.value !== form.dataset.savedBody;
        if (hasUnsavedText && !window.confirm("Esta respuesta rápida reemplazará el texto que escribiste. ¿Deseas continuar?")) return;
        input.value = nextText;
        updateCount();
        input.focus();
      });
    });

    edit?.addEventListener("click", () => {
      replyView.hidden = true;
      form.hidden = false;
      cancel.hidden = false;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
    cancel?.addEventListener("click", () => {
      input.value = form.dataset.savedBody || "";
      updateCount();
      error.hidden = true;
      form.hidden = true;
      replyView.hidden = false;
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.hidden = true;
      submit.disabled = true;
      submit.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) throw new Error(payload.error || "No fue posible publicar la respuesta.");

        const wasCreated = Boolean(payload.created);
        form.dataset.savedBody = payload.reply.body;
        input.value = payload.reply.body;
        version.value = payload.reply.version;
        updateCount();
        if (replyBody) replyBody.textContent = payload.reply.body;
        if (replyDate) {
          replyDate.textContent = `${payload.reply.updated_date_label}${payload.reply.is_edited ? " · Editada" : ""}`;
        }
        const status = card.querySelector("[data-review-status]");
        if (status) {
          status.classList.remove("partner-review-status--unanswered");
          status.classList.add("partner-review-status--answered");
          status.querySelector("span").textContent = "Respondida";
          const icon = status.querySelector("[data-lucide]");
          if (icon) icon.dataset.lucide = "circle-check";
        }
        const submitLabel = submit.querySelector("span");
        if (submitLabel) submitLabel.textContent = "Guardar cambios";
        cancel.hidden = false;
        form.hidden = true;
        replyView.hidden = false;
        updateMetrics(payload.metrics);
        refreshIcons();

        if (wasCreated && root.dataset.removeOnAnswer === "true") {
          card.classList.add("is-removing");
          window.setTimeout(() => {
            card.remove();
            const total = root.querySelector("[data-review-filtered-total]");
            const nextTotal = Math.max(0, Number.parseInt(total?.textContent || "0", 10) - 1);
            if (total) total.textContent = String(nextTotal);
            if (nextTotal === 0) {
              const summary = root.querySelector(".partner-review-results__summary");
              if (summary) summary.textContent = "0 reseñas encontradas";
              const list = root.querySelector("[data-review-list]");
              if (list) {
                const empty = document.createElement("section");
                empty.className = "partner-review-empty";
                const title = document.createElement("h2");
                title.textContent = "No quedan reseñas con estos filtros";
                const message = document.createElement("p");
                message.textContent = "La respuesta se publicó correctamente. Ajusta los filtros para verla como respondida.";
                empty.append(title, message);
                list.append(empty);
              }
              root.querySelector(".partner-review-pagination")?.remove();
            }
          }, 210);
        }
      } catch (submissionError) {
        error.textContent = submissionError.message;
        error.hidden = false;
      } finally {
        submit.disabled = false;
        submit.removeAttribute("aria-busy");
      }
    });
  });

  const lightbox = root.querySelector("[data-partner-review-lightbox]");
  const lightboxImage = lightbox?.querySelector("[data-partner-review-lightbox-image]");
  const closeLightbox = () => lightbox?.close();
  root.querySelectorAll("[data-partner-review-image]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!lightbox || !lightboxImage) return;
      lightboxImage.src = button.dataset.src || "";
      lightboxImage.alt = button.dataset.alt || "Foto de reseña";
      lightbox.showModal();
    });
  });
  lightbox?.querySelector("[data-partner-review-lightbox-close]")?.addEventListener("click", closeLightbox);
  lightbox?.addEventListener("click", (event) => {
    if (event.target === lightbox) closeLightbox();
  });
})();
