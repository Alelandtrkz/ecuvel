const assert = require("node:assert/strict");
const path = require("node:path");

global.document = { querySelector() { return null; } };
const scriptPath = path.resolve(__dirname, "../../app/static/js/partner-product-draft.js");
require(scriptPath);

const {
  normalizeChipValues,
  parseConditionValues,
  quickOptionField,
  quickOptionState,
} = global.EcuvelPartnerFieldState;

assert.deepEqual(
  parseConditionValues('["Laptop, Workstation", "Desktop"]'),
  ["Laptop, Workstation", "Desktop"],
);
assert.deepEqual(parseConditionValues("not-json"), []);
assert.deepEqual(parseConditionValues('{"values":["Desktop"]}'), []);
assert.deepEqual(parseConditionValues('["Desktop", null]'), []);

assert.deepEqual(
  normalizeChipValues([
    " Lavar a mano ",
    "",
    "Agua fría, ciclo suave",
    "Lavar a mano",
    "   ",
    "Secar a la sombra",
  ]),
  ["Lavar a mano", "Agua fría, ciclo suave", "Secar a la sombra"],
);
assert.deepEqual(normalizeChipValues("Lavar a mano, Agua fría"), []);

assert.deepEqual(
  quickOptionState({ value: "100", unit: "g", currentUnit: "kg" }),
  { value: "100", unit: "g" },
);
assert.deepEqual(
  quickOptionState({ value: "12", unit: "meses", currentUnit: "días" }),
  { value: "12", unit: "meses" },
);
assert.deepEqual(
  quickOptionState({ value: "16", currentUnit: "GB" }),
  { value: "16", unit: "GB" },
);

const genericField = {};
let requestedSelector = "";
assert.equal(
  quickOptionField({
    closest(selector) {
      requestedSelector = selector;
      return genericField;
    },
  }),
  genericField,
);
assert.equal(requestedSelector, ".partner-draft-field");

console.log("partner product field state: ok");
