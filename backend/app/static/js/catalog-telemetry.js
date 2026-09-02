document.addEventListener("DOMContentLoaded", () => {
  const cards = Array.from(
    document.querySelectorAll("[data-product-card][data-ranking-context]"),
  );
  if (!cards.length || !window.fetch) return;

  const csrfToken = document.querySelector("input[name='csrf_token']")?.value || "";
  const sentImpressions = new Set();

  const sendEvent = (card, eventType) => {
    const rankingContext = card.dataset.rankingContext;
    if (!rankingContext) return;
    const body = new FormData();
    body.set("csrf_token", csrfToken);
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

  if ("IntersectionObserver" in window) {
    const timers = new WeakMap();
    const observer = new IntersectionObserver((entries) => {
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
    }, { threshold: [0.5] });
    cards.forEach((card) => observer.observe(card));
  }

  cards.forEach((card) => {
    card.querySelectorAll("a[href]").forEach((link) => {
      link.addEventListener("click", () => sendEvent(card, "CLICK"));
    });
  });
});
