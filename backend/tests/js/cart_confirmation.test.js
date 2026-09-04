const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const scriptPath = path.resolve(__dirname, "../../app/static/js/cart.js");
const script = fs.readFileSync(scriptPath, "utf8");
assert.equal(script.includes("window.confirm"), false);
require(scriptPath);

const eventTarget = () => {
  const listeners = new Map();
  return {
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(listener);
    },
    dispatch(type, event = {}) {
      (listeners.get(type) || []).forEach((listener) => listener(event));
    },
  };
};

const root = eventTarget();
const cancelButton = eventTarget();
const confirmButton = eventTarget();
const title = { textContent: "" };
const message = { textContent: "" };
const dialogEvents = eventTarget();
const dialog = {
  ...dialogEvents,
  open: false,
  querySelector(selector) {
    return {
      "[data-cart-delete-title]": title,
      "[data-cart-delete-message]": message,
      "[data-cart-delete-cancel]": cancelButton,
      "[data-cart-delete-confirm]": confirmButton,
    }[selector] || null;
  },
  showModal() {
    this.open = true;
  },
  close() {
    this.open = false;
    this.dispatch("close");
  },
};

const submissionEvent = (form, submitter) => {
  const event = {
    target: { closest: () => form },
    submitter,
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
  root.dispatch("submit", event);
  return event;
};

const createForm = (confirmTitle, confirmMessage) => {
  const submitter = {
    focusCount: 0,
    focus() {
      this.focusCount += 1;
    },
  };
  const form = {
    dataset: { confirmTitle, confirmMessage },
    requestCount: 0,
    acceptedCount: 0,
    querySelector: () => submitter,
    requestSubmit(requestedSubmitter) {
      assert.equal(requestedSubmitter, submitter);
      this.requestCount += 1;
      const event = submissionEvent(this, requestedSubmitter);
      if (!event.defaultPrevented) this.acceptedCount += 1;
    },
  };
  return { form, submitter };
};

globalThis.EcuvelCartConfirmation.createDeleteConfirmation(root, dialog);

const line = createForm(
  "¿Eliminar este producto del carrito?",
  "Esta acción quitará el producto de tu carrito.",
);
let event = submissionEvent(line.form, line.submitter);
assert.equal(event.defaultPrevented, true);
assert.equal(dialog.open, true);
assert.equal(title.textContent, "¿Eliminar este producto del carrito?");
assert.equal(message.textContent, "Esta acción quitará el producto de tu carrito.");

cancelButton.dispatch("click");
assert.equal(dialog.open, false);
assert.equal(line.form.requestCount, 0);
assert.equal(line.submitter.focusCount, 1);

event = submissionEvent(line.form, line.submitter);
assert.equal(event.defaultPrevented, true);
const escapeEvent = {
  defaultPrevented: false,
  preventDefault() {
    this.defaultPrevented = true;
  },
};
dialog.dispatch("cancel", escapeEvent);
assert.equal(escapeEvent.defaultPrevented, true);
assert.equal(dialog.open, false);
assert.equal(line.form.requestCount, 0);
assert.equal(line.submitter.focusCount, 2);

submissionEvent(line.form, line.submitter);
confirmButton.dispatch("click");
assert.equal(dialog.open, false);
assert.equal(line.form.requestCount, 1);
assert.equal(line.form.acceptedCount, 1);

const selected = createForm(
  "¿Eliminar los productos seleccionados del carrito?",
  "Esta acción quitará los productos seleccionados de tu carrito.",
);
submissionEvent(selected.form, selected.submitter);
assert.equal(dialog.open, true);
assert.equal(title.textContent, "¿Eliminar los productos seleccionados del carrito?");
assert.equal(
  message.textContent,
  "Esta acción quitará los productos seleccionados de tu carrito.",
);
cancelButton.dispatch("click");
assert.equal(selected.form.requestCount, 0);

console.log("cart confirmation tests passed");
