// Synthetic input only; requests are mocked and never reach a database.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function manualEntry(fetch = () => { throw new Error("Unexpected request"); }) {
    const context = vm.createContext({ fetch });
    vm.runInContext(readFileSync(resolve(__dirname, "../src/finvault/api/static/manual-entry.js"), "utf8"), context);
    return vm.runInContext("({ manualEntryTime, manualCalendarMonth, manualCalendarDate, manualValidationMessage, saveManualTransaction })", context);
}

test("manual entry defaults to IST across midnight and year boundaries", () => {
    const { manualEntryTime } = manualEntry();
    for (const [utc, ist] of [
        ["2001-01-02T00:00:00Z", "2001-01-02T05:30"],
        ["2001-01-02T18:30:00Z", "2001-01-03T00:00"],
        ["2001-12-31T23:59:59Z", "2002-01-01T05:29"],
    ]) assert.equal(manualEntryTime(new Date(utc)), ist);
});

test("the themed calendar handles Monday-first weeks, leap years and early years", () => {
    const { manualCalendarMonth, manualCalendarDate } = manualEntry();
    for (const [year, month, days, start] of [
        [2024, 2, 29, 3], [2100, 2, 28, 0], [2000, 2, 29, 1],
        [2026, 10, 31, 3], [1, 1, 31, 0], [99, 2, 28, 6],
    ]) {
        const actual = manualCalendarMonth(year, month);
        assert.equal(actual.days, days);
        assert.equal(actual.start, start);
    }
    assert.equal(manualCalendarDate(1, 2, 3), "0001-02-03");
    assert.equal(manualCalendarDate(2026, 10, 23), "2026-10-23");
});

test("manual save sends one JSON request, preserving decimal amount and local IST input", async () => {
    const payload = { amount: "12.30", transaction_type: "debit", merchant: "Example Shop",
        transaction_date: "2001-01-02T10:30", source: "Cash", category: null, account: "", reference: null };
    const saved = { ...payload, id: 5, transaction_date: "2001-01-02T10:30:00+05:30" };
    const calls = [];
    const { saveManualTransaction } = manualEntry(async (url, options) => {
        calls.push([url, options]);
        return { ok: true, json: async () => saved };
    });
    assert.deepEqual(await saveManualTransaction(payload), saved);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/transactions");
    assert.equal(calls[0][1].method, "POST");
    assert.equal(calls[0][1].headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(calls[0][1].body), payload);
});

test("form validation supplies themed message text for required fields, amounts and time", () => {
    const { manualValidationMessage } = manualEntry();
    const field = overrides => ({
        willValidate: true, id: "manual-amount", type: "number", value: "", required: true,
        min: "0.01", max: "", labels: [{ textContent: "Amount (₹)" }], getAttribute: () => null,
        validity: { valid: true }, ...overrides,
    });
    assert.equal(manualValidationMessage(field({ validity: { valueMissing: true } })), "Amount (₹) is required.");
    assert.equal(manualValidationMessage(field({ validity: { rangeUnderflow: true } })), "Amount (₹) must be at least 0.01.");
    assert.equal(manualValidationMessage(field({ validity: { stepMismatch: true } })), "Use no more than two decimal places.");
    assert.equal(manualValidationMessage(field({ type: "text", value: "   ", labels: [{ textContent: "Merchant / description" }] })), "Merchant / description is required.");
    assert.equal(manualValidationMessage(field({ min: "0", max: "23", getAttribute: () => "Hour", validity: { rangeOverflow: true } })), "Hour: enter a number from 0 to 23.");
    assert.equal(manualValidationMessage(field({ willValidate: false, validity: { valueMissing: true } })), "");
    assert.equal(manualValidationMessage(field({ value: "12.30" })), "");
});

test("manual save explains validation and database errors without displaying raw validation data", async () => {
    for (const [status, detail, message] of [
        [422, [{ loc: ["body", "amount"], input: "invalid" }], /Check the amount/],
        [409, "This database needs the manual-entry update.", /manual-entry update/],
        [503, "Could not save the transaction. Try again.", /Could not save/],
        [500, null, /Couldn’t save the transaction \(500\)/],
    ]) {
        const { saveManualTransaction } = manualEntry(async () => ({
            ok: false, status, json: async () => ({ detail }),
        }));
        await assert.rejects(saveManualTransaction({}), message);
    }
});

test("ambiguous save results advise checking the dashboard before retrying", async () => {
    for (const fetch of [
        async () => { throw new Error("Connection closed"); },
        async () => ({ ok: true, json: async () => { throw new Error("Incomplete body"); } }),
        async () => ({ ok: true, json: async () => ({}) }),
    ]) {
        await assert.rejects(manualEntry(fetch).saveManualTransaction({}), /Refresh the dashboard before trying again/);
    }
});
