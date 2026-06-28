(function () {
  const timers = Array.from(document.querySelectorAll("[data-payment-countdown]"));
  if (!timers.length) return;

  const formatSeconds = (seconds) => {
    const safe = Math.max(0, seconds);
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const remainingSeconds = safe % 60;
    if (hours > 0) {
      return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
    }
    return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
  };

  let reloaded = false;
  const tick = () => {
    const now = Date.now();
    let hasActiveTimer = false;

    for (const timer of timers) {
      const expiresAt = Date.parse(timer.dataset.expiresAt || "");
      const value = timer.querySelector("[data-payment-countdown-value]");
      if (!Number.isFinite(expiresAt) || !value) continue;

      const remaining = Math.ceil((expiresAt - now) / 1000);
      value.textContent = remaining <= 0 ? "Tiempo vencido" : formatSeconds(remaining);
      timer.classList.toggle("is-expired", remaining <= 0);
      if (remaining > 0) {
        hasActiveTimer = true;
        continue;
      }

      for (const form of document.querySelectorAll("[data-expiring-action]")) {
        for (const control of form.querySelectorAll("button, input, select, textarea")) {
          control.disabled = true;
        }
        form.setAttribute("aria-disabled", "true");
      }
    }

    if (!hasActiveTimer && !reloaded) {
      reloaded = true;
      window.setTimeout(() => window.location.reload(), 2500);
    }
  };

  tick();
  window.setInterval(tick, 1000);
})();
