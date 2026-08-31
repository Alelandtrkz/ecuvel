const ADMIN_SIDEBAR_STORAGE_KEY = "ecuvel.admin.sidebar.collapsed";
const adminSidebarDesktop = window.matchMedia("(min-width: 981px)");
const adminSidebarMedium = window.matchMedia("(min-width: 981px) and (max-width: 1240px)");

const readAdminSidebarPreference = () => {
  try {
    const value = window.localStorage.getItem(ADMIN_SIDEBAR_STORAGE_KEY);
    return value === "true" ? true : (value === "false" ? false : null);
  } catch (_error) {
    return null;
  }
};

const writeAdminSidebarPreference = (collapsed) => {
  try {
    window.localStorage.setItem(ADMIN_SIDEBAR_STORAGE_KEY, String(collapsed));
  } catch (_error) {
    // The shell remains usable when storage is unavailable.
  }
};

const preferredAdminSidebarState = () => {
  const stored = readAdminSidebarPreference();
  return stored === null ? adminSidebarMedium.matches : stored;
};

const applyAdminSidebarState = (collapsed = preferredAdminSidebarState()) => {
  document.documentElement.classList.toggle(
    "admin-sidebar-collapsed",
    adminSidebarDesktop.matches && collapsed
  );
};

applyAdminSidebarState();

document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.querySelector("#admin-sidebar");
  const backdrop = document.querySelector(".admin-sidebar-backdrop");
  const openButton = document.querySelector("[data-admin-sidebar-open]");
  const closeButtons = document.querySelectorAll("[data-admin-sidebar-close]");
  const collapseButton = document.querySelector("[data-admin-sidebar-toggle]");
  const sidebarTooltip = document.querySelector("#admin-sidebar-tooltip");
  const tooltipTargets = document.querySelectorAll("[data-admin-tooltip]");

  const isSidebarCollapsed = () => document.documentElement.classList.contains(
    "admin-sidebar-collapsed"
  );

  const updateCollapseButton = () => {
    if (!collapseButton) return;
    const expanded = !isSidebarCollapsed();
    const label = expanded ? "Contraer barra lateral" : "Expandir barra lateral";
    collapseButton.setAttribute("aria-expanded", String(expanded));
    collapseButton.setAttribute("aria-label", label);
    collapseButton.title = label;
  };

  const hideSidebarTooltip = () => {
    if (!sidebarTooltip) return;
    sidebarTooltip.hidden = true;
    tooltipTargets.forEach((target) => target.removeAttribute("aria-describedby"));
  };

  const showSidebarTooltip = (target) => {
    if (!sidebarTooltip || !isSidebarCollapsed() || !adminSidebarDesktop.matches) return;
    const label = target.dataset.adminTooltip || target.getAttribute("aria-label");
    if (!label) return;
    const bounds = target.getBoundingClientRect();
    sidebarTooltip.textContent = label;
    sidebarTooltip.hidden = false;
    sidebarTooltip.style.left = `${Math.round(bounds.right + 10)}px`;
    sidebarTooltip.style.top = `${Math.round(bounds.top + (bounds.height / 2))}px`;
    sidebarTooltip.style.transform = "translateY(-50%)";
    target.setAttribute("aria-describedby", sidebarTooltip.id);
  };

  collapseButton?.addEventListener("click", () => {
    const collapsed = !isSidebarCollapsed();
    writeAdminSidebarPreference(collapsed);
    applyAdminSidebarState(collapsed);
    hideSidebarTooltip();
    updateCollapseButton();
  });
  tooltipTargets.forEach((target) => {
    target.addEventListener("mouseenter", () => showSidebarTooltip(target));
    target.addEventListener("focus", () => showSidebarTooltip(target));
    target.addEventListener("mouseleave", hideSidebarTooltip);
    target.addEventListener("blur", hideSidebarTooltip);
  });
  updateCollapseButton();

  const closeSidebar = () => {
    if (!sidebar || !backdrop) return;
    sidebar.classList.remove("is-open");
    backdrop.hidden = true;
    document.body.classList.remove("admin-drawer-open");
    openButton?.focus();
  };

  const openSidebar = () => {
    if (!sidebar || !backdrop) return;
    sidebar.classList.add("is-open");
    backdrop.hidden = false;
    document.body.classList.add("admin-drawer-open");
    sidebar.querySelector("a, button")?.focus();
  };

  openButton?.addEventListener("click", openSidebar);
  closeButtons.forEach((button) => button.addEventListener("click", closeSidebar));

  const disclosures = [];

  const setupDisclosure = (root, triggerSelector, panelSelector) => {
    const trigger = root?.querySelector(triggerSelector);
    const panel = root?.querySelector(panelSelector);
    if (!root || !trigger || !panel) return null;

    const focusableItems = () => Array.from(
      panel.querySelectorAll('a[href], button:not([disabled]), [role="menuitem"]')
    ).filter((item) => !item.hidden);

    const close = (restoreFocus = false) => {
      trigger.setAttribute("aria-expanded", "false");
      panel.hidden = true;
      if (restoreFocus) trigger.focus();
    };

    const open = (focusPosition = null) => {
      disclosures.forEach((other) => {
        if (other?.root !== root) other?.close(false);
      });
      trigger.setAttribute("aria-expanded", "true");
      panel.hidden = false;
      const items = focusableItems();
      if (focusPosition === "first") items[0]?.focus();
      if (focusPosition === "last") items.at(-1)?.focus();
    };

    trigger.addEventListener("click", () => {
      if (trigger.getAttribute("aria-expanded") === "true") close(false);
      else open();
    });

    trigger.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        open("first");
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        open("last");
      }
    });

    panel.addEventListener("keydown", (event) => {
      const items = focusableItems();
      const currentIndex = items.indexOf(document.activeElement);
      let nextIndex = null;
      if (event.key === "ArrowDown") nextIndex = (currentIndex + 1) % items.length;
      if (event.key === "ArrowUp") nextIndex = (currentIndex - 1 + items.length) % items.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = items.length - 1;
      if (nextIndex !== null && items.length) {
        event.preventDefault();
        items[nextIndex]?.focus();
      }
    });

    return { root, trigger, panel, close, open };
  };

  const quickMenu = setupDisclosure(
    document.querySelector("[data-admin-menu]"),
    "[data-admin-menu-trigger]",
    "[data-admin-menu-panel]"
  );
  if (quickMenu) disclosures.push(quickMenu);

  const profileMenu = setupDisclosure(
    document.querySelector("[data-admin-profile-menu]"),
    "[data-admin-profile-trigger]",
    "[data-admin-profile-panel]"
  );
  if (profileMenu) disclosures.push(profileMenu);

  document.addEventListener("click", (event) => {
    disclosures.forEach((menu) => {
      if (!menu.root.contains(event.target)) menu.close(false);
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    hideSidebarTooltip();
    const openMenu = disclosures.find((menu) => !menu.panel.hidden);
    if (openMenu) openMenu.close(true);
    if (sidebar?.classList.contains("is-open")) closeSidebar();
  });

  window.matchMedia("(min-width: 981px)").addEventListener("change", (event) => {
    if (event.matches && sidebar?.classList.contains("is-open")) closeSidebar();
    applyAdminSidebarState();
    hideSidebarTooltip();
    updateCollapseButton();
  });
  adminSidebarMedium.addEventListener("change", () => {
    if (readAdminSidebarPreference() !== null) return;
    applyAdminSidebarState();
    hideSidebarTooltip();
    updateCollapseButton();
  });
});
