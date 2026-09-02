(function () {
  "use strict";

  const catalogMenu = document.getElementById("catalog-menu");
  if (catalogMenu) {
    const mainCats = catalogMenu.querySelectorAll(".catalog-dropdown__main-cat");
    const subPanels = catalogMenu.querySelectorAll(".catalog-dropdown__sub-panel");
    const showPanel = (index) => {
      mainCats.forEach((button) => button.classList.remove("is-active"));
      subPanels.forEach((panel) => panel.classList.remove("is-visible"));
      catalogMenu.querySelector(`[data-cat-index="${index}"]`)?.classList.add("is-active");
      catalogMenu.querySelector(`[data-panel-index="${index}"]`)?.classList.add("is-visible");
    };
    mainCats.forEach((button) => {
      button.addEventListener("mouseenter", () => showPanel(button.dataset.catIndex));
    });
    document.addEventListener("click", (event) => {
      if (catalogMenu.open && !catalogMenu.contains(event.target)) {
        catalogMenu.removeAttribute("open");
      }
    });
  }

  const modal = document.getElementById("category-modal");
  if (!modal) return;

  const valueInput = document.getElementById("search-category-value");
  const labelSpan = document.getElementById("search-category-label");
  const closeButton = document.getElementById("close-category-modal");
  const items = Array.from(modal.querySelectorAll(".category-modal__item"));
  const openers = Array.from(document.querySelectorAll("[data-category-modal-open]"));
  let activeOpener = null;

  const focusable = () => Array.from(
    modal.querySelectorAll("button:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])"),
  ).filter((element) => !element.hidden);

  const openModal = (opener) => {
    activeOpener = opener;
    modal.hidden = false;
    document.body.classList.add("category-modal-open");
    const current = valueInput?.value || "";
    items.forEach((item) => {
      item.classList.toggle("is-selected", item.dataset.slug === current);
    });
    closeButton?.focus();
  };

  const closeModal = ({ restoreFocus = true } = {}) => {
    modal.hidden = true;
    document.body.classList.remove("category-modal-open");
    if (restoreFocus) activeOpener?.focus();
  };

  openers.forEach((opener) => opener.addEventListener("click", () => openModal(opener)));
  closeButton?.addEventListener("click", () => closeModal());

  items.forEach((item) => {
    item.addEventListener("click", () => {
      if (activeOpener?.id === "search-category-btn" && valueInput) {
        valueInput.value = item.dataset.slug || "";
        if (labelSpan) labelSpan.textContent = item.dataset.name || "Todos";
        closeModal();
        return;
      }
      const href = item.dataset.href;
      closeModal({ restoreFocus: false });
      if (href) window.location.assign(href);
    });
  });

  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });

  document.addEventListener("keydown", (event) => {
    if (modal.hidden) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeModal();
      return;
    }
    if (event.key !== "Tab") return;
    const elements = focusable();
    if (!elements.length) return;
    const first = elements[0];
    const last = elements[elements.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
})();
