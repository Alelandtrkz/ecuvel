document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-checkout-form]");
  if (!form) {
    return;
  }
  const options = Array.from(form.querySelectorAll("[data-payment-method]"));
  const submit = form.querySelector("[data-checkout-submit]");

  const updateMethod = () => {
    options.forEach((option) => {
      option.closest(".payment-option")?.classList.toggle(
        "is-selected",
        option.checked,
      );
    });
    if (submit) {
      submit.textContent = "Generar pedido";
    }
  };
  options.forEach((option) => option.addEventListener("change", updateMethod));
  updateMethod();

  form.addEventListener("submit", (event) => {
    if (!window.confirm("Se creará el pedido y se reservará el inventario. ¿Continuar?")) {
      event.preventDefault();
      return;
    }
    if (submit) {
      submit.disabled = true;
      submit.textContent = "Generando pedido…";
    }
  });
});
