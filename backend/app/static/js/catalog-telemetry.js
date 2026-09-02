document.addEventListener("DOMContentLoaded", () => {
  if (!window.fetch) return;

  const sentImpressions = new Set();
  const timers = new WeakMap();
  const csrfToken = () => document.querySelector("input[name='csrf_token']")?.value || "";
  const sendEvent = (card, eventType) => {
    const rankingContext = card?.dataset.rankingContext;
    if (!rankingContext) return;
    const body = new FormData();
    body.set("csrf_token", csrfToken());
    body.set("event_type", eventType);
    body.set("ranking_context", rankingContext);
    fetch("/catalogo/interacciones", {
      method: "POST",
      body,
      credentials: "same-origin",
      keepalive: true,
      headers: { Accept: "application/json" },
    }).catch(() => {});
  };

  const observer = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          const card = entry.target;
          const dedupeKey = card.dataset.rankingContext;
          if (!dedupeKey || sentImpressions.has(dedupeKey)) return;
          if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
            if (timers.has(card)) return;
            const timer = window.setTimeout(() => {
              sentImpressions.add(dedupeKey);
              timers.delete(card);
              observer.unobserve(card);
              sendEvent(card, "IMPRESSION");
            }, 250);
            timers.set(card, timer);
          } else if (timers.has(card)) {
            window.clearTimeout(timers.get(card));
            timers.delete(card);
          }
        });
      }, { threshold: [0.5] })
    : null;

  const observedCards = new WeakSet();
  const observe = (root = document) => {
    if (!observer) return;
    root.querySelectorAll?.("[data-product-card][data-ranking-context]").forEach((card) => {
      if (observedCards.has(card)) return;
      observedCards.add(card);
      observer.observe(card);
    });
  };

  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("[data-product-card][data-ranking-context] a[href]");
    if (link) sendEvent(link.closest("[data-product-card]"), "CLICK");
  });

  window.EcuvelCatalogTelemetry = { observe };
  observe(document);
});
