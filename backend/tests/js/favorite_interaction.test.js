const assert = require("node:assert/strict");
const path = require("node:path");

const listeners = new Map();
const activeClasses = new Set();
const attributes = new Map([
  ["aria-label", "Añadir Product Test a favoritos"],
  ["aria-pressed", "false"],
]);
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
  removeAttribute(name) {
    attributes.delete(name);
  },
  getAttribute(name) {
    return attributes.get(name) || "";
  },
  querySelector: () => null,
};
const form = {
  action: "/favoritos/productos/product-test/agregar",
  dataset: { productSlug: "product-test" },
  querySelector: () => button,
  closest: () => null,
};
const badge = { textContent: "", hidden: true };
const liveRegion = {
  className: "",
  setAttribute() {},
  textContent: "",
};

globalThis.window = globalThis;
globalThis.CSS = { escape: (value) => value };
globalThis.FormData = class FormData {};
globalThis.document = {
  body: { append() {} },
  createElement: () => liveRegion,
  addEventListener(type, listener) {
    listeners.set(type, listener);
  },
  querySelectorAll(selector) {
    if (selector === "[data-favorite-count]") return [badge];
    if (selector.includes("data-product-slug")) return [form];
    return [];
  },
};
globalThis.EcuvelIcons = { refresh() {} };

require(path.resolve(__dirname, "../../app/static/js/favorites.js"));
listeners.get("DOMContentLoaded")();

const payloads = [
  { ok: true, is_favorite: true, favorite_count: 1 },
  { ok: true, is_favorite: false, favorite_count: 0 },
];
globalThis.fetch = async () => ({
  ok: true,
  json: async () => payloads.shift(),
});

const submit = async () => {
  let prevented = false;
  await listeners.get("submit")({
    target: { closest: () => form },
    submitter: button,
    preventDefault() {
      prevented = true;
    },
  });
  assert.equal(prevented, true);
};

(async () => {
  await submit();
  assert.equal(activeClasses.has("is-active"), true);
  assert.equal(attributes.get("aria-pressed"), "true");
  assert.equal(form.action.endsWith("/eliminar"), true);
  assert.equal(badge.textContent, "1");
  assert.equal(badge.hidden, false);

  await submit();
  assert.equal(activeClasses.has("is-active"), false);
  assert.equal(attributes.get("aria-pressed"), "false");
  assert.equal(form.action.endsWith("/agregar"), true);
  assert.equal(badge.textContent, "0");
  assert.equal(badge.hidden, true);

  console.log("favorite interaction tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
