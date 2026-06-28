(function () {
  const dialog = document.querySelector("[data-store-dialog]");
  if (!dialog || typeof dialog.showModal !== "function") {
    return;
  }

  const body = dialog.querySelector("[data-store-dialog-body]");
  const closeButton = dialog.querySelector("[data-store-dialog-close]");
  let opener = null;

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function focusableElements() {
    return Array.from(
      dialog.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      )
    ).filter((element) => element.offsetParent !== null);
  }

  function closeDialog() {
    dialog.close();
  }

  function renderLoading() {
    body.innerHTML = `
      <section class="store-dialog-content" aria-live="polite">
        <h2 id="store-dialog-title">Cargando información</h2>
        <p>Estamos preparando la información pública de la tienda.</p>
      </section>
    `;
  }

  function renderError(fallbackUrl) {
    body.innerHTML = `
      <section class="store-dialog-content store-dialog__error">
        <h2 id="store-dialog-title">No pudimos cargar esta información</h2>
        <p>Intenta abrir la página completa para ver este contenido.</p>
        <a class="store-dialog__accept" href="${fallbackUrl}">Abrir página</a>
      </section>
    `;
  }

  async function loadFragment(trigger) {
    const fragmentUrl = trigger.dataset.dialogUrl;
    const fallbackUrl = trigger.getAttribute("href");
    renderLoading();
    refreshIcons();

    try {
      const response = await fetch(fragmentUrl, {
        headers: {
          Accept: "text/html",
          "X-Requested-With": "fetch",
        },
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error("fragment_failed");
      }
      body.innerHTML = await response.text();
      refreshIcons();
    } catch (_error) {
      renderError(fallbackUrl);
      refreshIcons();
    }
  }

  document.addEventListener("click", async (event) => {
    const trigger = event.target.closest("[data-store-dialog-trigger]");
    if (!trigger) {
      return;
    }
    event.preventDefault();
    opener = trigger;
    document.body.classList.add("store-dialog-open");
    dialog.showModal();
    await loadFragment(trigger);
    const firstFocusable = focusableElements()[0] || closeButton;
    firstFocusable?.focus();
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      closeDialog();
    }
    if (event.target.closest("[data-store-dialog-close], [data-store-dialog-accept]")) {
      closeDialog();
    }
  });

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog();
  });

  dialog.addEventListener("close", () => {
    document.body.classList.remove("store-dialog-open");
    if (opener && typeof opener.focus === "function") {
      opener.focus();
    }
  });

  dialog.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") {
      return;
    }
    const focusable = focusableElements();
    if (!focusable.length) {
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
})();
