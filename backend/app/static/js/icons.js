document.addEventListener("DOMContentLoaded", () => {
  if (!window.lucide) {
    return;
  }

  window.lucide.createIcons({
    attrs: {
      "aria-hidden": "true",
      "stroke-width": "1.8",
    },
  });
});
