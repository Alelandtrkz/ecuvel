(function () {
  const toggles = document.querySelectorAll("[data-password-toggle]");

  toggles.forEach((button) => {
    button.addEventListener("click", () => {
      const input = button.parentElement?.querySelector("input");
      if (!input) return;
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      button.setAttribute(
        "aria-label",
        showing ? "Mostrar contraseña" : "Ocultar contraseña",
      );
    });
  });
})();
