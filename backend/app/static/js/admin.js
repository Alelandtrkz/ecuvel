document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.querySelector("#admin-sidebar");
  const backdrop = document.querySelector(".admin-sidebar-backdrop");
  const openButton = document.querySelector("[data-admin-sidebar-open]");
  const closeButtons = document.querySelectorAll("[data-admin-sidebar-close]");

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

  const menu = document.querySelector("[data-admin-menu]");
  const menuTrigger = menu?.querySelector("[data-admin-menu-trigger]");
  const menuPanel = menu?.querySelector("[data-admin-menu-panel]");

  const closeMenu = (restoreFocus = false) => {
    if (!menuTrigger || !menuPanel) return;
    menuTrigger.setAttribute("aria-expanded", "false");
    menuPanel.hidden = true;
    if (restoreFocus) menuTrigger.focus();
  };

  menuTrigger?.addEventListener("click", () => {
    const willOpen = menuTrigger.getAttribute("aria-expanded") !== "true";
    menuTrigger.setAttribute("aria-expanded", String(willOpen));
    if (menuPanel) menuPanel.hidden = !willOpen;
    if (willOpen) menuPanel?.querySelector("a")?.focus();
  });

  document.addEventListener("click", (event) => {
    if (menu && !menu.contains(event.target)) closeMenu(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (menuPanel && !menuPanel.hidden) closeMenu(true);
    if (sidebar?.classList.contains("is-open")) closeSidebar();
  });

  window.matchMedia("(min-width: 981px)").addEventListener("change", (event) => {
    if (event.matches && sidebar?.classList.contains("is-open")) closeSidebar();
  });
});
