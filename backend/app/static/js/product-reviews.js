document.addEventListener("DOMContentLoaded", () => {
  const dialog = document.querySelector("[data-review-lightbox]");
  const image = dialog?.querySelector("[data-review-lightbox-image]");
  const closeButton = dialog?.querySelector("[data-review-lightbox-close]");
  const previousButton = dialog?.querySelector("[data-review-lightbox-previous]");
  const nextButton = dialog?.querySelector("[data-review-lightbox-next]");
  const counter = dialog?.querySelector("[data-review-lightbox-counter]");
  const items = Array.from(document.querySelectorAll("[data-review-lightbox-item]"));

  if (!dialog || !image || !items.length) {
    return;
  }

  const groups = new Map();
  let currentGroup = [];
  let currentIndex = 0;
  let opener = null;

  items.forEach((item) => {
    const groupName = item.dataset.reviewLightboxGroup || "default";
    const groupItems = groups.get(groupName) || [];
    groupItems.push({
      button: item,
      src: item.dataset.reviewLightboxSrc || "",
      alt: item.dataset.reviewLightboxAlt || "Foto de reseña",
    });
    groups.set(groupName, groupItems);
  });

  const update = () => {
    const item = currentGroup[currentIndex];
    if (!item) {
      return;
    }
    image.src = item.src;
    image.alt = item.alt;
    if (counter) {
      counter.textContent = `${currentIndex + 1} / ${currentGroup.length}`;
      counter.hidden = currentGroup.length < 2;
    }
    if (previousButton) {
      previousButton.hidden = currentGroup.length < 2;
    }
    if (nextButton) {
      nextButton.hidden = currentGroup.length < 2;
    }
  };

  const normalizedIndex = (index) =>
    (index + currentGroup.length) % currentGroup.length;

  const goTo = (index) => {
    if (!currentGroup.length) {
      return;
    }
    currentIndex = normalizedIndex(index);
    update();
  };

  const close = () => {
    if (dialog.open) {
      dialog.close();
    }
  };

  items.forEach((button) => {
    button.addEventListener("click", () => {
      const groupName = button.dataset.reviewLightboxGroup || "default";
      currentGroup = groups.get(groupName) || [];
      currentIndex = Number(button.dataset.reviewLightboxIndex || 0);
      opener = button;
      update();
      dialog.showModal();
      document.body.classList.add("review-lightbox-open");
      closeButton?.focus();
    });
  });

  previousButton?.addEventListener("click", () => goTo(currentIndex - 1));
  nextButton?.addEventListener("click", () => goTo(currentIndex + 1));
  closeButton?.addEventListener("click", close);

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      close();
    }
  });

  dialog.addEventListener("close", () => {
    document.body.classList.remove("review-lightbox-open");
    image.removeAttribute("src");
    opener?.focus({ preventScroll: true });
    opener = null;
  });

  dialog.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      goTo(currentIndex - 1);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      goTo(currentIndex + 1);
    }
    if (event.key !== "Tab") {
      return;
    }
    const focusable = Array.from(
      dialog.querySelectorAll("button:not([hidden]):not(:disabled), [href], [tabindex]:not([tabindex='-1'])"),
    );
    if (!focusable.length) {
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
});
