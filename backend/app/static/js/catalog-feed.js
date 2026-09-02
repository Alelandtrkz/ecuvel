document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-catalog-feed]");
  if (!root || !window.fetch) return;

  const target = document.querySelector(root.dataset.feedTarget || "");
  const button = root.querySelector("[data-feed-load]");
  const status = root.querySelector("[data-feed-status]");
  const sentinel = root.querySelector("[data-feed-sentinel]");
  if (!target || !button || !status || !sentinel) return;

  let cursor = root.dataset.feedCursor || "";
  let loadedCount = Number(root.dataset.feedLoadedCount) || 0;
  let state = cursor ? "idle" : "done";
  let activeRequest = null;
  let restoreInProgress = false;
  const seenListingKeys = new Set();
  root.querySelectorAll("[data-listing-key]").forEach((card) => {
    if (card.dataset.listingKey) seenListingKeys.add(card.dataset.listingKey);
  });

  const storageKey = `ecuvel:catalog-feed:${root.dataset.feedContext || "unknown"}`;
  const setStatus = (message) => { status.textContent = message; };
  const finish = () => {
    state = "done";
    cursor = "";
    root.dataset.feedCursor = "";
    button.hidden = true;
    setStatus("Has llegado al final del catálogo.");
    observer?.disconnect();
  };

  const requestUrl = () => {
    const url = new URL(root.dataset.feedEndpoint, window.location.origin);
    url.searchParams.set("cursor", cursor);
    if (root.dataset.feedQuery) url.searchParams.set("q", root.dataset.feedQuery);
    if (root.dataset.feedCategory) url.searchParams.set("category", root.dataset.feedCategory);
    if (root.dataset.feedStore) url.searchParams.set("store", root.dataset.feedStore);
    return url;
  };

  const appendCards = (html) => {
    const template = document.createElement("template");
    template.innerHTML = html;
    let appended = 0;
    template.content.querySelectorAll("[data-product-card]").forEach((card) => {
      const listingKey = card.dataset.listingKey;
      if (listingKey && seenListingKeys.has(listingKey)) {
        card.remove();
        return;
      }
      if (listingKey) seenListingKeys.add(listingKey);
      appended += 1;
    });
    const fragment = template.content;
    target.append(fragment);
    window.EcuvelCatalogTelemetry?.observe(target);
    window.EcuvelIcons?.refresh();
    return appended;
  };

  const loadMore = async () => {
    if (state === "expired") {
      window.location.reload();
      return false;
    }
    if (state !== "idle" || !cursor) return false;
    state = "loading";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setStatus("Cargando más productos…");
    activeRequest = new AbortController();
    try {
      const response = await fetch(requestUrl(), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: activeRequest.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) {
        if (payload.error === "invalid_cursor") {
          state = "expired";
          button.textContent = "Recargar catálogo";
          setStatus(payload.message || "La sesión del catálogo expiró. Recarga para continuar.");
          return false;
        }
        throw new Error(payload.message || "No pudimos cargar más productos.");
      }
      const appended = appendCards(payload.html || "");
      loadedCount = Number(payload.loaded_count) || loadedCount;
      root.dataset.feedLoadedCount = String(loadedCount);
      cursor = payload.next_cursor || "";
      root.dataset.feedCursor = cursor;
      if (!payload.has_more || !cursor) {
        finish();
      } else {
        state = "idle";
        setStatus(`${appended} producto${appended === 1 ? "" : "s"} más cargado${appended === 1 ? "" : "s"}.`);
      }
      return true;
    } catch (error) {
      if (error.name === "AbortError") return false;
      state = "error";
      button.textContent = "Intentar cargar más";
      setStatus(error.message || "No pudimos cargar más productos.");
      state = "idle";
      return false;
    } finally {
      activeRequest = null;
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  };

  button.addEventListener("click", loadMore);
  const observer = "IntersectionObserver" in window && state !== "done"
    ? new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting) && !restoreInProgress) loadMore();
      }, { rootMargin: "600px 0px" })
    : null;
  observer?.observe(sentinel);

  const persistForDetail = () => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({
        pending: true,
        context: root.dataset.feedContext,
        loadedCount,
        scrollY: window.scrollY,
        savedAt: Date.now(),
      }));
    } catch (_error) {}
  };

  root.addEventListener("click", (event) => {
    const detailLink = event.target.closest?.("[data-product-card] a[href*='/productos/']");
    if (detailLink) persistForDetail();
  });

  const restore = async () => {
    let saved;
    try {
      saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    } catch (_error) {
      saved = null;
    }
    if (
      !saved?.pending
      || saved.context !== root.dataset.feedContext
      || Date.now() - Number(saved.savedAt) > 3600000
    ) return;
    try {
      sessionStorage.removeItem(storageKey);
    } catch (_error) {}
    restoreInProgress = true;
    while (state === "idle" && loadedCount < Number(saved.loadedCount)) {
      const loaded = await loadMore();
      if (!loaded) break;
    }
    restoreInProgress = false;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => window.scrollTo(0, Number(saved.scrollY) || 0));
    });
  };

  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    try { sessionStorage.removeItem(storageKey); } catch (_error) {}
    window.EcuvelCatalogTelemetry?.observe(root);
  });
  restore();
});
