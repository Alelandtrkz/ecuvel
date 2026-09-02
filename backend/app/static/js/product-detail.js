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
    let thumbnails = Array.from(
      gallery.querySelectorAll("[data-gallery-thumbnail]"),
    );
    let counter = gallery.querySelector("[data-gallery-counter]");
    let previousButton = gallery.querySelector("[data-gallery-previous]");
    let nextButton = gallery.querySelector("[data-gallery-next]");
    const openButton = gallery.querySelector("[data-gallery-open]");
    const dialog = gallery.querySelector("[data-gallery-dialog]");
    const dialogImage = gallery.querySelector("[data-gallery-dialog-image]");
    let dialogCounter = gallery.querySelector("[data-gallery-dialog-counter]");
    let dialogPrevious = gallery.querySelector("[data-gallery-dialog-previous]");
    let dialogNext = gallery.querySelector("[data-gallery-dialog-next]");
    const dialogClose = gallery.querySelector("[data-gallery-close]");
    const fallbackUrl = gallery.dataset.galleryPlaceholderUrl;
    const fallbackAlt = gallery.dataset.galleryPlaceholderAlt;
    let currentIndex = 0;
    let lightboxOpener = null;

    if (!mainImage || !dialog || !dialogImage) {
      return;
    }

    let items = thumbnails.length
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

    const bindThumbnails = () => thumbnails.forEach((thumbnail, index) => {
      thumbnail.addEventListener("click", () => selectImage(index));
    });
    bindThumbnails();
    previousButton?.addEventListener("click", showPrevious);
    nextButton?.addEventListener("click", showNext);
    dialogPrevious?.addEventListener("click", showPrevious);
    dialogNext?.addEventListener("click", showNext);

    gallery.addEventListener("ecuvel:gallery-images", (event) => {
      const nextImages = Array.isArray(event.detail?.images) ? event.detail.images : [];
      items = nextImages.length
        ? nextImages.map((src, index) => ({ src, alt: `${event.detail?.name || "Producto"}, vista ${index + 1}` }))
        : [{ src: fallbackUrl, alt: fallbackAlt }];
      let thumbnailContainer = gallery.querySelector(".product-gallery__thumbnails");
      if (!thumbnailContainer && items.length > 1) {
        thumbnailContainer = document.createElement("div");
        thumbnailContainer.className = "product-gallery__thumbnails";
        thumbnailContainer.setAttribute("role", "tablist");
        gallery.querySelector(".product-gallery__layout")?.prepend(thumbnailContainer);
      }
      if (thumbnailContainer) {
        thumbnailContainer.replaceChildren();
        items.forEach((item, index) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = `product-gallery__thumbnail${index === 0 ? " is-active" : ""}`;
          button.dataset.galleryThumbnail = "";
          button.dataset.gallerySrc = item.src;
          button.dataset.galleryAlt = item.alt;
          button.setAttribute("role", "tab");
          button.setAttribute("aria-selected", String(index === 0));
          button.innerHTML = `<img src="${item.src}" alt="" loading="lazy">`;
          thumbnailContainer.appendChild(button);
        });
        thumbnailContainer.hidden = items.length < 2;
        thumbnails = Array.from(thumbnailContainer.querySelectorAll("[data-gallery-thumbnail]"));
        bindThumbnails();
      }
      if (items.length > 1 && !previousButton) {
        const stage = gallery.querySelector(".product-gallery__stage");
        previousButton = document.createElement("button");
        previousButton.type = "button";
        previousButton.className = "product-gallery__navigation product-gallery__navigation--previous";
        previousButton.dataset.galleryPrevious = "";
        previousButton.setAttribute("aria-label", "Ver imagen anterior");
        previousButton.innerHTML = '<i data-lucide="chevron-left" aria-hidden="true"></i>';
        nextButton = document.createElement("button");
        nextButton.type = "button";
        nextButton.className = "product-gallery__navigation product-gallery__navigation--next";
        nextButton.dataset.galleryNext = "";
        nextButton.setAttribute("aria-label", "Ver imagen siguiente");
        nextButton.innerHTML = '<i data-lucide="chevron-right" aria-hidden="true"></i>';
        counter = document.createElement("span");
        counter.className = "product-gallery__counter";
        counter.dataset.galleryCounter = "";
        counter.setAttribute("aria-live", "polite");
        stage?.append(previousButton, nextButton, counter);
        previousButton.addEventListener("click", showPrevious);
        nextButton.addEventListener("click", showNext);
      }
      if (items.length > 1 && !dialogPrevious) {
        const dialogStage = gallery.querySelector(".product-gallery-lightbox__stage");
        dialogPrevious = document.createElement("button");
        dialogPrevious.type = "button";
        dialogPrevious.dataset.galleryDialogPrevious = "";
        dialogPrevious.setAttribute("aria-label", "Ver imagen anterior");
        dialogPrevious.innerHTML = '<i data-lucide="chevron-left" aria-hidden="true"></i>';
        dialogNext = document.createElement("button");
        dialogNext.type = "button";
        dialogNext.dataset.galleryDialogNext = "";
        dialogNext.setAttribute("aria-label", "Ver imagen siguiente");
        dialogNext.innerHTML = '<i data-lucide="chevron-right" aria-hidden="true"></i>';
        dialogStage?.prepend(dialogPrevious);
        dialogStage?.append(dialogNext);
        dialogPrevious.addEventListener("click", showPrevious);
        dialogNext.addEventListener("click", showNext);
        dialogCounter = document.createElement("span");
        dialogCounter.dataset.galleryDialogCounter = "";
        gallery.querySelector(".product-gallery-lightbox header")?.insertBefore(
          dialogCounter,
          dialogClose,
        );
      }
      previousButton?.toggleAttribute("hidden", items.length < 2);
      nextButton?.toggleAttribute("hidden", items.length < 2);
      dialogPrevious?.toggleAttribute("hidden", items.length < 2);
      dialogNext?.toggleAttribute("hidden", items.length < 2);
      counter?.toggleAttribute("hidden", items.length < 2);
      dialogCounter?.toggleAttribute("hidden", items.length < 2);
      currentIndex = 0;
      selectImage(0);
      window.lucide?.createIcons?.();
    });

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

    if (!input) {
      return;
    }

    let maximum = Math.max(0, Number(selector.dataset.maxQuantity) || 0);
    const setQuantity = (value) => {
      const minimum = Number(input.min) || 1;
      if (maximum <= 0) {
        input.value = "1";
        input.disabled = true;
        decrease.disabled = true;
        increase.disabled = true;
        return;
      }
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
    selector.addEventListener("ecuvel:quantity-max", (event) => {
      maximum = Math.max(0, Number(event.detail?.maximum) || 0);
      selector.dataset.maxQuantity = String(maximum);
      input.max = String(Math.max(1, maximum));
      input.disabled = maximum <= 0;
      setQuantity(Math.min(Number(input.value) || 1, Math.max(1, maximum)));
    });
    setQuantity(Number(input.value));
  });

  document.querySelectorAll("[data-product-variant-selector]").forEach((selector) => {
    const payloadNode = selector.querySelector("[data-product-variant-payload]");
    if (!payloadNode) return;

    let payload;
    try {
      payload = JSON.parse(payloadNode.textContent || "{}");
    } catch (_error) {
      return;
    }
    const axes = Array.isArray(payload.axes) ? payload.axes : [];
    const variants = Array.isArray(payload.variants) ? payload.variants : [];
    if (!axes.length || !variants.length) return;

    const valueKey = (axis, variant) => {
      const raw = variant.attributes?.[axis.key];
      const matched = (axis.values || []).find(
        (value) => value.key === raw || value.label === raw,
      );
      return matched?.key || String(raw || "");
    };
    const variantValues = (variant) => Object.fromEntries(
      axes.map((axis) => [axis.key, valueKey(axis, variant)]),
    );
    let selected = variants.find(
      (variant) => variant.catalog_sku === payload.selected_catalog_sku,
    ) || variants[0];
    let selection = variantValues(selected);

    const formatMoney = (amount, currency) => {
      if (amount === null || amount === undefined || amount === "") return "";
      const numeric = Number(amount);
      if (!Number.isFinite(numeric)) return "";
      return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: currency || "USD",
        minimumFractionDigits: 2,
      }).format(numeric);
    };

    const matches = (variant, expected, exceptAxis = null) => {
      const values = variantValues(variant);
      return axes.every(
        (axis) => axis.key === exceptAxis || !expected[axis.key] || values[axis.key] === expected[axis.key],
      );
    };

    const updateButtons = () => {
      axes.forEach((axis) => {
        selector.querySelectorAll(`[data-public-variant-axis="${CSS.escape(axis.key)}"] [data-public-variant-value]`).forEach((button) => {
          const key = button.dataset.publicVariantValue;
          // En V3 las presentaciones son filas manuales, no una matriz completa.
          // Una opción sigue siendo navegable si existe en cualquier presentación.
          const candidates = variants.filter(
            (variant) => variantValues(variant)[axis.key] === key,
          );
          const exists = candidates.length > 0;
          const hasStock = candidates.some((variant) => variant.is_available);
          const isSelected = selection[axis.key] === key;
          button.disabled = !exists;
          button.classList.toggle("is-selected", isSelected);
          button.classList.toggle("is-out-of-stock", exists && !hasStock);
          button.setAttribute("aria-pressed", String(isSelected));
          button.setAttribute("aria-label", `${button.textContent.trim()}${exists && !hasStock ? ", agotado" : ""}`);
        });
      });
    };

    const applyVariant = (variant, updateUrl = true) => {
      selected = variant;
      selection = variantValues(variant);
      const publicTitle = variant.name ? `${payload.base_title || "Producto"} — ${variant.name}` : (payload.base_title || "Producto");
      document.querySelector("#product-title")?.replaceChildren(publicTitle);
      document.querySelector("[data-selected-variant-name]")?.replaceChildren(variant.name || "");
      document.querySelector("[data-selected-sku]")?.replaceChildren(variant.seller_sku || variant.catalog_sku || "");

      const price = document.querySelector("[data-variant-price]");
      const compare = document.querySelector("[data-variant-compare-price]");
      const availability = document.querySelector("[data-variant-availability]");
      const availabilityLabel = document.querySelector("[data-variant-availability-label]");
      const deliveryLabel = document.querySelector("[data-variant-delivery-label]");
      const offerInput = document.querySelector("[data-variant-offer-id]");
      const stockMessage = document.querySelector("[data-stock-message]");
      const addButton = document.querySelector("[data-cart-add-form] .purchase-card__add");
      const quantity = document.querySelector("[data-quantity-selector]");
      const previewCommercial = Boolean(document.querySelector("[data-preview-commercial]"));

      if (price) price.textContent = formatMoney(variant.price, variant.currency) || "Precio pendiente";
      if (compare) {
        compare.textContent = variant.compare_at_price
          ? formatMoney(variant.compare_at_price, variant.currency)
          : "";
        compare.hidden = !variant.compare_at_price;
      }
      if (availability) {
        availability.classList.toggle("purchase-card__availability--available", variant.is_available);
        availability.classList.toggle("purchase-card__availability--unavailable", !variant.is_available);
      }
      if (availabilityLabel) availabilityLabel.textContent = variant.availability_label;
      if (deliveryLabel) deliveryLabel.textContent = variant.delivery_label;
      if (offerInput) offerInput.value = variant.offer_id;
      if (stockMessage) {
        stockMessage.textContent = variant.availability_message;
        stockMessage.hidden = variant.is_available && !variant.low_stock;
        stockMessage.classList.toggle("purchase-card__stock-message--low", Boolean(variant.low_stock));
        stockMessage.classList.toggle("purchase-card__stock-message--unavailable", !variant.is_available);
      }
      if (addButton) addButton.disabled = previewCommercial || !variant.is_available;
      quantity?.dispatchEvent(new CustomEvent("ecuvel:quantity-max", {
        detail: { maximum: variant.max_quantity },
      }));
      document.querySelector("[data-product-gallery]")?.dispatchEvent(
        new CustomEvent("ecuvel:gallery-images", {
          detail: { images: variant.images, name: variant.name || document.title },
        }),
      );
      updateButtons();
      if (updateUrl && variant.catalog_sku) {
        const url = new URL(window.location.href);
        url.searchParams.set("variant", variant.catalog_sku);
        window.history.replaceState({}, "", url);
        document.querySelectorAll(
          "[data-cart-add-form] input[name='next'], [data-favorite-form] input[name='next']",
        ).forEach((nextInput) => {
          nextInput.value = `${url.pathname}${url.search}`;
        });
      }
    };

    selector.addEventListener("click", (event) => {
      const button = event.target.closest("[data-public-variant-value]");
      if (!button || button.disabled) return;
      const fieldset = button.closest("[data-public-variant-axis]");
      const axisKey = fieldset?.dataset.publicVariantAxis;
      if (!axisKey) return;
      const expected = { ...selection, [axisKey]: button.dataset.publicVariantValue };
      let next = variants.find((variant) => matches(variant, expected));
      if (!next) {
        const candidates = variants.filter(
          (variant) => variantValues(variant)[axisKey] === button.dataset.publicVariantValue,
        );
        const otherAxes = axes.filter((axis) => axis.key !== axisKey);
        candidates.sort((left, right) => {
          const score = (variant) => otherAxes.reduce(
            (total, axis) => total + Number(variantValues(variant)[axis.key] === selection[axis.key]),
            0,
          );
          return score(right) - score(left) || Number(right.is_available) - Number(left.is_available);
        });
        next = candidates[0];
      }
      if (next) applyVariant(next);
    });

    applyVariant(selected, false);
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
