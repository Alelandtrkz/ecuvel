const assert = require("node:assert/strict");
const path = require("node:path");

require(path.resolve(__dirname, "../../app/static/js/favorites.js"));

const activeClasses = new Set();
const attributes = new Map();
const text = { textContent: "Guardar en favoritos" };
const button = {
  classList: {
    toggle(name, enabled) {
      if (enabled) activeClasses.add(name);
      else activeClasses.delete(name);
    },
  },
  setAttribute(name, value) {
    attributes.set(name, value);
  },
  getAttribute(name) {
    return attributes.get(name) || "";
  },
  querySelector(selector) {
    return selector === "span" ? text : null;
  },
};

attributes.set("aria-label", "Añadir a favoritos");

globalThis.EcuvelFavoriteState.applyButtonState(button, true);
assert.equal(activeClasses.has("is-active"), true);
assert.equal(attributes.get("aria-pressed"), "true");
assert.equal(attributes.get("aria-label"), "Eliminar de favoritos");
assert.equal(text.textContent, "Guardado en favoritos");

globalThis.EcuvelFavoriteState.applyButtonState(button, false);
assert.equal(activeClasses.has("is-active"), false);
assert.equal(attributes.get("aria-pressed"), "false");
assert.equal(attributes.get("aria-label"), "Añadir a favoritos");
assert.equal(text.textContent, "Guardar en favoritos");

console.log("favorite state tests passed");
