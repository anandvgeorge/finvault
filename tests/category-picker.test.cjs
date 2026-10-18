// Fictional category data and a small DOM substitute; no real settings or database.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function picker(selectedIndex = 1) {
    const listeners = new Map();
    const document = {
        addEventListener: (event, handler) => listeners.set(event, handler),
        defaultView: { innerWidth: 375, innerHeight: 800, addEventListener() {},
            Event: class { constructor(type) { this.type = type; } } },
        createElement() {
            const attributes = new Map(), names = new Set();
            return { children: [], style: {}, dataset: {}, hidden: false, scrollTop: 0,
                scrollHeight: 360, clientHeight: 280,
                classList: { add: name => names.add(name) },
                setAttribute: (name, value) => attributes.set(name, value),
                getAttribute: name => attributes.get(name), removeAttribute: name => attributes.delete(name),
                appendChild(child) { child.parentNode = this; this.children.push(child); },
                replaceChildren() { this.children = []; },
                contains(node) { return this === node || this.children.some(child => child.contains(node)); },
                getBoundingClientRect() { return { left: 200, top: 200, bottom: 240, width: 160, height: 40 }; },
                focus() { this.focused = true; },
            };
        },
    };
    document.body = document.createElement();
    const context = vm.createContext({});
    for (const filename of ["ui.js", "category-picker.js"]) {
        vm.runInContext(readFileSync(resolve(__dirname, `../src/finvault/api/static/${filename}`), "utf8"), context);
    }
    const { GroupedCategoryMenu, categoryPickerSections } = vm.runInContext("({GroupedCategoryMenu, categoryPickerSections})", context);
    const groups = [{ id: "needs", label: "Needs" }, { id: "leisure", label: "Leisure" }, { id: "empty", label: "Empty" }];
    const assignments = { zoo: "needs", beta: "needs", alpha: "needs", travel: "leisure" };
    const select = document.createElement();
    select.options = [["", "Uncategorized"], ["zoo", "Zoo"], ["beta", "beta"], ["alpha", "Alpha"],
        ["travel", "Travel"], ["refunds", "Refunds"], ["lend", "Lend"], ["removed", "Removed (unavailable)"]]
        .map(([value, textContent]) => ({ value, textContent }));
    select.selectedIndex = selectedIndex;
    select.setAttribute("aria-label", "Category for Example shop");
    const changes = [];
    select.dispatchEvent = event => changes.push([event.type, select.options[select.selectedIndex].value]);
    document.body.appendChild(select);
    const menu = new GroupedCategoryMenu(document);
    menu.configure(groups, assignments);
    const button = menu.enhance(select);
    const findById = (node, id) => node.id === id ? node
        : node.children.map(child => findById(child, id)).find(Boolean);
    return { menu, button, select, groups, assignments, changes, listeners, categoryPickerSections,
        selectedValue: () => select.options[select.selectedIndex].value,
        active: () => findById(menu.menu, button.getAttribute("aria-activedescendant")),
        key: (key, extra = {}) => button.onkeydown({ key, preventDefault() {}, ...extra }),
        group: id => menu.rows.find(row => row.kind === "group" && row.section.id === id),
        option: value => menu.rows.find(row => row.kind === "option" && select.options[row.index].value === value),
    };
}

test("picker sorts leaves alphabetically while preserving values and config ordering", () => {
    const f = picker();
    const before = JSON.stringify({ groups: f.groups, assignments: f.assignments, options: f.select.options });
    const structure = f.categoryPickerSections(f.select.options, f.groups, f.assignments);
    assert.deepEqual(Array.from(structure.sections[0].options, item => [item.label, f.select.options[item.index].value]),
        [["Alpha", "alpha"], ["beta", "beta"], ["Zoo", "zoo"]]);
    assert.deepEqual(Array.from(structure.sections.at(-1).options, item => item.label), ["Lend", "Refunds", "Removed (unavailable)"]);
    assert.equal(JSON.stringify({ groups: f.groups, assignments: f.assignments, options: f.select.options }), before);
});

test("opening expands the selected category's group; headers only expand or collapse", () => {
    const f = picker();
    f.button.onclick();
    assert.equal(f.button.getAttribute("aria-haspopup"), "tree");
    assert.equal(f.menu.menu.getAttribute("role"), "tree");
    assert.equal(f.group("needs").node.getAttribute("aria-expanded"), "true");
    assert.equal(f.option("zoo").node.getAttribute("aria-selected"), "true");
    assert.equal(f.button.getAttribute("aria-activedescendant"), f.option("zoo").node.id);
    f.group("leisure").display.onclick();
    assert.equal(f.group("needs").node.getAttribute("aria-expanded"), "false");
    assert.equal(f.group("leisure").node.getAttribute("aria-expanded"), "true");
    assert.equal(f.option("zoo"), undefined);
    f.group("leisure").display.onclick();
    assert.equal(f.menu.rows.some(row => row.kind === "option" && row.sectionKey), false);
    f.group("empty").display.onclick();
    assert.equal(f.group("empty").node.getAttribute("aria-expanded"), "true");
    assert.equal(f.selectedValue(), "zoo");
    assert.deepEqual(f.changes, []);
    assert.equal(f.menu.menu.hidden, false);
});

test("choosing a sorted leaf assigns its real value and closes before change events run", () => {
    const f = picker();
    f.button.onclick();
    f.select.dispatchEvent = event => {
        assert.equal(f.menu.menu.hidden, true);
        assert.equal(f.button.getAttribute("aria-expanded"), "false");
        assert.equal(event.type, "change");
        assert.equal(f.selectedValue(), "alpha");
    };
    f.option("alpha").node.onclick();
    assert.equal(f.button.textContent, "Alpha");
    assert.equal(f.button.focused, true);
});

test("Uncategorized stays direct; independent and removed categories remain selectable", () => {
    const f = picker(0);
    f.button.onclick();
    assert.equal(f.menu.expandedGroup, null);
    assert.equal(f.option("").node.getAttribute("aria-level"), "1");
    f.group(null).display.onclick(); f.option("lend").node.onclick();
    assert.deepEqual(f.changes, [["change", "lend"]]);
    f.button.onclick();
    assert.equal(f.group(null).node.getAttribute("aria-expanded"), "true");
    f.option("").node.onclick();
    assert.deepEqual(f.changes.at(-1), ["change", ""]);
    f.select.selectedIndex = 7; f.button.onclick();
    assert.equal(f.option("removed").node.getAttribute("aria-selected"), "true");
    f.option("removed").node.onclick();
    assert.equal(f.changes.length, 2);
});

test("keyboard navigation expands and collapses headers; Escape, blur and Tab never assign a preview", () => {
    const f = picker(0);
    f.key("ArrowDown"); f.key("ArrowDown"); f.key("Enter");
    assert.equal(f.group("needs").node.getAttribute("aria-expanded"), "true");
    f.key("ArrowRight");
    assert.equal(f.active().textContent, "Alpha");
    f.key("ArrowLeft"); f.key("ArrowLeft");
    assert.equal(f.group("needs").node.getAttribute("aria-expanded"), "false");
    f.key("ArrowRight"); f.key("ArrowDown"); f.key("Escape");
    assert.equal(f.selectedValue(), "");
    f.key("a"); f.key("Tab");
    assert.equal(f.selectedValue(), "");
    f.button.onclick(); f.group("needs").display.onclick();
    f.option("beta").node.onpointermove({ pointerType: "mouse" });
    f.button.onblur();
    assert.equal(f.selectedValue(), "");
    assert.deepEqual(f.changes, []);
});

test("typeahead reveals a category in a collapsed group and Enter alone commits it", () => {
    const f = picker(0);
    f.key("t");
    assert.equal(f.group("leisure").node.getAttribute("aria-expanded"), "true");
    assert.equal(f.active().textContent, "Travel");
    assert.deepEqual(f.changes, []);
    f.key("Enter");
    assert.deepEqual(f.changes, [["change", "travel"]]);
    f.button.onclick(); f.key("Home"); f.key("Enter");
    assert.deepEqual(f.changes.at(-1), ["change", ""]);
});

test("picker retains trigger width and respects disabled, outside-click and scroll handling", () => {
    const f = picker();
    f.button.setAttribute("aria-disabled", "true"); f.button.onclick(); f.key("Enter");
    assert.equal(f.menu.menu.hidden, true);
    f.button.setAttribute("aria-disabled", "false"); f.button.onclick();
    assert.equal(parseFloat(f.menu.menu.style.width), f.button.getBoundingClientRect().width);
    f.listeners.get("scroll")({ target: f.menu.menu });
    assert.equal(f.menu.menu.hidden, false);
    f.listeners.get("pointerdown")({ target: {} });
    assert.equal(f.menu.menu.hidden, true);
    assert.deepEqual(f.changes, []);
});

test("legacy catalogs with no groups put all categories under Independent, with safe literal labels", () => {
    const f = picker(4);
    f.select.options[4].textContent = "Example <img src=x>";
    f.menu.configure([], {}); f.button.onclick();
    assert.equal(f.menu.rows.filter(row => row.kind === "group").length, 1);
    assert.equal(f.option("travel").node.textContent, "Example <img src=x>");
    assert.equal(f.option("travel").node.children.length, 0);
});
