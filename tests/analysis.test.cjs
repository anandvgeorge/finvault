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
    return { document, ...vm.runInContext("({AnalysisLoader, AnalysisView, initializeAnalysis})", context),
        get: id => document.getElementById(id) };
}

function summary(count = 2) {
    return {
        income_group: "pay", income_total: "100.00",
        totals: { total_debits: "42.50", total_credits: "100.00", transaction_count: count },
        groups: [{ id: "custom", label: "Custom <img src=x>", total_debits: "42.50", total_credits: "0.00", transaction_count: 1, income_percentage: "42.5" }],
        independent: [{ id: "refund", label: "Refund", total_debits: "0.00", total_credits: "100.00", transaction_count: 1 }],
        uncategorized: { total_debits: "0.00", total_credits: "0.00", transaction_count: 0 },
    };
}

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

test("main group cards omit zero sides, preserve mixed totals, and keep safe labels and persistent masking", () => {
    const { AnalysisView, document, get } = fixture();
    const view = new AnalysisView(document);
    const data = summary();
    data.groups.push(
        { label: "Example income", total_debits: "0.00", total_credits: "100.00", transaction_count: 1, income_percentage: "100.0" },
        { label: "Example mixed", total_debits: "5.00", total_credits: "2.00", transaction_count: 2, income_percentage: "125.0" },
        { label: "Example empty", total_debits: "0.00", total_credits: "0.00", transaction_count: 0 },
    );
    view.update({ status: "ready", summary: data });
    const card = get("group-cards").children[0];
    assert.equal(card.children[0].textContent, "Custom <img src=x>");
    assert.equal(card.children[0].children.length, 0);
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
    assert.match(get("selection-summary").textContent, /Credits₹100/);
    assert.match(get("other-cards").textContent, /Refund.*Credits₹100.*Uncategorized/);
    get("toggle-totals").onclick();
    assert.doesNotMatch(get("group-cards").textContent, /%/);
    for (const id of ["selection-summary", "group-cards", "other-cards"]) {
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
    assert.match(get("selection-summary").textContent, /0 transactions/);
});

test("analysis requests ignore stale responses and failures even when the transport ignores abort", async () => {
    const { AnalysisLoader } = fixture();
    const requests = [], events = [];
    const loader = new AnalysisLoader((url, signal) => {
        const request = { ...deferred(), url, signal };
        requests.push(request);
        return request.promise;
    }, result => events.push(result));
    const first = loader.load("years=2001"), second = loader.load("years=2002");
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(requests[1].url, "/analysis/summary?years=2002");
    requests[1].resolve(summary()); await second;
    requests[0].resolve(summary(99)); await first;
    assert.equal(events.filter(event => event.status === "ready").length, 1);
    assert.equal(events.at(-1).summary.totals.transaction_count, 2);
    const third = loader.load("years=2003"), fourth = loader.load("");
    requests[2].reject(new Error("Fictional failure")); await third;
    assert.equal(events.at(-1).status, "loading");
    requests[3].reject(new Error("Current failure")); await fourth;
    assert.equal(events.at(-1).status, "error");
    const retry = loader.load("");
    assert.equal(requests[4].url, "/analysis/summary");
    requests[4].resolve(summary()); await retry;
    assert.equal(events.at(-1).status, "ready");
});

test("analysis starts this month and combines year/month selections using the existing period rules", async () => {
    const { initializeAnalysis, document, get } = fixture();
    const now = new Date(), currentYear = now.getFullYear(), earlierYear = currentYear - 1;
    const requests = [];
    await initializeAnalysis(document, async url => {
        requests.push(url);
        return url === "/years" ? [currentYear, earlierYear] : summary();
    });
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
    assert.equal(get("data-status").textContent, "");
});

test("analysis retries calendar failures and summary failures without displaying old amounts", async () => {
    const { initializeAnalysis, document, get } = fixture();
    let failYears = true, failSummary = true;
    await initializeAnalysis(document, async url => {
        if (url === "/years") {
            if (failYears) throw new Error("Fictional calendar failure");
            return [];
        }
        if (failSummary) throw new Error("Fictional summary failure");
        return summary();
    });
    assert.match(get("data-status").textContent, /load filters/);
    assert.equal(get("retry").hidden, false);
    failYears = false;
    await get("retry").onclick();
    assert.match(get("data-status").textContent, /load this selection/);
    assert.equal(get("this-month").disabled, true);
    failSummary = false;
    await get("retry").onclick();
    assert.equal(get("retry").hidden, true);
    assert.match(get("group-cards").textContent, /₹42.5/);
    failSummary = true;
    await get("all-periods").onclick();
    assert.equal(get("group-cards").textContent, "");
    assert.equal(get("analysis-results").getAttribute("aria-busy"), "false");
});
