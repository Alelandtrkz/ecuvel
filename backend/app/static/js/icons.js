document.addEventListener("DOMContentLoaded", () => {
  if (!window.lucide) {
    return;
  }

  const refresh = () => window.lucide.createIcons({
      attrs: {
        "aria-hidden": "true",
        "stroke-width": "1.8",
      },
    });
  window.EcuvelIcons = { refresh };
  refresh();
});
