// Fictional tiles and pointer gestures; no server, files or financial data are changed.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function events(object = {}) {
    const handlers = new Map();
    object.addEventListener = (kind, handler) => {
        if (!handlers.has(kind)) handlers.set(kind, []);
        handlers.get(kind).push(handler);
    };
    object.fire = (kind, values = {}) => {
        const event = { isPrimary: true, button: 0, pointerId: 1, clientX: 30, clientY: 200,
            detail: 1, prevented: false, stopped: false,
            preventDefault() { this.prevented = true; },
            stopImmediatePropagation() { this.stopped = true; }, ...values };
        for (const handler of handlers.get(kind) || []) handler(event);
        return event;
    };
    return object;
}

function classes() {
    const names = new Set();
    return { add: name => names.add(name), remove: name => names.delete(name), contains: name => names.has(name) };
}

function fixture() {
    const context = vm.createContext({});
    vm.runInContext(readFileSync(resolve(__dirname, "../src/finvault/api/static/category-drag.js"), "utf8"), context);
    const CategoryDrag = vm.runInContext("CategoryDrag", context);
    const moves = [], announcements = [], frames = new Map(), timers = [], pageScrolls = [], previews = [];
    let sequence = 0, allowed = true, hit = null;
    const view = events({ innerWidth: 400, innerHeight: 700,
        requestAnimationFrame(callback) { const id = ++sequence; frames.set(id, callback); return id; },
        cancelAnimationFrame: id => frames.delete(id), setTimeout: callback => timers.push(callback),
        scrollBy: (x, y) => pageScrolls.push(y),
    });
    const document = events({ defaultView: view, hidden: false,
        body: { classList: classes(), appendChild: node => previews.push(node) },
        elementFromPoint: () => hit,
        createElement: () => ({ style: {}, offsetWidth: 140, offsetHeight: 32, setAttribute() {}, remove() { this.removed = true; } }),
        querySelector: selector => ({ getBoundingClientRect: () => selector === ".page-header" ? { bottom: 80 } : { top: 640 } }),
    });
    const section = id => ({ dataset: { groupId: id }, classList: classes(), inside: true,
        closest: selector => selector === ".category-section" ? sections[id] : null });
    const sections = { needs: null, leisure: null, "": null };
    for (const id of Object.keys(sections)) sections[id] = section(id);
    const row = { dataset: { categoryRow: "example" }, classList: classes(), isConnected: true, inside: true,
        closest: () => sections.needs, querySelector: () => ({ textContent: "Example category" }) };
    const handle = { closest: selector => selector === ".category-move" ? handle : row,
        matches: () => false, setPointerCapture() {}, releasePointerCapture() {} };
    const container = events({ contains: node => node.inside });
    let scrollTop = 50;
    const scroller = { clientHeight: 400, scrollHeight: 900,
        get scrollTop() { return scrollTop; },
        set scrollTop(value) { scrollTop = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
        getBoundingClientRect: () => ({ top: 100, bottom: 500, left: 10, right: 390 }),
    };
    const drag = new CategoryDrag(document, { container, scroller, canDrag: () => allowed,
        onMove: (...args) => moves.push(args), announce: message => announcements.push(message) });
    return { drag, document, view, container, handle, row, sections, scroller, moves, announcements, pageScrolls, previews,
        hit: section => { hit = section; }, allow: value => { allowed = value; },
        down: values => container.fire("pointerdown", { target: handle, ...values }),
        move: values => document.fire("pointermove", values),
        up: values => document.fire("pointerup", values),
        frame() { const [id, callback] = frames.entries().next().value; frames.delete(id); callback(); },
        timers() { while (timers.length) timers.shift()(); },
        get frameCount() { return frames.size; },
    };
}

test("tapping the move handle leaves its chooser click available; secondary pointers cannot start a drag", () => {
    const f = fixture();
    f.down({ button: 2 }); assert.equal(f.drag.active, null);
    f.down({ isPrimary: false }); assert.equal(f.drag.active, null);
    f.down();
    assert.equal(f.move({ clientX: 34, clientY: 203 }).prevented, false);
    f.up({ clientX: 34, clientY: 203 });
    assert.equal(f.document.fire("click").prevented, false);
    assert.equal(f.previews.length, 0);
    assert.deepEqual(f.moves, []);
});

test("drag highlights the destination and drops into an empty group without opening the chooser", () => {
    const f = fixture();
    f.hit(f.sections.leisure); f.down();
    assert.equal(f.move({ clientY: 240 }).prevented, true);
    assert.equal(f.sections.leisure.classList.contains("is-drop-target"), true);
    assert.equal(f.row.classList.contains("is-dragging"), true);
    f.up({ clientY: 240 });
    assert.deepEqual(f.moves, [["example", "leisure"]]);
    assert.equal(f.sections.leisure.classList.contains("is-drop-target"), false);
    assert.equal(f.row.classList.contains("is-dragging"), false);
    assert.equal(f.previews[0].removed, true);
    assert.equal(f.frameCount, 0);
    assert.equal(f.document.fire("click").stopped, true);
    assert.equal(f.document.fire("click", { detail: 0 }).prevented, false);
    f.timers();
    assert.equal(f.document.fire("click").prevented, false);
});

test("independent drops use no group, while same-group and outside drops leave assignments alone", () => {
    for (const target of ["", "needs", null, "outside"]) {
        const f = fixture();
        f.hit(target === "outside" ? { inside: false, closest() { return this; } } : f.sections[target] ?? null);
        f.down(); f.move({ clientY: 250 }); f.up({ clientY: 250 });
        assert.deepEqual(f.moves, target === "" ? [["example", null]] : []);
        assert.equal(f.document.body.classList.contains("category-dragging"), false);
    }
});

test("Escape, pointer cancellation, lost capture and leaving the window cancel without changing grouping", () => {
    for (const reason of ["escape", "pointercancel", "capture", "blur", "hidden", "resize"]) {
        const f = fixture();
        f.hit(f.sections.leisure); f.down(); f.move({ clientY: 250 });
        if (reason === "escape") f.document.fire("keydown", { key: "Escape" });
        else if (reason === "pointercancel") f.document.fire("pointercancel");
        else if (reason === "capture") f.document.fire("lostpointercapture");
        else if (reason === "hidden") { f.document.hidden = true; f.document.fire("visibilitychange"); }
        else f.view.fire(reason);
        f.up({ clientY: 250 });
        assert.deepEqual(f.moves, []);
        assert.equal(f.frameCount, 0);
        assert.equal(f.previews[0].removed, true);
        assert.match(f.announcements.at(-1), /cancelled/);
        if (reason === "escape") assert.equal(f.document.fire("click").prevented, true);
    }
});

test("busy settings and replaced tiles cancel dragging; unrelated pointers cannot finish it", () => {
    const f = fixture();
    f.allow(false); f.down(); assert.equal(f.drag.active, null);
    f.allow(true); f.down(); f.hit(f.sections.leisure);
    f.move({ pointerId: 2, clientY: 250 }); assert.equal(f.previews.length, 0);
    f.move({ clientY: 250 }); f.up({ pointerId: 2, clientY: 250 });
    assert.notEqual(f.drag.active, null);
    f.allow(false); f.up({ clientY: 250 });
    assert.deepEqual(f.moves, []);
    f.allow(true); f.down(); f.move({ clientY: 250 });
    f.row.isConnected = false; f.frame();
    assert.equal(f.drag.active, null);
    assert.equal(f.frameCount, 0);
});

test("dragging near edges scrolls the category panel or mobile page and refreshes the target", () => {
    const f = fixture();
    f.down(); f.hit(f.sections.needs); f.move({ clientY: 490 });
    f.hit(f.sections.leisure); f.frame();
    assert.ok(f.scroller.scrollTop > 50);
    assert.equal(f.sections.leisure.classList.contains("is-drop-target"), true);
    assert.equal(f.pageScrolls.length, 0);
    f.drag.cancel();
    f.scroller.scrollHeight = f.scroller.clientHeight;
    f.down(); f.move({ clientY: 630 }); f.frame();
    assert.ok(f.pageScrolls.at(-1) > 0);
    f.move({ clientX: 450, clientY: 630 });
    const previous = f.pageScrolls.length;
    f.frame();
    assert.equal(f.pageScrolls.length, previous);
    f.drag.cancel();
});
