// Fictional summaries and a DOM substitute; no real database, config or network.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture() {
    const elements = new Map();
    function node(tag = "div") {
        return {
            tagName: tag, children: [], dataset: {}, attributes: new Map(), text: "",
            ownerDocument: document, hidden: false, disabled: false,
            get textContent() { return this.text + this.children.map(child => child.textContent).join(""); },
            set textContent(value) { this.text = String(value); this.children = []; },
            setAttribute(name, value) { this.attributes.set(name, value); },
            getAttribute(name) { return this.attributes.get(name); },
            focus() { this.focused = true; },
            replaceChildren(...children) { this.text = ""; this.children = children; },
            appendChild(child) { this.children.push(child); child.parentNode = this; },
        };
    }
    const document = {
        addEventListener() {}, createElement: node,
        createTextNode(text) { const child = node("#text"); child.textContent = text; return child; },
        getElementById(id) { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    };
    const context = vm.createContext({ document, URLSearchParams, AbortController });
    for (const filename of ["summary-controls.js", "analysis.js"]) {
        vm.runInContext(readFileSync(resolve(__dirname, `../src/finvault/api/static/${filename}`), "utf8"), context);
    }
    return { document, ...vm.runInContext("({AnalysisView, CategorySelection, PeriodPicker, AmountVisibility})", context),
        get: id => document.getElementById(id) };
}

function summary(count = 2) {
    return {
        income_group: "pay", income_total: "100.00",
        totals: { total_debits: "42.50", total_credits: "100.00", transaction_count: count },
        groups: [{ id: "custom", label: "Custom <img src=x>", total_debits: "42.50", total_credits: "0.00", transaction_count: 1, income_percentage: "42.5", categories: [
            { id: "shop", label: "Example shop", total_debits: "42.50", total_credits: "0.00", transaction_count: 1, income_percentage: "42.5" },
            { id: "unused", label: "Unused", total_debits: "0.00", total_credits: "0.00", transaction_count: 0, income_percentage: "0.0" },
        ] }],
        independent: [{ id: "refund", label: "Refund", total_debits: "0.00", total_credits: "100.00", transaction_count: 1 }],
        uncategorized: { total_debits: "0.00", total_credits: "0.00", transaction_count: 0 },
    };
}

test("main group cards omit zero sides, preserve mixed totals, and keep safe labels and persistent masking", () => {
    const { AnalysisView, document, get } = fixture();
    const view = new AnalysisView(document);
    const data = summary();
    data.groups.push(
        { id: "income", categories: [], label: "Example income", total_debits: "0.00", total_credits: "100.00", transaction_count: 1, income_percentage: "100.0" },
        { id: "mixed", categories: [], label: "Example mixed", total_debits: "5.00", total_credits: "2.00", transaction_count: 2, income_percentage: "125.0" },
        { id: "empty", categories: [], label: "Example empty", total_debits: "0.00", total_credits: "0.00", transaction_count: 0 },
    );
    view.update({ status: "ready", summary: data });
    const card = get("group-cards").children[0];
    assert.equal(card.children[0].textContent, "Custom <img src=x>");
    assert.equal(card.children[0].children[0].children.length, 0);
    assert.equal(card.children[1].children[0].children[0].textContent, "1 transaction");
    assert.equal(card.children[1].children[1].textContent, "42.5%");
    assert.equal(card.children[2].children[0].className, "amount-line debit");
    assert.equal(card.children[2].children.length, 1);
    assert.equal(card.children[2].children[0].textContent, "Debits₹42.5");
    const [, income, mixed, empty] = get("group-cards").children;
    assert.equal(income.children[2].children.length, 1);
    assert.equal(income.children[2].children[0].className, "amount-line credit");
    assert.equal(income.children[2].textContent, "Credits₹100");
    assert.equal(income.children[1].children[1].textContent, "100%");
    assert.equal(mixed.children[2].children.length, 2);
    assert.equal(mixed.children[2].textContent, "Debits₹5Credits₹2");
    assert.equal(mixed.children[1].children[1].textContent, "125%");
    assert.equal(empty.children[2].children.length, 1);
    assert.equal(empty.children[2].textContent, "Total₹0");
    assert.equal(empty.children[1].children[1].textContent, "—");
    assert.match(get("other-cards").textContent, /Refund.*Credits₹100.*Uncategorized/);
    const refund = get("other-cards").children[0];
    assert.equal(refund.children[1].className, "card-count card-meta");
    assert.equal(refund.children[1].children[0].children[0].textContent, "1 transaction");
    assert.equal(refund.children[2].children.length, 1);
    assert.equal(refund.children[2].textContent, "Credits₹100");
    assert.equal(get("other-cards").children[1].children[2].textContent, "Total₹0");
    get("toggle-totals").onclick();
    assert.doesNotMatch(get("group-cards").textContent, /%/);
    for (const id of ["group-cards", "other-cards"]) {
        assert.doesNotMatch(get(id).textContent, /₹/);
        assert.match(get(id).textContent, /••••/);
    }
    view.update({ status: "loading" });
    assert.equal(get("group-cards").children.length, 0);
    view.update({ status: "ready", summary: summary() });
    assert.doesNotMatch(get("group-cards").textContent, /₹/);
    get("toggle-totals").onclick();
    assert.match(get("group-cards").textContent, /Debits₹42.5/);
    assert.match(get("group-cards").textContent, /42.5%/);
    assert.doesNotMatch(get("other-cards").textContent, /%/);
});

test("analysis handles empty selections and missing main groups without inventing groups", () => {
    const { AnalysisView, document, get } = fixture();
    const view = new AnalysisView(document);
    const zero = { total_debits: "0.00", total_credits: "0.00", transaction_count: 0 };
    view.update({ status: "ready", summary: { totals: zero, groups: [], independent: [], uncategorized: zero } });
    assert.equal(get("analysis-message").hidden, false);
    assert.equal(get("analysis-message").textContent, "No transactions for this selection.");
    assert.match(get("group-cards").textContent, /No main categories configured/);
    assert.equal(get("group-cards").children[0].children[0].href, "/settings");
    assert.match(get("other-cards").textContent, /Uncategorized/);
});

test("analysis starts this month and combines year/month selections using the existing period rules", async () => {
    const { PeriodPicker, document, get } = fixture();
    const now = new Date(), currentYear = now.getFullYear(), earlierYear = currentYear - 1;
    const requests = [];
    const picker = new PeriodPicker(document, [currentYear, earlierYear], query => {
        requests.push(`/analysis/summary${query ? `?${query}` : ""}`);
    });
    picker.refresh();
    const initial = new URLSearchParams(requests.at(-1).split("?")[1]);
    assert.equal(initial.get("months"), `${currentYear}-${String(now.getMonth() + 1).padStart(2, "0")}`);
    assert.equal(get("this-month").getAttribute("aria-pressed"), "true");
    await get("all-periods").onclick();
    assert.equal(requests.at(-1), "/analysis/summary");
    const [currentRow, earlierRow] = get("period-years").children;
    await currentRow.children[0].onclick();
    await earlierRow.children[0].onclick();
    await earlierRow.children[1].children[0].onclick();
    await earlierRow.children[1].children[1].onclick();
    const combined = new URLSearchParams(requests.at(-1).split("?")[1]);
    assert.deepEqual(combined.getAll("years"), [String(currentYear), String(earlierYear)]);
    assert.deepEqual(combined.getAll("months"), [`${earlierYear}-01`, `${earlierYear}-02`]);
    await earlierRow.children[1].children[0].onclick();
    assert.equal(new URLSearchParams(requests.at(-1).split("?")[1]).get("months"), `${earlierYear}-02`);
    await get("this-month").onclick();
    assert.equal(requests.at(-1).split("?")[1], initial.toString());
    assert.equal(earlierRow.children[1].hidden, true);
});


test("card selection filters main groups, subcategories, independent categories and Uncategorized", () => {
    const { CategorySelection } = fixture();
    const selection = new CategorySelection();
    const data = summary();
    data.groups.push({ id: "another", label: "Another", categories: [{ id: "other", label: "Other" }] });
    selection.update(data);
    const rows = [{ id: 1, category: "shop" }, { id: 2, category: "unused" },
        { id: 3, category: "refund" }, { id: 4, category: null }, { id: 5, category: "other" }];
    const ids = () => selection.apply(rows).map(row => row.id);
    selection.toggleGroup("custom");
    assert.deepEqual(ids(), [1, 2]);
    selection.toggleCategory("shop", "custom");
    assert.deepEqual(ids(), [1]);
    assert.equal(selection.label(), "Custom <img src=x> › Example shop");
    selection.toggleCategory("shop", "custom");
    assert.deepEqual(ids(), [1, 2]);
    selection.toggleGroup("another");
    assert.deepEqual(ids(), [5]);
    selection.toggleCategory("refund");
    assert.deepEqual(ids(), [3]);
    selection.toggleUncategorized();
    assert.deepEqual(ids(), [4]);
    selection.toggleUncategorized();
    assert.deepEqual(ids(), [1, 2, 3, 4, 5]);
    selection.toggleGroup("custom");
    selection.toggleCategory("shop", "custom");
    selection.update({ ...data, groups: [{ ...data.groups[0], categories: [] }] });
    assert.equal(selection.label(), "All categories");
});

test("expanding subcategory cards preserves shared masking, totals, selection and keyboard focus", () => {
    const { AnalysisView, AmountVisibility, document, get } = fixture();
    const amounts = new AmountVisibility(get("toggle-totals"), "totals");
    const overview = document.createElement("p");
    amounts.set(overview, "1000.00");
    let changes = 0;
    const view = new AnalysisView(document, amounts, () => { changes++; });
    const data = summary();
    view.update({ status: "ready", summary: data });
    const groupButton = get("group-cards").children[0].children[0].children[0];
    groupButton.onclick();
    assert.equal(groupButton.getAttribute("aria-pressed"), "true");
    assert.equal(get("subcategory-section").hidden, false);
    assert.equal(get("subcategory-cards").children.length, 2);
    assert.match(get("subcategory-cards").textContent, /42.5%/);
    const shop = get("subcategory-cards").children[0].children[0].children[0];
    shop.onclick();
    assert.match(get("category-selection-label").textContent, /Example shop/);
    const activeShop = get("subcategory-cards").children[0].children[0].children[0];
    assert.equal(activeShop.getAttribute("aria-pressed"), "true");
    assert.equal(activeShop.focused, true);
    const registeredFields = amounts.amounts.size;
    get("toggle-totals").onclick();
    assert.equal(overview.textContent, "••••");
    assert.doesNotMatch(get("subcategory-cards").textContent, /[₹%]/);
    get("all-subcategories").onclick();
    assert.equal(view.selection.current.kind, "group");
    assert.equal(amounts.amounts.size, registeredFields);
    view.update({ status: "loading" });
    view.update({ status: "ready", summary: data });
    assert.equal(view.selection.current.kind, "group");
    assert.doesNotMatch(get("subcategory-cards").textContent, /[₹%]/);
    get("clear-category-selection").onclick();
    assert.equal(get("subcategory-section").hidden, true);
    get("toggle-totals").onclick();
    assert.equal(overview.textContent, "₹1,000");
    assert.ok(changes >= 3);
});
