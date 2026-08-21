(() => {
  document.querySelectorAll("[data-confirm-open]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.dataset.confirmOpen)?.showModal());
  });
  document.querySelectorAll("[data-confirm-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });
  document.querySelectorAll(".admin-confirm-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  });

  const cedulaValid = (value) => {
    const digits = value.replace(/\D/g, "");
    if (digits.length !== 10) return false;
    const province = Number(digits.slice(0, 2));
    if (province < 1 || province > 24 || Number(digits[2]) > 5) return false;
    let total = 0;
    for (let index = 0; index < 9; index += 1) {
      let item = Number(digits[index]) * (index % 2 === 0 ? 2 : 1);
      total += item > 9 ? item - 9 : item;
    }
    return (10 - (total % 10)) % 10 === Number(digits[9]);
  };
  document.querySelectorAll("[data-identification-number]").forEach((input) => {
    const feedback = input.parentElement.querySelector("[data-identification-feedback]");
    const update = () => {
      const kind = input.form.elements.identification_type?.value;
      if (kind !== "ECUADOR_CEDULA" || !input.value) {
        feedback.textContent = "La validación comprueba formato, no identidad oficial.";
        feedback.classList.remove("is-valid", "is-invalid");
        return;
      }
      const valid = cedulaValid(input.value);
      feedback.textContent = valid ? "Formato matemático válido." : "La cédula no supera la validación matemática.";
      feedback.classList.toggle("is-valid", valid);
      feedback.classList.toggle("is-invalid", !valid);
    };
    input.addEventListener("input", update);
    input.form.elements.identification_type?.addEventListener("change", update);
    update();
  });

  document.querySelectorAll("[data-staff-form]").forEach((form) => {
    const role = form.querySelector("[data-staff-role]");
    const preview = form.querySelector("[data-permission-preview]");
    if (!role || !preview) return;
    const matrix = JSON.parse(form.dataset.rolePermissions || "{}");
    const labels = JSON.parse(form.dataset.permissionLabels || "{}");
    const update = () => {
      preview.replaceChildren(...(matrix[role.value] || []).map((permission) => {
        const item = document.createElement("li");
        item.textContent = labels[permission] || permission;
        return item;
      }));
    };
    role.addEventListener("change", update);
    update();
  });
})();
