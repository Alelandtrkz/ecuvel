document.addEventListener("DOMContentLoaded", () => {
  const liveRegion = document.createElement("div");
  liveRegion.className = "visually-hidden";
  liveRegion.setAttribute("aria-live", "polite");
  document.body.append(liveRegion);

  const updateFavoriteBadges = (count) => {
    let badges = Array.from(document.querySelectorAll("[data-favorite-count]"));
    if (!badges.length && count > 0) {
      document.querySelectorAll(".header-action--favorites").forEach((link) => {
        const badge = document.createElement("strong");
        badge.className = "header-favorite-badge";
        badge.dataset.favoriteCount = "";
        link.append(badge);
        badges.push(badge);
      });
    }
    badges.forEach((badge) => {
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.hidden = count <= 0;
    });
  };

  const syncMatchingForms = (productSlug, isFavorite) => {
    document
      .querySelectorAll(`[data-favorite-form][data-product-slug="${CSS.escape(productSlug)}"]`)
      .forEach((form) => {
        const button = form.querySelector("button[aria-pressed]");
        if (!button) return;
        form.action = form.action.replace(
          isFavorite ? "/agregar" : "/eliminar",
          isFavorite ? "/eliminar" : "/agregar",
        );
        button.classList.toggle("is-active", isFavorite);
        button.setAttribute("aria-pressed", String(isFavorite));
        const label = button.getAttribute("aria-label") || "";
        button.setAttribute(
          "aria-label",
          isFavorite
            ? label.replace(/^Añadir|^Guardar/, "Eliminar").replace(" a favoritos", " de favoritos")
            : label.replace(/^Eliminar/, "Añadir").replace(" de favoritos", " a favoritos"),
        );
        const text = button.querySelector("span");
        if (text && /favoritos/i.test(text.textContent || "")) {
          text.textContent = isFavorite ? "Guardado en favoritos" : "Guardar en favoritos";
        }
      });
  };

  document.querySelectorAll("[data-favorite-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      if (!window.fetch || !window.FormData) return;
      event.preventDefault();
      const submitter = event.submitter || form.querySelector("button[type='submit']");
      const productSlug = form.dataset.productSlug;
      submitter?.setAttribute("aria-busy", "true");
      submitter?.setAttribute("disabled", "");
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: {
            Accept: "application/json",
          },
          credentials: "same-origin",
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
          if (payload.login_url) {
            window.location.assign(payload.login_url);
            return;
          }
          throw new Error(payload.message || "No pudimos actualizar favoritos.");
        }
        liveRegion.textContent = payload.message || "Favoritos actualizados.";
        updateFavoriteBadges(Number(payload.favorite_count) || 0);
        syncMatchingForms(productSlug || payload.product_slug, Boolean(payload.is_favorite));
        if (!payload.is_favorite) {
          const shell = form.closest("[data-favorite-card]");
          if (shell) {
            shell.remove();
          }
        }
        window.lucide?.createIcons?.();
      } catch (error) {
        liveRegion.textContent = error.message || "No pudimos actualizar favoritos.";
      } finally {
        submitter?.removeAttribute("aria-busy");
        submitter?.removeAttribute("disabled");
      }
    });
  });
});
