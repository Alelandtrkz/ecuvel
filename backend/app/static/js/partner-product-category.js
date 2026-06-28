(function () {
  const builder = document.querySelector("[data-category-builder]");
  if (!builder) return;

  const mainOptions = Array.from(builder.querySelectorAll("[data-main-category]"));
  const panels = Array.from(builder.querySelectorAll("[data-subcategory-panel]"));
  const continueButton = builder.querySelector("[data-continue-button]");
  const help = builder.querySelector("[data-selection-help] span");

  function syncMain(categoryId, clearSubcategory) {
    mainOptions.forEach((option) => {
      const input = option.querySelector('input[type="radio"]');
      const selected = option.dataset.mainCategory === categoryId;
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-checked", selected ? "true" : "false");
      if (input) input.checked = selected;
    });
    panels.forEach((panel) => {
      const selected = panel.dataset.subcategoryPanel === categoryId;
      panel.classList.toggle("is-active", selected);
      panel.toggleAttribute("hidden", !selected);
      if (clearSubcategory || !selected) {
        panel.querySelectorAll('input[name="subcategory_id"]').forEach((input) => {
          input.checked = false;
          input.closest(".partner-subcategory-option")?.classList.remove("is-selected");
        });
      }
    });
    syncContinue();
  }

  function syncContinue() {
    const selectedSubcategory = builder.querySelector('input[name="subcategory_id"]:checked');
    if (continueButton) continueButton.disabled = !selectedSubcategory;
    if (help) {
      help.textContent = selectedSubcategory
        ? "Selección lista para continuar."
        : "Se requiere una selección para continuar.";
    }
  }

  mainOptions.forEach((option) => {
    option.setAttribute("role", "radio");
    option.addEventListener("click", () => {
      syncMain(option.dataset.mainCategory, true);
    });
  });

  builder.querySelectorAll(".partner-subcategory-option").forEach((option) => {
    option.addEventListener("click", () => {
      builder.querySelectorAll(".partner-subcategory-option").forEach((item) => {
        item.classList.remove("is-selected");
      });
      option.classList.add("is-selected");
      syncContinue();
    });
  });

  const selectedCategory = builder.querySelector('input[name="category_id"]:checked');
  if (selectedCategory) syncMain(selectedCategory.value, false);
  syncContinue();
})();
