const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

global.document = { querySelector() { return null; } };
const scriptPath = path.resolve(__dirname, "../../app/static/js/partner-product-draft.js");
const script = fs.readFileSync(scriptPath, "utf8");
require(scriptPath);

const {
  conditionChangePlan,
  conditionApplies,
  dependentFieldKeys,
  eligibleAxes,
  isDefaultAxis,
  resolvedTriggerValue,
} = global.EcuvelPartnerVariantConditions;

const computerCondition = {
  field: "tipo_equipo",
  values: ["Laptop", "Desktop", "Tablet"],
};
const valueFrom = (attributes) => (key) => attributes[key];

assert.equal(conditionApplies(null, valueFrom({})), true);
assert.equal(
  conditionApplies(computerCondition, valueFrom({ tipo_equipo: "Laptop" })),
  true,
);
assert.equal(
  conditionApplies(computerCondition, valueFrom({ tipo_equipo: "Monitor" })),
  false,
);
assert.equal(conditionApplies(computerCondition, valueFrom({})), false);
assert.equal(
  isDefaultAxis(
    { condition: computerCondition, default_for: ["Laptop"] },
    valueFrom({ tipo_equipo: "Laptop" }),
  ),
  true,
);
assert.equal(
  isDefaultAxis(
    { condition: computerCondition, default_for: ["Laptop"] },
    valueFrom({ tipo_equipo: "Monitor" }),
  ),
  false,
);

const computerCatalog = [
  { key: "color", label: "Color", condition: null },
  { key: "ram", label: "RAM", condition: computerCondition },
  { key: "almacenamiento", label: "Almacenamiento", condition: computerCondition },
  {
    key: "tamano",
    label: "Tamaño",
    condition: { field: "tipo_equipo", values: ["Monitor"] },
  },
];
const eligibleKeys = (attributes) => eligibleAxes(
  computerCatalog,
  valueFrom(attributes),
).map((axis) => axis.key);

assert.deepEqual(
  eligibleKeys({ tipo_equipo: "Laptop" }),
  ["color", "ram", "almacenamiento"],
);
assert.deepEqual(
  eligibleKeys({ tipo_equipo: "Monitor" }),
  ["color", "tamano"],
);

const syntheticCondition = {
  field: "clase_producto",
  values: ["A"],
};
assert.equal(
  conditionApplies(syntheticCondition, valueFrom({ clase_producto: "A" })),
  true,
);
assert.equal(
  conditionApplies(syntheticCondition, valueFrom({ clase_producto: "B" })),
  false,
);

const computerFields = [
  { key: "tipo_equipo", condition: null },
  { key: "color_principal", condition: null },
  { key: "material", condition: null },
  { key: "ram_gb", condition: computerCondition },
  { key: "almacenamiento_gb", condition: computerCondition },
  { key: "sistema_operativo", condition: computerCondition },
  {
    key: "pantalla_pulgadas",
    condition: { field: "tipo_equipo", values: ["Laptop", "Tablet", "Monitor"] },
  },
];
assert.deepEqual(
  dependentFieldKeys(computerFields, "tipo_equipo"),
  ["ram_gb", "almacenamiento_gb", "sistema_operativo", "pantalla_pulgadas"],
);
assert.equal(dependentFieldKeys(computerFields, "tipo_equipo").includes("color_principal"), false);
assert.equal(dependentFieldKeys(computerFields, "tipo_equipo").includes("material"), false);
assert.equal(resolvedTriggerValue("Laptop", "Desktop", false), "Laptop");
assert.equal(resolvedTriggerValue("Laptop", "Desktop", true), "Desktop");

assert.deepEqual(
  conditionChangePlan({
    previousValue: "Laptop",
    currentValue: "Monitor",
    wasVariantModeActive: true,
    invalidAxisCount: 0,
    hasVariantData: false,
  }),
  { changed: true, shouldConfirm: false, shouldDeactivate: true },
);
assert.deepEqual(
  conditionChangePlan({
    previousValue: "Laptop",
    currentValue: "Monitor",
    wasVariantModeActive: true,
    invalidAxisCount: 2,
    hasVariantData: true,
  }),
  { changed: true, shouldConfirm: true, shouldDeactivate: true },
);
assert.deepEqual(
  conditionChangePlan({
    previousValue: "Laptop",
    currentValue: "Laptop",
    wasVariantModeActive: true,
    invalidAxisCount: 2,
    hasVariantData: true,
  }),
  { changed: false, shouldConfirm: false, shouldDeactivate: false },
);
assert.deepEqual(
  conditionChangePlan({
    previousValue: "Laptop",
    currentValue: "Desktop",
    wasVariantModeActive: false,
    invalidAxisCount: 0,
    hasVariantData: false,
    dependentValueCount: 4,
  }),
  { changed: true, shouldConfirm: true, shouldDeactivate: false },
);

const triggerState = { tipo_equipo: "Laptop" };
const previousTriggerValue = triggerState.tipo_equipo;
triggerState.tipo_equipo = "Monitor";
assert.deepEqual(eligibleKeys(triggerState), ["color", "tamano"]);
triggerState.tipo_equipo = previousTriggerValue;
assert.equal(triggerState.tipo_equipo, "Laptop");
assert.deepEqual(
  eligibleKeys(triggerState),
  ["color", "ram", "almacenamiento"],
);

assert.equal(script.includes("Ã"), false);
assert.equal(script.includes("Â"), false);
assert.match(
  script,
  /title: "¿Descartar este borrador\?"/,
);
assert.match(script, /await requestProductDraftConfirmation\(/);
assert.match(script, /dependentFields\.forEach\(\(entry\) => \{/);
assert.match(
  script,
  /toggle\.checked = false;\s+deactivateVariantMode\(\{[\s\S]*?alreadyConfirmed: true,[\s\S]*?skipSourceFields:/,
);

console.log("partner product variant conditions: ok");
