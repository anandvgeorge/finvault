// Synthetic selections and mocked responses; no network, browser, or database.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function dashboard() {
    const html = readFileSync(resolve(__dirname, "../src/finvault/api/static/index.html"), "utf8");
    const script = html.match(/<script id="dashboard-script">([\s\S]*?)<\/script>/)[1];
    const context = vm.createContext({
        URLSearchParams, AbortController,
        document: { addEventListener() {} },
        fetch: async () => { throw new Error("Unexpected network request"); },
    });
    vm.runInContext(script, context);
    return { ...vm.runInContext("({ PeriodSelection, AmountVisibility, CategoryMenu, CategoryEditor, CATEGORY_OPTIONS, saveCategory, TransactionFilters, TransactionSort, DashboardLoader, fetchJSON, renderTransactionDate })", context), context };
}

function selection(years = [2024, 2025]) {
    const { PeriodSelection } = dashboard();
    // Use a fixed date outside these years, starting with an all-time selection.
    return new PeriodSelection(years, new Date(2001, 0, 2));
}

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

// Small DOM stand-in for testing the actual amount renderer and toggle handler.
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

test("transaction dates show stored time and offset without changing the calendar day", () => {
    const { renderTransactionDate } = dashboard();
    for (const [timestamp, time] of [
        ["2001-10-13T09:15:30", "09:15:30"],
        ["2001-10-13T00:10:00+05:30", "00:10:00 IST"],
        ["2001-10-13T23:50:00-04:00", "23:50:00 -04:00"],
        ["2001-10-13T09:15:30.123456Z", "09:15:30 UTC"],
        ["2001-10-13T00:00:00", "00:00:00"],
    ]) {
        const cell = element();
        renderTransactionDate(cell, timestamp);
        const display = cell.children[0];
        assert.equal(display.dateTime, timestamp);
        assert.equal(display.text, "2001-10-13");
        assert.equal(display.children[0].textContent, time);
        assert.equal(display.children[0].className, "transaction-time");
    }
});

test("a date without a stored time does not acquire a fabricated time", () => {
    const { renderTransactionDate } = dashboard();
    const cell = element();
    renderTransactionDate(cell, "2001-10-13");
    assert.equal(cell.textContent, "2001-10-13");
    assert.equal(cell.children[0].children.length, 0);
});

test("amounts start visible with accessible Hide actions", () => {
    const { AmountVisibility } = dashboard();
    const button = element();
    const display = new AmountVisibility(button, "totals");
    const amount = element();
    display.set(amount, "1490000.75");
    assert.equal(amount.textContent, "₹14,90,000.75");
    assert.equal(button.getAttribute("aria-label"), "Hide totals");
    assert.equal(button.title, "Hide totals");
    assert.equal(button.dataset.hidden, "false");
});

test("totals and transaction amounts hide independently and restore their exact values", () => {
    const { AmountVisibility } = dashboard();
    const totalsButton = element(), transactionButton = element();
    const totals = new AmountVisibility(totalsButton, "totals");
    const transactions = new AmountVisibility(transactionButton, "transaction amounts");
    const cards = [element(), element(), element(), element(), element()];
    const values = [42.5, 100, 57.5, 0, -25];
    cards.forEach((card, index) => totals.set(card, values[index]));
    const amount = element();
    transactions.set(amount, "42.50");
    totalsButton.onclick();
    assert.equal(cards.every(card => card.textContent === "••••"), true);
    assert.equal(cards[0].children[0].getAttribute("aria-label"), "Amount hidden");
    assert.equal(totalsButton.getAttribute("aria-label"), "Show totals");
    assert.equal(totalsButton.dataset.hidden, "true");
    assert.equal(amount.textContent, "₹42.5");
    transactionButton.onclick();
    assert.equal(amount.textContent, "••••");
    assert.equal(transactionButton.getAttribute("aria-label"), "Show transaction amounts");
    totalsButton.onclick();
    assert.deepEqual(cards.map(card => card.textContent), ["₹42.5", "₹100", "₹57.5", "₹0", "₹-25"]);
    assert.equal(amount.textContent, "••••");
    transactionButton.onclick();
    assert.equal(amount.textContent, "₹42.5");
});

test("newly filtered or loaded amounts remain masked without retaining removed cells", () => {
    const { AmountVisibility } = dashboard();
    const button = element();
    const display = new AmountVisibility(button, "transaction amounts");
    const previous = element(), next = element();
    display.set(previous, "42.50");
    button.onclick();
    display.clear();
    display.set(next, "99.75");
    assert.equal(next.textContent, "••••");
    assert.equal(display.visible, false);
    button.onclick();
    assert.equal(next.textContent, "₹99.75");
    assert.equal(previous.textContent, "••••");
    assert.equal(display.amounts.size, 1);
});

test("hiding totals also hides the net cashflow colour cue", () => {
    const { AmountVisibility } = dashboard();
    const button = element();
    const display = new AmountVisibility(button, "totals");
    const cashflow = element();
    cashflow.id = "net-cashflow";
    display.set(cashflow, -42);
    assert.equal(cashflow.dataset.direction, "negative");
    button.onclick();
    assert.equal(cashflow.dataset.direction, "hidden");
    display.set(cashflow, 100);
    assert.equal(cashflow.textContent, "••••");
    assert.equal(cashflow.dataset.direction, "hidden");
    button.onclick();
    assert.equal(cashflow.dataset.direction, "positive");
    assert.equal(cashflow.textContent, "₹100");
    display.set(cashflow, 0);
    assert.equal(cashflow.dataset.direction, "neutral");
});

test("visibility can change while loading and a fresh page starts visible again", () => {
    const { AmountVisibility } = dashboard();
    const button = element();
    const display = new AmountVisibility(button, "totals");
    display.clear();
    button.onclick();
    const amount = element();
    display.set(amount, 0);
    assert.equal(amount.textContent, "••••");
    const fresh = new AmountVisibility(element(), "totals");
    fresh.set(amount, 0);
    assert.equal(amount.textContent, "₹0");
    assert.equal(fresh.visible, true);
});

function categoryMenu() {
    const listeners = new Map();
    const document = {
        addEventListener(name, handler) { listeners.set(name, handler); },
        defaultView: {
            innerWidth: 375, innerHeight: 844,
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
                getBoundingClientRect() { return { left: 200, top: 780, bottom: 816, width: 160, height: 240 }; },
            });
            return node;
        },
    };
    document.body = document.createElement();
    const select = document.createElement();
    select.options = ["Uncategorized", "Necessary", "Lifestyle", "Investment", "Salary", "Other"]
        .map((textContent, index) => ({ textContent, value: String(index) }));
    select.selectedIndex = 0;
    select.dispatchEvent = event => { select.lastEvent = event.type; };
    select.setAttribute("aria-label", "Category for Example Shop");
    document.body.appendChild(select);
    const { CategoryMenu } = dashboard();
    const menu = new CategoryMenu(document);
    const button = menu.enhance(select);
    const key = (key, extra = {}) => button.onkeydown({ key, preventDefault() {}, ...extra });
    return { menu, button, select, listeners, key };
}

test("category menu previews keyboard choices, Escape cancels, and Enter commits", () => {
    const { menu, button, select, key } = categoryMenu();
    assert.equal(select.hidden, true);
    assert.equal(button.textContent, "Uncategorized");
    assert.equal(button.getAttribute("role"), "combobox");
    key("ArrowDown");
    assert.equal(button.getAttribute("aria-expanded"), "true");
    assert.equal(menu.activeIndex, 0);
    key("ArrowDown");
    assert.equal(menu.activeIndex, 1);
    assert.equal(select.selectedIndex, 0);
    key("Escape");
    assert.equal(button.textContent, "Uncategorized");
    assert.equal(button.getAttribute("aria-activedescendant"), undefined);
    key("End");
    key("Enter");
    assert.equal(select.selectedIndex, 5);
    assert.equal(button.textContent, "Other");
    assert.equal(select.lastEvent, "change");
    assert.equal(menu.menu.hidden, true);
});

test("category menu supports typeahead, Tab, pointer selection, and safe option labels", () => {
    const { menu, button, select, key } = categoryMenu();
    key("l");
    assert.equal(menu.activeIndex, 2);
    key("Tab");
    assert.equal(button.textContent, "Lifestyle");
    select.options.push({ value: "custom", textContent: "Example <img src=x> category" });
    button.onclick();
    assert.equal(menu.items[6].textContent, "Example <img src=x> category");
    assert.equal(menu.items[6].children.length, 0);
    menu.items[6].onclick();
    assert.equal(button.textContent, "Example <img src=x> category");
    assert.equal(select.selectedIndex, 6);
});

test("category menu fits a narrow viewport and closes on outside click, scrolling, or resize", () => {
    const { menu, button, listeners } = categoryMenu();
    button.onclick();
    assert.equal(menu.menu.style.width, "160px");
    assert.equal(menu.menu.style.left, "200px");
    assert.equal(menu.menu.style.top, "534px");
    listeners.get("pointerdown")({ target: menu.items[0] });
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
    const { menu, button, select } = categoryMenu();
    let changes = 0;
    select.dispatchEvent = () => {
        changes++;
        assert.equal(menu.button, null);
        assert.equal(menu.menu.hidden, true);
        button.onblur();
    };
    button.onclick();
    menu.items[0].onclick();
    assert.equal(changes, 0);
    button.onclick();
    menu.items[1].onclick();
    assert.equal(changes, 1);
});

test("category options include Eat out and Lend with the API values", () => {
    const { CATEGORY_OPTIONS } = dashboard();
    assert.equal(CATEGORY_OPTIONS.find(([value]) => value === "eat_out")[1], "Eat out");
    assert.equal(CATEGORY_OPTIONS.find(([value]) => value === "lend")[1], "Lend");
});

test("category saves use PATCH with JSON and null clears a selection", async () => {
    const { saveCategory, context } = dashboard();
    const requests = [];
    context.fetch = async (url, options) => {
        requests.push({ url, options });
        return { ok: true, json: async () => ({ id: 7, category: JSON.parse(options.body).category }) };
    };
    await saveCategory(7, "eat_out");
    await saveCategory(7, null);
    assert.equal(requests[0].url, "/transactions/7/category");
    assert.equal(requests[0].options.method, "PATCH");
    assert.equal(requests[0].options.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(requests[0].options.body), { category: "eat_out" });
    assert.deepEqual(JSON.parse(requests[1].options.body), { category: null });
});

test("HTTP errors, network errors, and mismatched category responses are rejected", async () => {
    const { saveCategory, context } = dashboard();
    for (const status of [404, 422, 500]) {
        context.fetch = async () => ({ ok: false, status, json: () => { throw new Error("Unexpected body read"); } });
        await assert.rejects(saveCategory(7, "lend"));
    }
    context.fetch = async () => { throw new Error("Offline"); };
    await assert.rejects(saveCategory(7, "lend"));
    for (const response of [{ id: 8, category: "lend" }, { id: 7, category: "salary" }]) {
        context.fetch = async () => ({ ok: true, json: async () => response });
        await assert.rejects(saveCategory(7, "lend"));
    }
});

test("category edits report pending/saved states and retain the saved value across stale GET data", async () => {
    const { CategoryEditor } = dashboard();
    const pending = deferred();
    const events = [];
    const editor = new CategoryEditor(() => pending.promise, (id, state) => events.push({ id, ...state }));
    const transaction = { id: 7, category: null };
    const saving = editor.save(transaction, "eat_out");
    assert.equal(editor.stateFor(transaction).status, "saving");
    assert.equal(editor.stateFor({ id: 7, category: null }).category, "eat_out");
    pending.resolve({ id: 7, category: "eat_out" });
    await saving;
    assert.equal(transaction.category, "eat_out");
    assert.equal(editor.stateFor({ id: 7, category: null }).category, "eat_out");
    assert.deepEqual(events, [
        { id: 7, category: "eat_out", status: "saving" },
        { id: 7, category: "eat_out", status: "saved" },
    ]);
});

test("failed category edits restore the last saved value and can be retried or cleared", async () => {
    const { CategoryEditor } = dashboard();
    let fail = true;
    const editor = new CategoryEditor(async () => { if (fail) throw new Error("Offline"); }, () => {});
    const transaction = { id: 7, category: "salary" };
    await editor.save(transaction, "lend");
    assert.equal(editor.stateFor(transaction).category, "salary");
    assert.equal(editor.stateFor(transaction).status, "error");
    assert.equal(transaction.category, "salary");
    fail = false;
    await editor.save(transaction, "lend");
    assert.equal(editor.stateFor(transaction).status, "saved");
    assert.equal(transaction.category, "lend");
    await editor.save(transaction, null);
    assert.equal(transaction.category, null);
});

test("category edits skip unchanged values and prevent overlapping saves for the same row", async () => {
    const { CategoryEditor } = dashboard();
    const pending = deferred();
    let calls = 0;
    const editor = new CategoryEditor(() => { calls++; return pending.promise; }, () => {});
    const transaction = { id: 7, category: "salary" };
    await editor.save(transaction, "salary");
    assert.equal(calls, 0);
    const saving = editor.save(transaction, "lend");
    await editor.save({ id: 7, category: "salary" }, "eat_out");
    assert.equal(calls, 1);
    pending.resolve();
    await saving;
    assert.equal(editor.stateFor(transaction).category, "lend");
});

test("different transaction categories save independently when responses arrive out of order", async () => {
    const { CategoryEditor } = dashboard();
    const first = deferred(), second = deferred();
    const editor = new CategoryEditor(id => id === 1 ? first.promise : second.promise, () => {});
    const one = { id: 1, category: null }, two = { id: 2, category: "salary" };
    const savingOne = editor.save(one, "eat_out"), savingTwo = editor.save(two, "lend");
    second.resolve();
    await savingTwo;
    assert.equal(editor.stateFor(one).status, "saving");
    assert.equal(two.category, "lend");
    first.reject(new Error("Offline"));
    await savingOne;
    assert.equal(one.category, null);
    assert.equal(two.category, "lend");
});

const TRANSACTIONS = [
    { id: 1, transaction_type: "debit", source: "UPI" },
    { id: 2, transaction_type: "credit", source: "Credit" },
    { id: 3, transaction_type: "debit", source: "Debit card" },
    { id: 4, transaction_type: "debit", source: "Credit card" },
    { id: 5, transaction_type: "credit", source: "Debit card" },
    { id: 6, transaction_type: "debit", source: "Bank transfer" },
    { id: 7, transaction_type: "credit", source: "UPI" },
];

test("transaction filters default to all rows and preserve their order", () => {
    const { TransactionFilters } = dashboard();
    const filters = new TransactionFilters();
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [1, 2, 3, 4, 5, 6, 7]);
    assert.equal(TRANSACTIONS.length, 7);
});

test("Debit includes every debit source until specific payment types narrow it", () => {
    const { TransactionFilters } = dashboard();
    const filters = new TransactionFilters();
    filters.toggleType("debit");
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [1, 3, 4, 6]);
    filters.toggleSource("UPI");
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [1]);
    filters.toggleSource("Debit card");
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [1, 3]);
    filters.toggleSource("Credit card");
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [1, 3, 4]);
});

test("debit payment types toggle off and clearing the last includes all debits again", () => {
    const { TransactionFilters } = dashboard();
    const filters = new TransactionFilters();
    filters.toggleType("debit");
    filters.toggleSource("UPI");
    filters.toggleSource("Debit card");
    filters.toggleSource("UPI");
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [3]);
    filters.toggleSource("Debit card");
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [1, 3, 4, 6]);
});

test("Credit clears debit payment filters and includes credits such as card reversals", () => {
    const { TransactionFilters } = dashboard();
    const filters = new TransactionFilters();
    filters.toggleType("debit");
    filters.toggleSource("UPI");
    filters.toggleType("credit");
    assert.equal(filters.sources.size, 0);
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [2, 5, 7]);
    filters.toggleSource("Credit card");
    assert.equal(filters.sources.size, 0);
    filters.toggleType("credit");
    assert.equal(filters.type, null);
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [1, 2, 3, 4, 5, 6, 7]);
});

test("deselecting Debit clears its payment types rather than restoring stale filters", () => {
    const { TransactionFilters } = dashboard();
    const filters = new TransactionFilters();
    filters.toggleType("debit");
    filters.toggleSource("UPI");
    filters.toggleType("debit");
    assert.equal(filters.type, null);
    assert.equal(filters.sources.size, 0);
    filters.toggleSource("Debit card");
    assert.equal(filters.sources.size, 0);
    filters.toggleType("debit");
    assert.deepEqual(filters.apply(TRANSACTIONS).map(row => row.id), [1, 3, 4, 6]);
});

test("transaction filters operate only on the supplied date-range rows", () => {
    const { TransactionFilters } = dashboard();
    const filters = new TransactionFilters();
    filters.toggleType("debit");
    filters.toggleSource("UPI");
    assert.deepEqual(filters.apply([TRANSACTIONS[1], TRANSACTIONS[2]]), []);
    assert.deepEqual(filters.apply([TRANSACTIONS[0], TRANSACTIONS[4]]).map(row => row.id), [1]);
});

const SORT_TRANSACTIONS = [
    { id: 1, transaction_date: "2001-10-13T18:00:00", amount: "9.50", transaction_type: "debit", source: "UPI" },
    { id: 2, transaction_date: "2001-10-13T09:15:00", amount: "100.00", transaction_type: "credit", source: "Credit" },
    { id: 3, transaction_date: "2001-10-12T23:59:59", amount: "20.05", transaction_type: "debit", source: "UPI" },
    { id: 4, transaction_date: "2001-10-11T00:00:00", amount: "20.05", transaction_type: "debit", source: "Debit card" },
];

test("transaction sorting preserves the server order until a header is clicked", () => {
    const { TransactionSort } = dashboard();
    const sort = new TransactionSort();
    const customOrder = [SORT_TRANSACTIONS[2], SORT_TRANSACTIONS[0], SORT_TRANSACTIONS[3]];
    assert.deepEqual(sort.apply(customOrder).map(row => row.id), [3, 1, 4]);
    assert.notEqual(sort.apply(customOrder), customOrder);
    sort.toggle("merchant");
    assert.equal(sort.field, null);
});

test("each sort field starts descending, toggles ascending, and resets when switching columns", () => {
    const { TransactionSort } = dashboard();
    const sort = new TransactionSort();
    for (const [field, direction] of [
        ["date", "desc"], ["date", "asc"], ["amount", "desc"],
        ["amount", "asc"], ["date", "desc"], ["amount", "desc"],
    ]) {
        sort.toggle(field);
        assert.equal(sort.field, field);
        assert.equal(sort.direction, direction);
    }
});

test("amount sorting compares numeric values and keeps equal amounts in their existing order", () => {
    const { TransactionSort } = dashboard();
    const sort = new TransactionSort();
    sort.toggle("amount");
    assert.deepEqual(sort.apply(SORT_TRANSACTIONS).map(row => row.id), [2, 3, 4, 1]);
    sort.toggle("amount");
    assert.deepEqual(sort.apply(SORT_TRANSACTIONS).map(row => row.id), [1, 3, 4, 2]);
    assert.deepEqual(SORT_TRANSACTIONS.map(row => row.id), [1, 2, 3, 4]);
});

test("date sorting includes the stored clock time and preserves date ordering across offsets", () => {
    const { TransactionSort } = dashboard();
    const sort = new TransactionSort();
    const rows = [...SORT_TRANSACTIONS, {
        id: 5, transaction_date: "2001-10-14T00:10:00+05:30", amount: "0",
    }];
    sort.toggle("date");
    assert.deepEqual(sort.apply(rows).map(row => row.id), [5, 1, 2, 3, 4]);
    sort.toggle("date");
    assert.deepEqual(sort.apply(rows).map(row => row.id), [4, 3, 2, 1, 5]);
});

test("sorting applies to the filtered rows and remains selected across new data or empty results", () => {
    const { TransactionSort, TransactionFilters } = dashboard();
    const sort = new TransactionSort(), filters = new TransactionFilters();
    filters.toggleType("debit");
    sort.toggle("amount");
    sort.toggle("amount");
    assert.deepEqual(sort.apply(filters.apply(SORT_TRANSACTIONS)).map(row => row.id), [1, 3, 4]);
    assert.deepEqual(Array.from(sort.apply([])), []);
    assert.deepEqual(sort.apply([SORT_TRANSACTIONS[1], SORT_TRANSACTIONS[0]]).map(row => row.id), [1, 2]);
    assert.equal(sort.direction, "asc");
});

test("available years are sorted and current year/month are selected by default", () => {
    const { PeriodSelection } = dashboard();
    const current = new PeriodSelection([2024, 2025, 2024], new Date(2025, 9, 16));
    assert.deepEqual(Array.from(current.years), [2025, 2024]);
    const query = new URLSearchParams(current.query());
    assert.deepEqual(query.getAll("years"), ["2025"]);
    assert.deepEqual(query.getAll("months"), ["2025-10"]);
    assert.equal(current.label(), "2025: Oct");
});

test("missing current year or an empty database defaults to all time", () => {
    for (const years of [[], [2024, 2025]]) {
        const current = selection(years);
        assert.equal(current.query(), "");
        assert.equal(current.label(), "All time");
    }
});

test("This month replaces mixed selections and follows the date at the time of the click", () => {
    const current = selection();
    current.toggleYear(2024);
    current.toggleMonth(2024, 3);
    current.toggleYear(2025);
    current.toggleMonth(2025, 1);
    const october = new Date(2025, 9, 16);
    assert.equal(current.selectThisMonth(october), true);
    const query = new URLSearchParams(current.query());
    assert.deepEqual(query.getAll("years"), ["2025"]);
    assert.deepEqual(query.getAll("months"), ["2025-10"]);
    assert.equal(current.isThisMonth(october), true);
    const november = new Date(2025, 10, 1);
    assert.equal(current.isThisMonth(november), false);
    current.selectThisMonth(november);
    assert.deepEqual(new URLSearchParams(current.query()).getAll("months"), ["2025-11"]);
    current.toggleMonth(2025, 12);
    assert.equal(current.isThisMonth(november), false);
    current.clear();
    assert.equal(current.isThisMonth(november), false);
    current.selectThisMonth(november);
    assert.equal(current.isThisMonth(november), true);
});

test("This month cannot accidentally show all time when the current year is unavailable", () => {
    const current = selection([2024]);
    current.toggleYear(2024);
    const before = current.query();
    assert.equal(current.selectThisMonth(new Date(2025, 9, 16)), false);
    assert.equal(current.query(), before);
    assert.equal(current.isThisMonth(new Date(2025, 9, 16)), false);
});

test("clicking a year includes its full year until months are selected", () => {
    const current = selection();
    current.toggleYear(2025);
    assert.equal(current.query(), "years=2025");
    assert.equal(current.label(), "2025: Full year");
});

test("a full year combines with multiple months of another year", () => {
    const current = selection();
    current.toggleYear(2024);
    current.toggleYear(2025);
    current.toggleMonth(2025, 2);
    current.toggleMonth(2025, 1);
    const query = new URLSearchParams(current.query());
    assert.deepEqual(query.getAll("years"), ["2025", "2024"]);
    assert.deepEqual(query.getAll("months"), ["2025-01", "2025-02"]);
    assert.equal(current.label(), "2025: Jan, Feb · 2024: Full year");
});

test("clicking selected months deselects them; removing the last restores full year", () => {
    const current = selection();
    current.toggleYear(2025);
    current.toggleMonth(2025, 1);
    current.toggleMonth(2025, 2);
    current.toggleMonth(2025, 1);
    assert.deepEqual(new URLSearchParams(current.query()).getAll("months"), ["2025-02"]);
    current.toggleMonth(2025, 2);
    assert.equal(current.query(), "years=2025");
    assert.equal(current.label(), "2025: Full year");
});

test("deselecting a year removes its months and preserves another year's selection", () => {
    const current = selection();
    for (const year of [2024, 2025]) {
        current.toggleYear(year);
        current.toggleMonth(year, 1);
    }
    current.toggleYear(2025);
    let query = new URLSearchParams(current.query());
    assert.deepEqual(query.getAll("years"), ["2024"]);
    assert.deepEqual(query.getAll("months"), ["2024-01"]);
    current.toggleYear(2025);
    query = new URLSearchParams(current.query());
    assert.deepEqual(query.getAll("months"), ["2024-01"]);
    assert.equal(current.selected.get(2025).size, 0);
});

test("All clears every year and month, including the initial default", () => {
    const { PeriodSelection } = dashboard();
    const current = new PeriodSelection([2024, 2025], new Date(2025, 9, 16));
    current.toggleYear(2024);
    current.toggleMonth(2024, 3);
    current.clear();
    assert.equal(current.query(), "");
    assert.equal(current.selected.size, 0);
    assert.equal(current.label(), "All time");
});

test("months cannot select a collapsed year or a year absent from the database", () => {
    const current = selection();
    current.toggleMonth(2025, 1);
    current.toggleYear(1999);
    assert.equal(current.query(), "");
    current.toggleYear(2025);
    current.toggleMonth(2025, 0);
    current.toggleMonth(2025, 13);
    assert.equal(current.query(), "years=2025");
});

test("cards and rows use identical filters and are published only after both finish", async () => {
    const { DashboardLoader } = dashboard();
    const calls = [];
    const states = [];
    const transactions = deferred();
    const loader = new DashboardLoader(async (url, signal) => {
        calls.push({ url, signal });
        return url.startsWith("/overview") ? { total_debits: 42 } : transactions.promise;
    }, state => states.push(state));
    const current = selection();
    current.toggleYear(2025);
    current.toggleMonth(2025, 1);
    const pending = loader.load(current.query());
    assert.equal(states.length, 1);
    assert.equal(states[0].status, "loading");
    assert.equal(calls[0].url.split("?")[1], calls[1].url.split("?")[1]);
    assert.equal(calls[0].signal, calls[1].signal);
    transactions.resolve([{ merchant: "Example Merchant" }]);
    await pending;
    assert.equal(states.length, 2);
    assert.equal(states[1].overview.total_debits, 42);
    assert.equal(states[1].transactions[0].merchant, "Example Merchant");
});

test("all-time requests omit selection filters on both endpoints", async () => {
    const { DashboardLoader } = dashboard();
    const calls = [];
    const loader = new DashboardLoader(async url => { calls.push(url); return []; }, () => {});
    await loader.load("");
    assert.deepEqual(calls, ["/overview", "/transactions"]);
});

test("a slow earlier response cannot replace a newer selection", async () => {
    const { DashboardLoader } = dashboard();
    const requests = [];
    const states = [];
    const loader = new DashboardLoader((url, signal) => {
        const response = deferred();
        requests.push({ ...response, url, signal });
        return response.promise;
    }, state => states.push(state));
    const older = loader.load("years=2024");
    const newer = loader.load("years=2025");
    assert.equal(requests[0].signal.aborted, true);
    requests[2].resolve({ total_debits: 25 });
    requests[3].resolve([]);
    await newer;
    requests[0].resolve({ total_debits: 24 });
    requests[1].resolve([]);
    await older;
    const ready = states.filter(state => state.status === "ready");
    assert.equal(ready.length, 1);
    assert.equal(ready[0].overview.total_debits, 25);
});

test("a superseded error cannot clear a newer successful selection", async () => {
    const { DashboardLoader } = dashboard();
    const requests = [];
    const states = [];
    const loader = new DashboardLoader(() => {
        const response = deferred();
        requests.push(response);
        return response.promise;
    }, state => states.push(state));
    const older = loader.load("years=2024");
    const newer = loader.load("years=2025");
    requests[2].resolve({ total_debits: 25 });
    requests[3].resolve([]);
    await newer;
    requests[0].reject(new Error("Synthetic outdated failure"));
    requests[1].resolve([]);
    await older;
    assert.equal(states[states.length - 1].status, "ready");
    assert.equal(states.some(state => state.status === "error"), false);
});

test("a failed request aborts its sibling and can be retried", async () => {
    const { DashboardLoader } = dashboard();
    const states = [];
    const signals = [];
    let fail = true;
    const loader = new DashboardLoader(async (url, signal) => {
        signals.push(signal);
        if (fail) throw new Error("Synthetic network failure");
        return [];
    }, state => states.push(state));
    await loader.load("years=2025");
    assert.equal(states[states.length - 1].status, "error");
    assert.equal(signals[0].aborted, true);
    fail = false;
    await loader.load("years=2025");
    assert.equal(states[states.length - 1].status, "ready");
});

test("HTTP errors are rejected before response bodies are rendered", async () => {
    const { fetchJSON, context } = dashboard();
    context.fetch = async () => ({ ok: false, status: 500 });
    await assert.rejects(fetchJSON("/overview"), /Request failed: 500/);
});
