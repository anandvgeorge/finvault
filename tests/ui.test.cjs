// Shared dropdown behaviour with fictional options and a DOM substitute; no dashboard or network.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function element() {
    return {
        ownerDocument: { createElement: () => element() },
        dataset: {}, attributes: new Map(), children: [], text: "",
        get textContent() { return this.text + this.children.map(child => child.textContent).join(""); },
        set textContent(value) { this.text = String(value); this.children = []; },
        setAttribute(name, value) { this.attributes.set(name, value); },
        getAttribute(name) { return this.attributes.get(name); },
        replaceChildren(...children) { this.text = ""; this.children = children; },
        appendChild(child) { this.children.push(child); },
    };
}

function categoryMenu(options = {}, viewport = { width: 375, height: 844 },
    trigger = { left: 200, top: 780, bottom: 816, width: 160 }) {
    const listeners = new Map();
    const document = {
        addEventListener(name, handler) { listeners.set(name, handler); },
        defaultView: {
            innerWidth: viewport.width, innerHeight: viewport.height,
            addEventListener(name, handler) { listeners.set(name, handler); },
            Event: class { constructor(type) { this.type = type; } },
        },
        createElement() {
            const node = element();
            Object.assign(node, {
                style: {}, scrollTop: 0, scrollHeight: 240, clientHeight: 240,
                offsetTop: 0, offsetHeight: 36,
                removeAttribute(name) { this.attributes.delete(name); },
                appendChild(child) { child.parentNode = this; this.children.push(child); },
                contains(target) { return this === target || this.children.some(child => child.contains(target)); },
                focus() {},
                // Simulate the popup's constrained box, not a browser layout engine.
                getBoundingClientRect() {
                    const left = parseFloat(this.style.left) || 0;
                    const top = parseFloat(this.style.top) || 0;
                    const width = parseFloat(this.style.width) || 0;
                    const height = this.style.maxHeight
                        ? Math.min(this.scrollHeight, parseFloat(this.style.maxHeight)) : this.scrollHeight;
                    return { left, top, width, height, right: left + width, bottom: top + height };
                },
            });
            return node;
        },
    };
    document.body = document.createElement();
    const select = document.createElement();
    select.options = ["Uncategorized", "Necessary", "Lifestyle", "Investment", "Salary", "Other"]
        .map(textContent => ({ textContent, value: textContent.toLowerCase() }));
    select.selectedIndex = 0;
    select.dispatchEvent = event => { select.lastEvent = event.type; };
    select.setAttribute("aria-label", "Category for Example Shop");
    document.body.appendChild(select);
    const context = vm.createContext({});
    vm.runInContext(readFileSync(resolve(__dirname, "../src/finvault/api/static/ui.js"), "utf8"), context);
    const CategoryMenu = vm.runInContext("CategoryMenu", context);
    const menu = new CategoryMenu(document, options);
    const button = menu.enhance(select);
    button.getBoundingClientRect = () => trigger;
    const key = (key, extra = {}) => button.onkeydown({ key, preventDefault() {}, ...extra });
    const option = label => menu.menu.children.find(node => node.textContent === label);
    const active = () => menu.menu.children.find(node => node.id === button.getAttribute("aria-activedescendant"));
    const selectedValue = () => select.options[select.selectedIndex].value;
    return { menu, button, select, listeners, key, option, active, selectedValue };
}

test("shared menus support rule-specific IDs and triggers without changing selection events", () => {
    const { menu, button, select, key, active, selectedValue } = categoryMenu({ id: "rule-menu", triggerClass: "rule-trigger" });
    assert.equal(menu.menu.id, "rule-menu");
    assert.equal(button.className, "rule-trigger");
    button.onclick();
    key("End");
    assert.equal(button.getAttribute("aria-controls"), menu.menu.id);
    assert.equal(active().textContent, "Other");
    key("Enter");
    assert.equal(selectedValue(), "other");
    assert.equal(select.lastEvent, "change");
    assert.equal(button.textContent, "Other");
});

test("category menu previews keyboard choices, Escape cancels, and Enter commits", () => {
    const { menu, button, select, key, active, selectedValue } = categoryMenu();
    assert.equal(select.hidden, true);
    assert.equal(button.textContent, "Uncategorized");
    assert.equal(button.getAttribute("role"), "combobox");
    key("ArrowDown");
    assert.equal(button.getAttribute("aria-expanded"), "true");
    assert.equal(active().textContent, "Uncategorized");
    key("ArrowDown");
    assert.equal(active().textContent, "Necessary");
    assert.equal(selectedValue(), "uncategorized");
    key("Escape");
    assert.equal(button.textContent, "Uncategorized");
    assert.equal(button.getAttribute("aria-activedescendant"), undefined);
    key("End");
    key("Enter");
    assert.equal(selectedValue(), "other");
    assert.equal(button.textContent, "Other");
    assert.equal(select.lastEvent, "change");
    assert.equal(menu.menu.hidden, true);
});

test("category menu supports typeahead, Tab, pointer selection, and safe option labels", () => {
    const { button, select, key, option, active, selectedValue } = categoryMenu();
    key("l");
    assert.equal(active().textContent, "Lifestyle");
    key("Tab");
    assert.equal(button.textContent, "Lifestyle");
    select.options.push({ value: "custom", textContent: "Example <img src=x> category" });
    button.onclick();
    const custom = option("Example <img src=x> category");
    assert.equal(custom.children.length, 0);
    custom.onclick();
    assert.equal(button.textContent, "Example <img src=x> category");
    assert.equal(selectedValue(), "custom");
});

test("category menu stays within the viewport and closes on outside click, scrolling, or resize", () => {
    for (const [viewport, trigger] of [
        [{ width: 375, height: 844 }, { left: 20, top: 100, bottom: 136, width: 160 }],
        [{ width: 375, height: 844 }, { left: 200, top: 780, bottom: 816, width: 160 }],
        [{ width: 375, height: 844 }, { left: 300, top: 300, bottom: 336, width: 160 }],
        [{ width: 140, height: 180 }, { left: 8, top: 70, bottom: 106, width: 160 }],
    ]) {
        const { menu, button } = categoryMenu({}, viewport, trigger);
        button.onclick();
        const box = menu.menu.getBoundingClientRect();
        assert.equal(box.width, Math.min(trigger.width, viewport.width - 16));
        assert.ok(box.left >= 8 && box.right <= viewport.width - 8, "popup fits horizontally");
        assert.ok(box.top >= 0 && box.bottom <= viewport.height, "popup fits vertically");
        assert.ok(box.bottom <= trigger.top || box.top >= trigger.bottom, "popup does not cover its trigger");
    }
    const { menu, button, listeners, option } = categoryMenu();
    button.onclick();
    listeners.get("pointerdown")({ target: option("Uncategorized") });
    assert.equal(menu.menu.hidden, false);
    listeners.get("pointerdown")({ target: {} });
    assert.equal(menu.menu.hidden, true);
    button.onclick();
    listeners.get("scroll")({ target: menu.menu });
    assert.equal(menu.menu.hidden, false);
    listeners.get("scroll")({ target: {} });
    assert.equal(menu.menu.hidden, true);
    button.onclick();
    listeners.get("resize")();
    assert.equal(menu.menu.hidden, true);
});

test("saving category controls keep keyboard focus but cannot open another menu", () => {
    const { menu, button, key } = categoryMenu();
    button.setAttribute("aria-disabled", "true");
    button.onclick();
    key("Enter");
    assert.equal(menu.menu.hidden, true);
    let blocked = false;
    button.onkeydown({ key: "Tab", preventDefault() { blocked = true; } });
    assert.equal(blocked, false);
    button.setAttribute("aria-disabled", "false");
    key("Enter");
    assert.equal(menu.menu.hidden, false);
});

test("category menu closes before its change handler runs, and unchanged choices send no event", () => {
    const { menu, button, select, option, selectedValue } = categoryMenu();
    let changes = 0;
    select.dispatchEvent = () => {
        changes++;
        assert.equal(button.getAttribute("aria-expanded"), "false");
        assert.equal(menu.menu.hidden, true);
        button.onblur();
    };
    button.onclick();
    option("Uncategorized").onclick();
    assert.equal(changes, 0);
    button.onclick();
    option("Necessary").onclick();
    assert.equal(changes, 1);
    assert.equal(selectedValue(), "necessary");
});

