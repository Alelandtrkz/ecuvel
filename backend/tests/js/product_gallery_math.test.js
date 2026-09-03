const assert = require("node:assert/strict");
const path = require("node:path");

global.document = { addEventListener() {} };
require(path.resolve(__dirname, "../../app/static/js/product-detail.js"));

const { clamp, fitMetrics, panBounds, pointerDistance } = global.EcuvelGalleryMath;

const small = fitMetrics(447, 447, 736, 736);
assert.equal(small.fittedWidth, 447);
assert.equal(small.fittedHeight, 447);
assert.equal(small.maxZoom, 1);

const medium = fitMetrics(1000, 1000, 700, 700);
assert.equal(medium.fittedWidth, 700);
assert.ok(Math.abs(medium.maxZoom - (10 / 7)) < 0.0001);

const large = fitMetrics(2000, 1500, 800, 600);
assert.equal(large.maxZoom, 2.5);
assert.equal(fitMetrics(10000, 10000, 500, 500).maxZoom, 4);

assert.deepEqual(panBounds(447, 447, 1, 736, 736), { x: 0, y: 0 });
assert.deepEqual(panBounds(800, 600, 2, 800, 600), { x: 400, y: 300 });
assert.equal(pointerDistance({ x: 0, y: 0 }, { x: 3, y: 4 }), 5);
assert.equal(clamp(Number.NaN, 1, 4), 1);
assert.equal(clamp(9, 1, 4), 4);

console.log("product gallery math: ok");
