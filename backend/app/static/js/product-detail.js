document.addEventListener("DOMContentLoaded", () => {
  const showDynamicNotice = (message) => {
    const notice = document.querySelector("[data-notice-toast]");
    const noticeMessage = notice?.querySelector("[data-notice-message]");
    if (!notice || !noticeMessage) return;
    noticeMessage.textContent = message;
    notice.hidden = false;
    window.requestAnimationFrame(() => notice.classList.add("is-visible"));
  };

  document.querySelectorAll("[data-product-gallery]").forEach((gallery) => {
    const mainImage = gallery.querySelector("[data-gallery-main-image]");
    const thumbnails = Array.from(
      gallery.querySelectorAll("[data-gallery-thumbnail]"),
    );
    const counter = gallery.querySelector("[data-gallery-counter]");
    const previousButton = gallery.querySelector("[data-gallery-previous]");
    const nextButton = gallery.querySelector("[data-gallery-next]");
    const openButton = gallery.querySelector("[data-gallery-open]");
    const dialog = gallery.querySelector("[data-gallery-dialog]");
    const dialogImage = gallery.querySelector("[data-gallery-dialog-image]");
    const dialogCounter = gallery.querySelector("[data-gallery-dialog-counter]");
    const dialogPrevious = gallery.querySelector("[data-gallery-dialog-previous]");
    const dialogNext = gallery.querySelector("[data-gallery-dialog-next]");
    const dialogClose = gallery.querySelector("[data-gallery-close]");
    const fallbackUrl = gallery.dataset.galleryPlaceholderUrl;
    const fallbackAlt = gallery.dataset.galleryPlaceholderAlt;
    let currentIndex = 0;
    let lightboxOpener = null;

    if (!mainImage || !dialog || !dialogImage) {
      return;
    }

    const items = thumbnails.length
      ? thumbnails.map((thumbnail) => ({
          src: thumbnail.dataset.gallerySrc,
          alt: thumbnail.dataset.galleryAlt,
        }))
      : [{ src: mainImage.getAttribute("src"), alt: mainImage.alt }];

    const applyImageFallback = (image) => {
      image.addEventListener("error", () => {
        if (image.dataset.fallbackApplied === "true") {
          return;
        }
        image.dataset.fallbackApplied = "true";
        image.src = fallbackUrl;
        if (image.hasAttribute("alt") && image.alt) {
          image.alt = fallbackAlt;
        }
      });
    };

    gallery
      .querySelectorAll("[data-gallery-fallback-image]")
      .forEach(applyImageFallback);

    const updateImage = (image, item) => {
      delete image.dataset.fallbackApplied;
      image.src = item.src;
      image.alt = item.alt;
    };

    const normalizedIndex = (index) =>
      (index + items.length) % items.length;

    const selectImage = (index, moveFocus = false) => {
      currentIndex = normalizedIndex(index);
      const currentItem = items[currentIndex];
      updateImage(mainImage, currentItem);
      mainImage.dataset.galleryIndex = String(currentIndex);

      thumbnails.forEach((thumbnail, thumbnailIndex) => {
        const isSelected = thumbnailIndex === currentIndex;
        thumbnail.classList.toggle("is-active", isSelected);
        thumbnail.setAttribute("aria-selected", String(isSelected));
        thumbnail.tabIndex = isSelected ? 0 : -1;
      });

      const selectedThumbnail = thumbnails[currentIndex];
      selectedThumbnail?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
        inline: "nearest",
      });
      if (moveFocus) {
        selectedThumbnail?.focus();
      }

      const counterText = `${currentIndex + 1} / ${items.length}`;
      if (counter) {
        counter.textContent = counterText;
      }
      if (dialogCounter) {
        dialogCounter.textContent = counterText;
      }
      if (dialog.open) {
        updateImage(dialogImage, currentItem);
      }

      if (items.length > 1) {
        const nextItem = items[normalizedIndex(currentIndex + 1)];
        const preload = new Image();
        preload.src = nextItem.src;
      }
    };

    const showPrevious = () => selectImage(currentIndex - 1);
    const showNext = () => selectImage(currentIndex + 1);

    thumbnails.forEach((thumbnail, index) => {
      thumbnail.addEventListener("click", () => selectImage(index));
    });
    previousButton?.addEventListener("click", showPrevious);
    nextButton?.addEventListener("click", showNext);
    dialogPrevious?.addEventListener("click", showPrevious);
    dialogNext?.addEventListener("click", showNext);

    const handleGalleryNavigation = (event, moveFocus = false) => {
      if (items.length < 2) {
        return;
      }
      const destinations = {
        ArrowLeft: currentIndex - 1,
        ArrowRight: currentIndex + 1,
        Home: 0,
        End: items.length - 1,
      };
      if (!(event.key in destinations)) {
        return;
      }
      event.preventDefault();
      selectImage(destinations[event.key], moveFocus);
    };

    gallery.addEventListener("keydown", (event) => {
      if (!dialog.open) {
        handleGalleryNavigation(event, thumbnails.includes(event.target));
      }
    });

    const closeLightbox = () => {
      if (dialog.open) {
        dialog.close();
      }
    };

    openButton?.addEventListener("click", () => {
      lightboxOpener = openButton;
      updateImage(dialogImage, items[currentIndex]);
      dialog.showModal();
      document.body.classList.add("gallery-lightbox-open");
      dialogClose?.focus();
    });
    dialogClose?.addEventListener("click", closeLightbox);
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeLightbox();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        closeLightbox();
      }
    });
    dialog.addEventListener("close", () => {
      document.body.classList.remove("gallery-lightbox-open");
      lightboxOpener?.focus();
    });
    dialog.addEventListener("keydown", (event) => {
      handleGalleryNavigation(event);
      if (event.key !== "Tab") {
        return;
      }

      const focusable = Array.from(
        dialog.querySelectorAll("button:not(:disabled), [href], [tabindex]:not([tabindex='-1'])"),
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

    selectImage(0);
  });

  document.querySelectorAll("[data-quantity-selector]").forEach((selector) => {
    const input = selector.querySelector("input[type='number']");
    const decrease = selector.querySelector("[data-quantity-decrease]");
    const increase = selector.querySelector("[data-quantity-increase]");

    if (!input || input.disabled) {
      return;
    }

    const maximum = Number(selector.dataset.maxQuantity) || 1;
    const setQuantity = (value) => {
      const minimum = Number(input.min) || 1;
      if (value > maximum) {
        showDynamicNotice(`Solo quedan ${maximum} unidades disponibles.`);
      }
      input.value = String(Math.min(maximum, Math.max(minimum, value)));
      decrease.disabled = Number(input.value) <= minimum;
      increase.disabled = Number(input.value) >= maximum;
      decrease.setAttribute("aria-disabled", String(decrease.disabled));
      increase.setAttribute("aria-disabled", String(increase.disabled));
      increase.setAttribute(
        "aria-label",
        increase.disabled
          ? "Cantidad máxima disponible alcanzada"
          : "Aumentar cantidad",
      );
    };

    decrease.addEventListener("click", () => setQuantity(Number(input.value) - 1));
    increase.addEventListener("click", () => setQuantity(Number(input.value) + 1));
    input.addEventListener("change", () => setQuantity(Number(input.value) || 1));
    setQuantity(Number(input.value));
  });

  document.querySelectorAll("[data-cart-add-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = form.querySelector("input[name='quantity']");
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
        });
        const payload = await response.json();
        if (!response.ok) {
          if (input && payload.max_quantity) {
            input.max = String(payload.max_quantity);
            input.value = String(
              Math.min(Number(input.value), Number(payload.max_quantity)),
            );
          }
          showDynamicNotice(payload.message || "No fue posible añadir el producto.");
          return;
        }
        window.location.assign(payload.redirect_url || window.location.href);
      } catch (_error) {
        HTMLFormElement.prototype.submit.call(form);
      }
    });
  });

  const toast = document.querySelector("[data-notice-toast]");
  const toastMessage = toast?.querySelector("[data-notice-message]");
  const closeButton = toast?.querySelector("[data-notice-close]");
  let closeTimer;

  const closeToast = () => {
    if (!toast) {
      return;
    }
    toast.classList.remove("is-visible");
    window.setTimeout(() => {
      toast.hidden = true;
    }, 180);
  };

  document.querySelectorAll("[data-notice]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!toast || !toastMessage) {
        return;
      }
      window.clearTimeout(closeTimer);
      toastMessage.textContent = button.dataset.notice;
      toast.hidden = false;
      window.requestAnimationFrame(() => toast.classList.add("is-visible"));
      closeTimer = window.setTimeout(closeToast, 5000);
    });
  });

  closeButton?.addEventListener("click", closeToast);
});
