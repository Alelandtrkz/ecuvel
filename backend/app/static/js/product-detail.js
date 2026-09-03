const ecuvelGalleryMath = (() => {
  const clamp = (value, minimum, maximum) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return minimum;
    return Math.min(maximum, Math.max(minimum, numeric));
  };

  const fitMetrics = (naturalWidth, naturalHeight, viewportWidth, viewportHeight) => {
    const width = Math.max(0, Number(naturalWidth) || 0);
    const height = Math.max(0, Number(naturalHeight) || 0);
    const availableWidth = Math.max(0, Number(viewportWidth) || 0);
    const availableHeight = Math.max(0, Number(viewportHeight) || 0);
    if (!width || !height || !availableWidth || !availableHeight) {
      return { fittedWidth: 0, fittedHeight: 0, maxZoom: 1 };
    }
    const fitScale = Math.min(1, availableWidth / width, availableHeight / height);
    const fittedWidth = width * fitScale;
    const fittedHeight = height * fitScale;
    const maxZoom = Math.max(1, Math.min(
      4,
      width / fittedWidth,
      height / fittedHeight,
    ));
    return { fittedWidth, fittedHeight, maxZoom };
  };

  const panBounds = (fittedWidth, fittedHeight, scale, viewportWidth, viewportHeight) => ({
    x: Math.max(0, ((Number(fittedWidth) || 0) * (Number(scale) || 1) - (Number(viewportWidth) || 0)) / 2),
    y: Math.max(0, ((Number(fittedHeight) || 0) * (Number(scale) || 1) - (Number(viewportHeight) || 0)) / 2),
  });

  const pointerDistance = (first, second) => Math.hypot(
    second.x - first.x,
    second.y - first.y,
  );

  return { clamp, fitMetrics, panBounds, pointerDistance };
})();

globalThis.EcuvelGalleryMath = ecuvelGalleryMath;

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
    const { clamp, fitMetrics, panBounds, pointerDistance } = ecuvelGalleryMath;
    const mainImage = gallery.querySelector("[data-gallery-main-image]");
    const openButton = gallery.querySelector("[data-gallery-open]");
    const dialog = gallery.querySelector("[data-gallery-dialog]");
    const dialogImage = gallery.querySelector("[data-gallery-dialog-image]");
    const zoomViewport = gallery.querySelector("[data-gallery-zoom-viewport]");
    const zoomInButton = gallery.querySelector("[data-gallery-zoom-in]");
    const zoomOutButton = gallery.querySelector("[data-gallery-zoom-out]");
    const zoomFitButton = gallery.querySelector("[data-gallery-zoom-fit]");
    const zoomStatus = gallery.querySelector("[data-gallery-zoom-status]");
    const dialogClose = gallery.querySelector("[data-gallery-close]");
    let thumbnails = Array.from(gallery.querySelectorAll("[data-gallery-thumbnail]"));
    let previousButton = gallery.querySelector("[data-gallery-previous]");
    let nextButton = gallery.querySelector("[data-gallery-next]");
    let counter = gallery.querySelector("[data-gallery-counter]");
    const dialogPrevious = gallery.querySelector("[data-gallery-dialog-previous]");
    const dialogNext = gallery.querySelector("[data-gallery-dialog-next]");
    const dialogCounter = gallery.querySelector("[data-gallery-dialog-counter]");
    const fallbackUrl = gallery.dataset.galleryPlaceholderUrl;
    const fallbackAlt = gallery.dataset.galleryPlaceholderAlt;

    if (!mainImage || !dialog || !dialogImage || !zoomViewport) return;

    const numberOrNull = (value) => {
      const parsed = Number(value);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    };
    const itemFromThumbnail = (thumbnail) => ({
      masterUrl: thumbnail.dataset.galleryMasterUrl,
      thumbnailUrl: thumbnail.dataset.galleryThumbnailUrl || thumbnail.dataset.galleryMasterUrl,
      alt: thumbnail.dataset.galleryAlt || fallbackAlt,
      masterWidth: numberOrNull(thumbnail.dataset.galleryMasterWidth),
      masterHeight: numberOrNull(thumbnail.dataset.galleryMasterHeight),
      thumbnailWidth: numberOrNull(thumbnail.dataset.galleryThumbnailWidth),
      thumbnailHeight: numberOrNull(thumbnail.dataset.galleryThumbnailHeight),
      identity: thumbnail.dataset.galleryIdentity || thumbnail.dataset.galleryMasterUrl,
      isPlaceholder: false,
    });
    const itemFromMain = () => ({
      masterUrl: mainImage.getAttribute("src"),
      thumbnailUrl: mainImage.getAttribute("src"),
      alt: mainImage.alt || fallbackAlt,
      masterWidth: numberOrNull(mainImage.getAttribute("width")),
      masterHeight: numberOrNull(mainImage.getAttribute("height")),
      thumbnailWidth: null,
      thumbnailHeight: null,
      identity: mainImage.getAttribute("src"),
      isPlaceholder: gallery.dataset.galleryHasMedia !== "true",
    });
    const placeholderItem = () => ({
      masterUrl: fallbackUrl,
      thumbnailUrl: fallbackUrl,
      alt: fallbackAlt,
      masterWidth: null,
      masterHeight: null,
      thumbnailWidth: null,
      thumbnailHeight: null,
      identity: "placeholder",
      isPlaceholder: true,
    });
    const normalizeVariantItem = (raw, index, name) => {
      if (typeof raw === "string") {
        return {
          ...placeholderItem(),
          masterUrl: raw,
          thumbnailUrl: raw,
          alt: `${name || "Producto"}, vista ${index + 1}`,
          identity: raw,
          isPlaceholder: false,
        };
      }
      if (!raw || typeof raw !== "object" || !raw.master_url) return null;
      return {
        masterUrl: raw.master_url,
        thumbnailUrl: raw.thumbnail_url || raw.master_url,
        alt: raw.alt || `${name || "Producto"}, vista ${index + 1}`,
        masterWidth: numberOrNull(raw.master_width),
        masterHeight: numberOrNull(raw.master_height),
        thumbnailWidth: numberOrNull(raw.thumbnail_width),
        thumbnailHeight: numberOrNull(raw.thumbnail_height),
        identity: raw.identity || raw.master_url,
        isPlaceholder: false,
      };
    };

    let items = thumbnails.length ? thumbnails.map(itemFromThumbnail) : [itemFromMain()];
    let currentIndex = 0;
    let lightboxOpener = null;
    let resizeFrame = null;
    const zoom = {
      scale: 1,
      maxZoom: 1,
      fittedWidth: 0,
      fittedHeight: 0,
      panX: 0,
      panY: 0,
    };
    const pointers = new Map();
    let dragStart = null;
    let pinchStart = null;
    let lastTap = null;

    const announceZoom = (message) => {
      if (zoomStatus) zoomStatus.textContent = message;
    };
    const currentBounds = () => panBounds(
      zoom.fittedWidth,
      zoom.fittedHeight,
      zoom.scale,
      zoomViewport.clientWidth,
      zoomViewport.clientHeight,
    );
    const clampPan = () => {
      if (zoom.scale <= 1) {
        zoom.panX = 0;
        zoom.panY = 0;
        return;
      }
      const bounds = currentBounds();
      zoom.panX = clamp(zoom.panX, -bounds.x, bounds.x);
      zoom.panY = clamp(zoom.panY, -bounds.y, bounds.y);
    };
    const updateZoomControls = () => {
      const atFit = zoom.scale <= 1.001;
      const atMaximum = zoom.scale >= zoom.maxZoom - 0.001;
      if (zoomInButton) zoomInButton.disabled = zoom.maxZoom <= 1.001 || atMaximum;
      if (zoomOutButton) zoomOutButton.disabled = atFit;
      if (zoomFitButton) zoomFitButton.disabled = atFit;
      zoomViewport.classList.toggle("is-zoomed", !atFit);
      zoomViewport.classList.toggle("is-pannable", currentBounds().x > 0 || currentBounds().y > 0);
    };
    const applyZoomTransform = () => {
      clampPan();
      dialogImage.style.width = `${zoom.fittedWidth}px`;
      dialogImage.style.height = `${zoom.fittedHeight}px`;
      dialogImage.style.transform = `translate3d(${zoom.panX}px, ${zoom.panY}px, 0) scale(${zoom.scale})`;
      updateZoomControls();
    };
    const recalculateFit = ({ reset = false } = {}) => {
      const isPlaceholder = items[currentIndex]?.isPlaceholder || dialogImage.dataset.galleryPlaceholder === "true";
      const metrics = fitMetrics(
        dialogImage.naturalWidth,
        dialogImage.naturalHeight,
        zoomViewport.clientWidth,
        zoomViewport.clientHeight,
      );
      zoom.fittedWidth = metrics.fittedWidth;
      zoom.fittedHeight = metrics.fittedHeight;
      zoom.maxZoom = isPlaceholder ? 1 : metrics.maxZoom;
      if (reset) {
        zoom.scale = 1;
        zoom.panX = 0;
        zoom.panY = 0;
      } else {
        zoom.scale = clamp(zoom.scale, 1, zoom.maxZoom);
      }
      applyZoomTransform();
    };
    const resetZoom = (message = "Imagen ajustada.") => {
      zoom.scale = 1;
      zoom.panX = 0;
      zoom.panY = 0;
      pointers.clear();
      dragStart = null;
      pinchStart = null;
      applyZoomTransform();
      announceZoom(message);
    };
    const zoomTo = (nextScale, clientX = null, clientY = null) => {
      const previousScale = zoom.scale;
      const targetScale = clamp(nextScale, 1, zoom.maxZoom);
      if (Math.abs(targetScale - previousScale) < 0.001) {
        updateZoomControls();
        if (zoom.maxZoom <= 1.001) announceZoom("Esta imagen ya se muestra a su resolución disponible.");
        return;
      }
      if (clientX !== null && clientY !== null) {
        const rect = zoomViewport.getBoundingClientRect();
        const offsetX = clientX - (rect.left + rect.width / 2);
        const offsetY = clientY - (rect.top + rect.height / 2);
        const ratio = targetScale / previousScale;
        zoom.panX = offsetX - ((offsetX - zoom.panX) * ratio);
        zoom.panY = offsetY - ((offsetY - zoom.panY) * ratio);
      } else {
        const ratio = targetScale / previousScale;
        zoom.panX *= ratio;
        zoom.panY *= ratio;
      }
      zoom.scale = targetScale;
      applyZoomTransform();
      announceZoom(targetScale <= 1.001 ? "Imagen ajustada." : `Zoom ${Math.round(targetScale * 100)}%.`);
    };

    const applyImageFallback = (image) => {
      if (image.dataset.galleryFallbackBound === "true") return;
      image.dataset.galleryFallbackBound = "true";
      image.addEventListener("error", () => {
        const step = Number(image.dataset.galleryFallbackStep || 0);
        const preferred = image.dataset.galleryFallbackUrl;
        if (step === 0 && preferred && image.src !== new URL(preferred, document.baseURI).href) {
          image.dataset.galleryFallbackStep = "1";
          image.src = preferred;
          return;
        }
        if (step < 2 && image.src !== new URL(fallbackUrl, document.baseURI).href) {
          image.dataset.galleryFallbackStep = "2";
          image.dataset.galleryPlaceholder = "true";
          image.src = fallbackUrl;
          if (image.alt) image.alt = fallbackAlt;
        }
      });
    };
    gallery.querySelectorAll("[data-gallery-fallback-image]").forEach(applyImageFallback);

    const updateImage = (image, item) => {
      image.dataset.galleryFallbackStep = "0";
      delete image.dataset.galleryPlaceholder;
      delete image.dataset.galleryFallbackUrl;
      image.src = item.masterUrl;
      image.alt = item.alt;
      if (item.masterWidth && item.masterHeight) {
        image.width = item.masterWidth;
        image.height = item.masterHeight;
      } else {
        image.removeAttribute("width");
        image.removeAttribute("height");
      }
    };
    const normalizedIndex = (index) => (index + items.length) % items.length;
    const updateSelection = (moveFocus = false) => {
      thumbnails.forEach((thumbnail, index) => {
        const selected = index === currentIndex;
        thumbnail.classList.toggle("is-active", selected);
        thumbnail.setAttribute("aria-pressed", String(selected));
        thumbnail.tabIndex = selected ? 0 : -1;
      });
      const selectedThumbnail = thumbnails[currentIndex];
      selectedThumbnail?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
      if (moveFocus) selectedThumbnail?.focus();
    };
    const updateCounters = () => {
      const text = items[0]?.isPlaceholder ? "Imagen de presentación" : `${currentIndex + 1} / ${items.length}`;
      if (counter) counter.textContent = text;
      if (dialogCounter) dialogCounter.textContent = text;
    };
    const selectImage = (index, moveFocus = false) => {
      currentIndex = normalizedIndex(index);
      const item = items[currentIndex];
      resetZoom();
      updateImage(mainImage, item);
      mainImage.dataset.galleryIndex = String(currentIndex);
      updateSelection(moveFocus);
      updateCounters();
      if (dialog.open) {
        updateImage(dialogImage, item);
        if (dialogImage.complete && dialogImage.naturalWidth) recalculateFit({ reset: true });
      }
      if (items.length > 1) {
        const preload = new Image();
        preload.src = items[normalizedIndex(currentIndex + 1)].masterUrl;
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

    const icon = (name) => {
      const element = document.createElement("i");
      element.dataset.lucide = name;
      element.setAttribute("aria-hidden", "true");
      return element;
    };
    const ensureMainNavigation = () => {
      const stage = gallery.querySelector(".product-gallery__stage");
      if (!previousButton) {
        previousButton = document.createElement("button");
        previousButton.type = "button";
        previousButton.className = "product-gallery__navigation product-gallery__navigation--previous";
        previousButton.dataset.galleryPrevious = "";
        previousButton.setAttribute("aria-label", "Ver imagen anterior");
        previousButton.replaceChildren(icon("chevron-left"));
        previousButton.addEventListener("click", showPrevious);
        stage?.append(previousButton);
      }
      if (!nextButton) {
        nextButton = document.createElement("button");
        nextButton.type = "button";
        nextButton.className = "product-gallery__navigation product-gallery__navigation--next";
        nextButton.dataset.galleryNext = "";
        nextButton.setAttribute("aria-label", "Ver imagen siguiente");
        nextButton.replaceChildren(icon("chevron-right"));
        nextButton.addEventListener("click", showNext);
        stage?.append(nextButton);
      }
      if (!counter) {
        counter = document.createElement("span");
        counter.className = "product-gallery__counter";
        counter.dataset.galleryCounter = "";
        counter.setAttribute("role", "status");
        counter.setAttribute("aria-live", "polite");
        stage?.append(counter);
      }
    };

    gallery.addEventListener("ecuvel:gallery-images", (event) => {
      const rawImages = Array.isArray(event.detail?.images) ? event.detail.images : [];
      items = rawImages
        .map((raw, index) => normalizeVariantItem(raw, index, event.detail?.name))
        .filter(Boolean);
      if (!items.length) items = [placeholderItem()];

      let thumbnailContainer = gallery.querySelector(".product-gallery__thumbnails");
      if (!thumbnailContainer && items.length > 1) {
        thumbnailContainer = document.createElement("div");
        thumbnailContainer.className = "product-gallery__thumbnails";
        thumbnailContainer.setAttribute("role", "group");
        thumbnailContainer.setAttribute("aria-label", "Imágenes del producto");
        gallery.querySelector(".product-gallery__layout")?.prepend(thumbnailContainer);
      }
      if (thumbnailContainer) {
        const nodes = items.map((item, index) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = `product-gallery__thumbnail${index === 0 ? " is-active" : ""}`;
          button.dataset.galleryThumbnail = "";
          button.dataset.galleryIndex = String(index);
          button.dataset.galleryMasterUrl = item.masterUrl;
          button.dataset.galleryThumbnailUrl = item.thumbnailUrl;
          button.dataset.galleryAlt = item.alt;
          button.dataset.galleryMasterWidth = item.masterWidth || "";
          button.dataset.galleryMasterHeight = item.masterHeight || "";
          button.dataset.galleryThumbnailWidth = item.thumbnailWidth || "";
          button.dataset.galleryThumbnailHeight = item.thumbnailHeight || "";
          button.dataset.galleryIdentity = item.identity;
          button.setAttribute("aria-label", `Mostrar imagen ${index + 1}`);
          button.setAttribute("aria-pressed", String(index === 0));
          button.tabIndex = index === 0 ? 0 : -1;
          const image = document.createElement("img");
          image.src = item.thumbnailUrl;
          image.alt = "";
          image.loading = "lazy";
          image.decoding = "async";
          image.dataset.galleryFallbackImage = "";
          image.dataset.galleryFallbackUrl = item.masterUrl;
          if (item.thumbnailWidth && item.thumbnailHeight) {
            image.width = item.thumbnailWidth;
            image.height = item.thumbnailHeight;
          }
          applyImageFallback(image);
          button.replaceChildren(image);
          return button;
        });
        thumbnailContainer.replaceChildren(...nodes);
        thumbnailContainer.hidden = items.length < 2;
        thumbnails = Array.from(thumbnailContainer.querySelectorAll("[data-gallery-thumbnail]"));
        bindThumbnails();
      }
      if (items.length > 1) ensureMainNavigation();
      [previousButton, nextButton, dialogPrevious, dialogNext, counter].forEach((element) => {
        element?.toggleAttribute("hidden", items.length < 2);
      });
      gallery.classList.toggle("product-gallery--multiple", items.length > 1);
      gallery.classList.toggle("product-gallery--single", items.length < 2);
      gallery.dataset.galleryHasMedia = String(!items[0].isPlaceholder);
      currentIndex = 0;
      selectImage(0);
      window.lucide?.createIcons?.();
    });

    const isEditable = (target) => target instanceof HTMLElement && (
      target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
    );
    const handleNavigation = (event, moveFocus = false) => {
      if (items.length < 2 || isEditable(event.target)) return false;
      const destinations = { ArrowLeft: currentIndex - 1, ArrowRight: currentIndex + 1, Home: 0, End: items.length - 1 };
      if (!(event.key in destinations)) return false;
      event.preventDefault();
      selectImage(destinations[event.key], moveFocus);
      return true;
    };
    gallery.addEventListener("keydown", (event) => {
      if (!dialog.open) handleNavigation(event, thumbnails.includes(event.target));
    });

    zoomInButton?.addEventListener("click", () => zoomTo(zoom.scale * 1.4));
    zoomOutButton?.addEventListener("click", () => zoomTo(zoom.scale / 1.4));
    zoomFitButton?.addEventListener("click", () => resetZoom());
    dialogImage.addEventListener("load", () => recalculateFit({ reset: true }));

    const pointerPoint = (event) => ({ x: event.clientX, y: event.clientY });
    const beginRemainingDrag = () => {
      const remaining = Array.from(pointers.values())[0];
      dragStart = remaining ? { x: remaining.x, y: remaining.y, panX: zoom.panX, panY: zoom.panY } : null;
    };
    zoomViewport.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      zoomViewport.setPointerCapture?.(event.pointerId);
      const point = pointerPoint(event);
      pointers.set(event.pointerId, { ...point, pointerType: event.pointerType, startX: point.x, startY: point.y, startedAt: performance.now() });
      if (pointers.size === 1) {
        dragStart = { ...point, panX: zoom.panX, panY: zoom.panY };
      } else if (pointers.size === 2) {
        const [first, second] = Array.from(pointers.values());
        pinchStart = { distance: Math.max(1, pointerDistance(first, second)), scale: zoom.scale };
      }
      event.preventDefault();
    });
    zoomViewport.addEventListener("pointermove", (event) => {
      if (!pointers.has(event.pointerId)) return;
      const stored = pointers.get(event.pointerId);
      pointers.set(event.pointerId, { ...stored, ...pointerPoint(event) });
      if (pointers.size >= 2 && pinchStart) {
        const [first, second] = Array.from(pointers.values());
        const centerX = (first.x + second.x) / 2;
        const centerY = (first.y + second.y) / 2;
        zoomTo(pinchStart.scale * (pointerDistance(first, second) / pinchStart.distance), centerX, centerY);
      } else if (pointers.size === 1 && dragStart && zoom.scale > 1) {
        zoom.panX = dragStart.panX + event.clientX - dragStart.x;
        zoom.panY = dragStart.panY + event.clientY - dragStart.y;
        applyZoomTransform();
      }
      event.preventDefault();
    });
    const finishPointer = (event, cancelled = false) => {
      const stored = pointers.get(event.pointerId);
      pointers.delete(event.pointerId);
      try { zoomViewport.releasePointerCapture?.(event.pointerId); } catch (_error) { /* capture already released */ }
      pinchStart = null;
      beginRemainingDrag();
      if (cancelled || !stored || event.pointerType !== "touch") return;
      const distance = Math.hypot(event.clientX - stored.startX, event.clientY - stored.startY);
      const now = performance.now();
      if (distance > 10 || now - stored.startedAt > 350) {
        lastTap = null;
        return;
      }
      if (lastTap && now - lastTap.time <= 320 && Math.hypot(event.clientX - lastTap.x, event.clientY - lastTap.y) <= 32) {
        if (zoom.maxZoom > 1.001) {
          if (zoom.scale > 1.001) resetZoom();
          else zoomTo(Math.min(2, zoom.maxZoom), event.clientX, event.clientY);
        }
        lastTap = null;
      } else {
        lastTap = { time: now, x: event.clientX, y: event.clientY };
      }
    };
    zoomViewport.addEventListener("pointerup", (event) => finishPointer(event));
    zoomViewport.addEventListener("pointercancel", (event) => finishPointer(event, true));
    zoomViewport.addEventListener("lostpointercapture", (event) => {
      if (pointers.has(event.pointerId)) finishPointer(event, true);
    });

    const closeLightbox = () => {
      resetZoom();
      if (dialog.open) dialog.close();
    };
    openButton?.addEventListener("click", () => {
      lightboxOpener = openButton;
      resetZoom();
      updateImage(dialogImage, items[currentIndex]);
      dialog.showModal();
      document.body.classList.add("gallery-lightbox-open");
      window.requestAnimationFrame(() => recalculateFit({ reset: true }));
      dialogClose?.focus();
    });
    dialogClose?.addEventListener("click", closeLightbox);
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeLightbox();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeLightbox();
    });
    dialog.addEventListener("close", () => {
      resetZoom();
      document.body.classList.remove("gallery-lightbox-open");
      lightboxOpener?.focus();
    });
    dialog.addEventListener("keydown", (event) => {
      if (isEditable(event.target)) return;
      if (["+", "="].includes(event.key)) {
        event.preventDefault();
        zoomTo(zoom.scale * 1.4);
      } else if (event.key === "-") {
        event.preventDefault();
        zoomTo(zoom.scale / 1.4);
      } else if (event.key === "0") {
        event.preventDefault();
        resetZoom();
      } else if (handleNavigation(event)) {
        return;
      } else if (event.key === "Tab") {
        const focusable = Array.from(dialog.querySelectorAll("button:not(:disabled):not([hidden]), [href], [tabindex]:not([tabindex='-1'])"));
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    });
    window.addEventListener("resize", () => {
      if (!dialog.open || resizeFrame !== null) return;
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = null;
        recalculateFit();
      });
    });

    updateSelection();
    updateCounters();
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
