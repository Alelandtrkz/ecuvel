document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-proof-upload]");
  if (!form) return;
  const input = form.querySelector("[data-proof-file]");
  const preview = form.querySelector("[data-proof-preview]");
  const image = form.querySelector("[data-proof-image]");
  const pdf = form.querySelector("[data-proof-pdf]");
  const name = form.querySelector("[data-proof-name]");
  const size = form.querySelector("[data-proof-size]");
  const submit = form.querySelector("[data-proof-submit]");
  let objectUrl = null;

  input.addEventListener("change", () => {
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    const file = input.files[0];
    preview.hidden = !file;
    submit.disabled = !file;
    if (!file) return;
    name.textContent = file.name;
    size.textContent = `${(file.size / 1024 / 1024).toFixed(2)} MiB`;
    const isImage = file.type.startsWith("image/");
    if (isImage) {
      objectUrl = URL.createObjectURL(file);
      image.hidden = true;
      image.onload = () => { image.hidden = false; image.onload = null; };
      image.src = objectUrl;
      if (pdf) pdf.hidden = true;
    } else {
      image.removeAttribute("src");
      image.hidden = true;
      if (pdf) pdf.hidden = false;
    }
  });

  form.addEventListener("submit", () => {
    submit.disabled = true;
    submit.textContent = "Enviando…";
  });
});
