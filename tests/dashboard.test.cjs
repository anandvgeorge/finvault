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
    return { ...vm.runInContext("({ PeriodSelection, DashboardLoader, fetchJSON })", context), context };
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
