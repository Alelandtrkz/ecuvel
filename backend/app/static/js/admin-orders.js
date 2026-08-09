document.addEventListener("DOMContentLoaded", () => {
  const rowMenus = [...document.querySelectorAll(".admin-row-actions[data-admin-menu]")];
  const menuItems = (panel) => [...panel.querySelectorAll('[role="menuitem"]')];

  const closeRowMenus = (except = null) => {
    rowMenus.forEach((menu) => {
      if (menu === except) return;
      const trigger = menu.querySelector("[data-admin-menu-trigger]");
      const panel = menu.querySelector("[data-admin-menu-panel]");
      if (trigger && panel) {
        trigger.setAttribute("aria-expanded", "false");
        panel.hidden = true;
      }
    });
  };

  rowMenus.forEach((menu) => {
    const trigger = menu.querySelector("[data-admin-menu-trigger]");
    const panel = menu.querySelector("[data-admin-menu-panel]");
    trigger?.addEventListener("click", (event) => {
      event.stopPropagation();
      const opening = trigger.getAttribute("aria-expanded") !== "true";
      closeRowMenus(opening ? menu : null);
      trigger.setAttribute("aria-expanded", String(opening));
      if (panel) panel.hidden = !opening;
      if (opening && panel) {
        const triggerRect = trigger.getBoundingClientRect();
        const panelRect = panel.getBoundingClientRect();
        const gutter = 8;
        panel.style.left = `${Math.min(
          window.innerWidth - panelRect.width - gutter,
          Math.max(gutter, triggerRect.right - panelRect.width),
        )}px`;
        panel.style.top = `${
          window.innerHeight - triggerRect.bottom >= panelRect.height + gutter
            ? triggerRect.bottom + 6
            : Math.max(gutter, triggerRect.top - panelRect.height - 6)
        }px`;
        menuItems(panel)[0]?.focus();
      }
    });
    panel?.addEventListener("keydown", (event) => {
      const items = menuItems(panel);
      const currentIndex = items.indexOf(document.activeElement);
      let targetIndex = null;
      if (event.key === "ArrowDown") targetIndex = (currentIndex + 1) % items.length;
      if (event.key === "ArrowUp") targetIndex = (currentIndex - 1 + items.length) % items.length;
      if (event.key === "Home") targetIndex = 0;
      if (event.key === "End") targetIndex = items.length - 1;
      if (targetIndex !== null && items.length) {
        event.preventDefault();
        items[targetIndex].focus();
      }
    });
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".admin-row-actions")) closeRowMenus();
  });

  const dialogs = [...document.querySelectorAll("dialog.admin-dialog")];
  const notes = document.querySelector("[data-review-notes]");
  document.querySelectorAll("[data-modal-open]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.modalOpen || "");
      if (!dialog) return;
      dialog.querySelectorAll("[data-modal-notes]").forEach((field) => {
        field.value = notes?.value || "";
      });
      dialog.showModal();
      dialog.querySelector("textarea:not([type='hidden']), button")?.focus();
    });
  });
  dialogs.forEach((dialog) => {
    dialog.querySelectorAll("[data-modal-close]").forEach((button) => {
      button.addEventListener("click", () => dialog.close());
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      const openMenu = rowMenus.find((menu) =>
        menu.querySelector("[data-admin-menu-trigger]")?.getAttribute("aria-expanded") === "true"
      );
      closeRowMenus();
      openMenu?.querySelector("[data-admin-menu-trigger]")?.focus();
    }
  });
  window.addEventListener("resize", () => closeRowMenus());
  window.addEventListener("scroll", () => closeRowMenus(), true);

  const viewer = document.querySelector("[data-proof-viewer]");
  const image = viewer?.querySelector("[data-proof-image]");
  if (viewer && image) {
    let scale = 1;
    let rotation = 0;
    const render = () => {
      image.style.transform = `scale(${scale}) rotate(${rotation}deg)`;
      image.dataset.zoomed = String(scale !== 1 || rotation !== 0);
    };
    viewer.querySelector("[data-proof-zoom-in]")?.addEventListener("click", () => {
      scale = Math.min(3, Number((scale + 0.2).toFixed(1)));
      render();
    });
    viewer.querySelector("[data-proof-zoom-out]")?.addEventListener("click", () => {
      scale = Math.max(0.4, Number((scale - 0.2).toFixed(1)));
      render();
    });
    viewer.querySelector("[data-proof-rotate]")?.addEventListener("click", () => {
      rotation = (rotation + 90) % 360;
      render();
    });
    viewer.querySelector("[data-proof-fit]")?.addEventListener("click", () => {
      scale = 1;
      rotation = 0;
      render();
    });
  }
});
