const assert = require("node:assert/strict");
const path = require("node:path");
const { performance } = require("node:perf_hooks");

global.document = { addEventListener() {} };
require(path.resolve(__dirname, "../../app/static/js/product-detail.js"));

const {
  applyPresentation,
  createIndex,
  initialVariant,
  optionState,
  quantityState,
  resolveVariant,
  variantUrl,
  variantViewState,
} = global.EcuvelVariantState;

const axes = [
  {
    key: "color",
    values: [
      { key: "black", label: "Negro" },
      { key: "blue", label: "Azul" },
      { key: "green", label: "Verde" },
    ],
  },
  {
    key: "ram",
    values: [
      { key: "8", label: "8" },
      { key: "16", label: "16" },
      { key: "32", label: "32" },
    ],
  },
  {
    key: "storage",
    values: [
      { key: "256", label: "256" },
      { key: "512", label: "512" },
    ],
  },
];

const variants = [
  {
    catalog_sku: "BLACK-8-256",
    attributes: { color: "black", ram: "8", storage: "256" },
    price: "900.00",
    is_available: true,
  },
  {
    catalog_sku: "BLUE-16-512-SOLD",
    attributes: { color: "Azul", ram: "16", storage: "512" },
    price: "1300.00",
    is_available: false,
  },
  {
    catalog_sku: "BLUE-32-512",
    attributes: { color: "blue", ram: "32", storage: "512" },
    price: "700.00",
    is_available: true,
  },
];

const index = createIndex(axes, variants);
assert.equal(initialVariant(index, "BLACK-8-256"), variants[0]);
assert.equal(initialVariant(index, "missing"), variants[0]);

const blackValues = index.valuesByVariant.get(variants[0]);
assert.equal(resolveVariant(index, blackValues, "storage", "256"), variants[0]);
assert.equal(
  resolveVariant(index, blackValues, "color", "blue"),
  variants[1],
  "recovery preserves the clicked value and payload order, not stock or price",
);
assert.equal(resolveVariant(index, blackValues, "color", "green"), null);

assert.deepEqual(optionState(index, "color", "green"), {
  disabled: true,
  outOfStock: false,
});
assert.deepEqual(optionState(index, "ram", "16"), {
  disabled: false,
  outOfStock: true,
});
assert.deepEqual(optionState(index, "color", "blue"), {
  disabled: false,
  outOfStock: false,
});

assert.deepEqual(quantityState(7, 3), { value: 3, maximum: 3, disabled: false });
assert.deepEqual(quantityState(0, 8), { value: 1, maximum: 8, disabled: false });
assert.deepEqual(quantityState(4, 0), { value: 1, maximum: 0, disabled: true });

const url = variantUrl(
  "https://ecuvel.test/productos/telefono?ref=home#specs",
  "BLUE-16-512-SOLD",
);
assert.equal(url.searchParams.get("variant"), "BLUE-16-512-SOLD");
assert.equal(url.searchParams.get("ref"), "home");
assert.equal(url.hash, "#specs");

const money = (amount, currency) => (amount ? `${currency} ${amount}` : "");
const inStockView = variantViewState({
  price: "1200.00",
  compare_at_price: "1400.00",
  currency: "USD",
  is_available: true,
  availability_label: "Disponible",
  delivery_label: "Entrega estimada mañana",
  offer_id: "offer-a",
  availability_message: "Disponible",
  low_stock: false,
  max_quantity: 10,
}, money);
const soldOutView = variantViewState({
  price: "900.00",
  compare_at_price: null,
  currency: "USD",
  is_available: false,
  availability_label: "Producto agotado",
  delivery_label: "Información de entrega próximamente",
  offer_id: "offer-b",
  availability_message: "Producto agotado",
  low_stock: false,
  max_quantity: 0,
}, money);
assert.deepEqual(
  {
    price: inStockView.price,
    comparePrice: inStockView.comparePrice,
    compareHidden: inStockView.compareHidden,
    offerId: inStockView.offerId,
    maximum: inStockView.maximum,
  },
  {
    price: "USD 1200.00",
    comparePrice: "USD 1400.00",
    compareHidden: false,
    offerId: "offer-a",
    maximum: 10,
  },
);
assert.equal(soldOutView.comparePrice, "");
assert.equal(soldOutView.compareHidden, true);
assert.equal(soldOutView.available, false);
assert.equal(soldOutView.offerId, "offer-b");
assert.equal(soldOutView.maximum, 0);
assert.equal(soldOutView.deliveryLabel, "Información de entrega próximamente");

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.className = "";
    this.id = "";
    this.textContent = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = [...children];
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

const summaryRoot = new FakeElement("div");
const specificationsRoot = new FakeElement("div");
const fakeDocument = {
  createElement(tagName) {
    return new FakeElement(tagName);
  },
  querySelector(selector) {
    if (selector === "[data-product-summary-content]") return summaryRoot;
    if (selector === "[data-product-specifications-content]") return specificationsRoot;
    return null;
  },
};

const variantA = {
  public_summary: [{ label: "RAM", value: "8 GB" }],
  public_specifications: [
    { label: "Memoria", value: "8 GB", kind: "text", list_items: [] },
    {
      label: "Garantía",
      value: "Garantía de tienda · 12 meses",
      kind: "multiline",
      list_items: ["Responsable: ECUVEL"],
    },
    {
      label: "Contenido del paquete",
      value: "",
      kind: "list",
      list_items: ["Teléfono", "Cable USB-C"],
    },
  ],
  public_seller_highlights: ["Carga rápida"],
};
const attack = '<img src=x onerror="globalThis.pwned=true">';
const variantB = {
  public_summary: [{ label: "RAM", value: attack }],
  public_specifications: [],
  public_seller_highlights: [],
};

applyPresentation(fakeDocument, variantA);
assert.equal(summaryRoot.children[0].tagName, "DL");
assert.equal(specificationsRoot.children[0].className, "product-specs product-specs--buyer");
assert.equal(specificationsRoot.children[0].children[1].children[1].className, "product-specification-warranty");
assert.equal(specificationsRoot.children[0].children[2].children[1].children[0].tagName, "UL");
assert.equal(specificationsRoot.children[1].tagName, "SECTION");

applyPresentation(fakeDocument, variantB);
assert.equal(summaryRoot.children[0].children[0].children[1].textContent, attack);
assert.equal(summaryRoot.children[0].children[0].children[1].children.length, 0);
assert.equal(specificationsRoot.children[0].className, "detail-empty-state");
assert.equal(globalThis.pwned, undefined);

applyPresentation(fakeDocument, variantA);
assert.equal(summaryRoot.children[0].children[0].children[1].textContent, "8 GB");
assert.equal(specificationsRoot.children.length, 2, "A → B → A clears and restores every node");

const highCardinalityAxes = [
  { key: "color", values: Array.from({ length: 5 }, (_, i) => ({ key: `c${i}`, label: `C${i}` })) },
  { key: "size", values: Array.from({ length: 12 }, (_, i) => ({ key: `s${i}`, label: `S${i}` })) },
];
const highCardinalityVariants = [];
for (let color = 0; color < 5; color += 1) {
  for (let size = 0; size < 12; size += 1) {
    highCardinalityVariants.push({
      catalog_sku: `C${color}-S${size}`,
      attributes: { color: `c${color}`, size: `s${size}` },
      is_available: true,
    });
  }
}
const started = performance.now();
const highCardinalityIndex = createIndex(highCardinalityAxes, highCardinalityVariants);
for (let iteration = 0; iteration < 1000; iteration += 1) {
  const current = highCardinalityIndex.valuesByVariant.get(
    highCardinalityVariants[iteration % highCardinalityVariants.length],
  );
  assert.ok(resolveVariant(highCardinalityIndex, current, "size", `s${iteration % 12}`));
}
const elapsed = performance.now() - started;
assert.ok(elapsed < 1000, `high-cardinality selection took ${elapsed.toFixed(2)} ms`);

console.log(`product variant state: ok (${elapsed.toFixed(2)} ms for 60 variants / 1000 resolutions)`);
