const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

global.document = { querySelector() { return null; } };
const scriptPath = path.resolve(__dirname, "../../app/static/js/partner-product-draft.js");
require(scriptPath);
const script = fs.readFileSync(scriptPath, "utf8");

const { estimate } = global.EcuvelPartnerCommissionPolicy;
const policy = (rate) => ({
  available: true,
  minimum_price: "0.25",
  minimum_commission: "0.25",
  rate_percent: String(rate),
});

assert.equal(estimate(policy(6), "0.25"), null);

for (const [price, amount] of [
  ["0.26", 0.25],
  ["1.00", 0.25],
  ["2.99", 0.25],
  ["3.00", 0.25],
  ["3.01", 0.25],
  ["3.25", 0.25],
  ["4.00", 0.25],
]) {
  const result = estimate(policy(6), price);
  assert.equal(result.mode, "MINIMUM");
  assert.equal(result.amount, amount);
  assert.equal(result.rate, 6);
}

assert.deepEqual(
  estimate(policy(6), "5.00"),
  {
    mode: "PERCENTAGE",
    label: "6% / $0.30",
    amount: 0.3,
    net: 4.7,
    percentageAmount: 0.3,
    rate: 6,
  },
);

for (const price of ["3.00", "3.01"]) {
  const result = estimate(policy(8), price);
  assert.equal(result.mode, "MINIMUM");
  assert.equal(result.amount, 0.25);
  assert.equal(result.rate, 8);
}

assert.deepEqual(
  estimate(policy(8), "3.25"),
  {
    mode: "PERCENTAGE",
    label: "8% / $0.26",
    amount: 0.26,
    net: 2.99,
    percentageAmount: 0.26,
    rate: 8,
  },
);

assert.deepEqual(
  estimate({ ...policy(6), available: false }, "5.00"),
  { mode: "MISSING" },
);

assert.match(script, /Tarifa mínima ECUVEL/);
assert.match(script, /La comisión de la categoría es/);
assert.equal(script.includes("USD 3.00"), false);
assert.equal(script.includes("threshold"), false);

console.log("partner product commission policy: ok");
