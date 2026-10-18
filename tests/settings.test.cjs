// Settings behavior using fictional keywords, mocked requests and no real files.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function settings() {
    const html = readFileSync(resolve(__dirname, "../src/finvault/api/static/settings.html"), "utf8");
    const script = html.match(/<script id="settings-script">([\s\S]*?)<\/script>/)[1];
    const context = vm.createContext({ document: { addEventListener() {} }, fetch: async () => { throw new Error("Unexpected request"); } });
    vm.runInContext(readFileSync(resolve(__dirname, "../src/finvault/api/static/ui.js"), "utf8"), context);
    vm.runInContext(script, context);
    return { ...vm.runInContext("({ categoryId, SettingsDraft, SettingsSession, requestSettings, statusTime, refreshSystemStatus, backfillCommands, copyCommandText, requestBackfill, BackfillSession, initializeBrowserBackfill })", context), context };
}

function fixture() {
    return { categories: [{ id: "custom", label: "Custom" }, { id: "travel", label: "Travel" }], rules: [] };
}

function plain(value) { return JSON.parse(JSON.stringify(value)); }
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

test("browser backfill posts the shared optional month to this server and handles safe errors", async () => {
    const { context, requestBackfill } = settings();
    const requests = [];
    context.fetch = async (url, options) => {
        requests.push({ url, options });
        return { ok: true, json: async () => ({ matched: 2 }) };
    };
    await requestBackfill("preview", "");
    await requestBackfill("apply", "2001-02");
    assert.equal(requests[0].url, "/settings/categories/backfill/preview");
    assert.equal(requests[1].url, "/settings/categories/backfill/apply");
    assert.equal(requests[1].options.method, "POST");
    assert.equal(requests[1].options.cache, "no-store");
    assert.deepEqual(JSON.parse(requests[0].options.body), { month: null });
    assert.deepEqual(JSON.parse(requests[1].options.body), { month: "2001-02" });
    context.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: "Could not read or update categories. Try again." }) });
    await assert.rejects(requestBackfill("apply", ""), /Could not read or update/);
    context.fetch = async () => ({ ok: false, status: 422, json: async () => ({ detail: [{ input: "fictional private input" }] }) });
    await assert.rejects(requestBackfill("preview", ""), error => /valid month/.test(error.message) && !error.message.includes("private"));
    context.fetch = async () => { throw new Error("Synthetic network failure"); };
    await assert.rejects(requestBackfill("preview", ""), /reach the server/);
    await assert.rejects(requestBackfill("apply", ""), /may have completed.*Preview/);
});

test("browser backfill blocks unsaved or duplicate requests and reports preview and apply counts", async () => {
    const { BackfillSession } = settings();
    const pending = deferred(), calls = [];
    const state = new BackfillSession(async (...args) => { calls.push(args); return pending.promise; }, () => {});
    await state.run("apply");
    assert.equal(calls.length, 0);
    state.ready = true;
    const run = state.run("preview", "2001-02");
    assert.equal(state.busy, true);
    assert.match(state.message, /Checking matches.*2001-02/);
    await state.run("apply");
    assert.equal(calls.length, 1);
    pending.resolve({ considered: 5, matched: 2, unmatched: 1, missing_body: 1, unsupported_type: 1 });
    await run;
    assert.equal(state.busy, false);
    assert.match(state.message, /Checked: 5.*Matches: 2.*No match: 1.*Missing body: 1.*Unsupported type: 1.*No changes saved/);
    state.request = async () => ({ considered: 2, matched: 2, updated: 1, skipped: 1, unmatched: 0, missing_body: 0, unsupported_type: 0 });
    await state.run("apply");
    assert.match(state.message, /Applied.*all months.*Updated: 1.*Skipped: 1/);
    state.request = async () => { throw new Error("Try again."); };
    await state.run("preview");
    assert.equal(state.error, true);
    assert.equal(state.message, "Try again.");
    assert.equal(state.busy, false);
    await state.run("preview", "2001-13");
    assert.match(state.message, /YYYY-MM/);
});

test("browser buttons share the month field, require saved settings, and recover after failure", async () => {
    const { context, initializeBrowserBackfill, SettingsDraft } = settings();
    const elements = Object.fromEntries(["backfill-month", "run-backfill-preview", "run-backfill-apply", "backfill-result", "backfill-actions"].map(id => [id, {
        dataset: {}, value: "2001-02", disabled: false, textContent: "",
        setAttribute() {}, checkValidity() { return this.value !== "invalid"; },
        addEventListener(event, callback) { this[event] = callback; },
    }]));
    context.document.getElementById = id => elements[id];
    const session = { draft: null, busy: false };
    const view = initializeBrowserBackfill(session);
    const preview = elements["run-backfill-preview"], apply = elements["run-backfill-apply"], month = elements["backfill-month"];
    assert.equal(preview.disabled, true);
    session.draft = new SettingsDraft(fixture()); view.refresh();
    assert.equal(preview.disabled || apply.disabled, false);
    session.draft.categories[0].label = "Changed"; view.refresh();
    assert.equal(apply.disabled, true);
    assert.match(elements["backfill-result"].textContent, /Save your rule changes/);
    session.draft = new SettingsDraft(fixture()); view.refresh();
    const pending = deferred();
    context.fetch = () => pending.promise;
    apply.onclick();
    assert.equal(preview.disabled && apply.disabled && month.disabled, true);
    assert.equal(apply.textContent, "Applying…");
    pending.reject(new Error("Synthetic network failure"));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(preview.disabled || apply.disabled || month.disabled, false);
    assert.match(elements["backfill-result"].textContent, /may have completed/);
    month.value = "invalid"; month.input();
    assert.equal(preview.disabled && apply.disabled, true);
    month.value = ""; month.input();
    assert.equal(preview.disabled || apply.disabled, false);
});

test("backfill commands distinguish preview from apply and support an optional month", () => {
    const { backfillCommands } = settings();
    const all = backfillCommands("laptop");
    assert.equal(all.preview, "uv run --locked python -m finvault.backfill_categories");
    assert.equal(all.apply, all.preview + " --apply");
    const month = backfillCommands("laptop", "2001-02");
    assert.equal(month.preview, all.preview + " --month 2001-02");
    assert.equal(month.apply, month.preview + " --apply");
});

test("Pi commands quote paths and change to the runtime directory before running", () => {
    const { backfillCommands } = settings();
    const commands = backfillCommands("pi", "2001-02");
    assert.equal(commands.preview, 'cd "$HOME/.local/share/finvault" &&\n"$HOME/.local/share/finvault/current/.venv/bin/python" -m finvault.backfill_categories --month 2001-02');
    assert.equal(commands.apply, commands.preview + " --apply");
    assert.doesNotMatch(backfillCommands("pi").preview, /--month/);
});

test("invalid months and unknown hosts cannot become copyable shell commands", () => {
    const { backfillCommands } = settings();
    for (const month of ["2001-2", "2001-00", "2001-13", "0000-01", "2001-02; echo example", "$(echo example)"]) {
        assert.throws(() => backfillCommands("laptop", month), /YYYY-MM/);
    }
    assert.throws(() => backfillCommands("unknown"), /Choose/);
});

test("copy uses the clipboard API without changing focus or creating a field", async () => {
    const { copyCommandText } = settings();
    const values = [];
    assert.equal(await copyCommandText("example command", {}, { writeText: async text => values.push(text) }), true);
    assert.deepEqual(values, ["example command"]);
});

test("HTTP copy fallback cleans up and restores focus on success, failure and rejection", async () => {
    const { copyCommandText } = settings();
    for (const outcome of [true, false, "throw"]) {
        let removed = false, restored = false, selected = false, appended = false;
        const input = { style: {}, focus() {}, select() { selected = true; }, remove() { removed = true; } };
        const document = {
            activeElement: { focus() { restored = true; } },
            createElement: tag => { assert.equal(tag, "textarea"); return input; },
            body: { appendChild() { appended = true; } },
            execCommand: command => {
                assert.equal(command, "copy"); assert.equal(selected, true); assert.equal(input.value, "example command");
                if (outcome === "throw") throw new Error("Synthetic clipboard failure");
                return outcome;
            },
        };
        const clipboard = outcome === true ? undefined : { writeText: async () => { throw new Error("Synthetic permission denial"); } };
        assert.equal(await copyCommandText("example command", document, clipboard), outcome === true);
        assert.equal(appended && removed && restored, true);
    }
});

test("status timestamps always display IST, with explicit missing and invalid states", () => {
    const { statusTime } = settings();
    assert.match(statusTime("2001-01-01T22:00:00+00:00"), /02 Jan 2001.*03:30:00/);
    assert.equal(statusTime("2001-01-02T03:30:00+05:30"), statusTime("2001-01-01T22:00:00+00:00"));
    assert.equal(statusTime(null), "Not recorded yet");
    assert.equal(statusTime("invalid"), "Unavailable");
});

test("system status refresh handles errors separately from settings and only links valid commits", async () => {
    const { context, refreshSystemStatus } = settings();
    const nodes = Object.fromEntries(["refresh-status", "last-ingestion", "running-commit", "last-deploy-check", "check-result"].map(id => [id, {
        textContent: "Loading…", dataset: {}, replaceChildren(child) { this.child = child; },
    }]));
    context.document.getElementById = id => nodes[id];
    context.document.createElement = () => ({});
    let info = { last_ingestion_at: null, commit_id: "a".repeat(40), last_deploy_check_at: "2001-01-01T00:00:00+00:00", last_deploy_check_status: "unchanged" };
    context.fetch = async (url, options) => {
        assert.equal(url, "/settings/status");
        assert.equal(options.cache, "no-store");
        assert.equal(nodes["refresh-status"].disabled, true);
        return { ok: true, json: async () => info };
    };
    await refreshSystemStatus();
    assert.equal(nodes["last-ingestion"].textContent, "Not recorded yet");
    assert.equal(nodes["running-commit"].child.href, `https://github.com/anandvgeorge/finvault/commit/${info.commit_id}`);
    assert.match(nodes["last-deploy-check"].textContent, /05:30:00/);
    assert.equal(nodes["check-result"].textContent, "No updates");
    for (const [state, label] of [["checking", "Checking…"], ["deployed", "Updated"], ["failed", "Failed"], [null, "Not recorded yet"], ["toString", "Not recorded yet"]]) {
        info.last_deploy_check_status = state;
        await refreshSystemStatus();
        assert.equal(nodes["check-result"].textContent, label);
    }
    info.commit_id = "<script>invalid</script>";
    await refreshSystemStatus();
    assert.equal(nodes["running-commit"].textContent, "Unavailable");
    context.fetch = async () => { throw new Error("Synthetic connection error"); };
    await refreshSystemStatus();
    assert.equal(nodes["running-commit"].textContent, "Couldn’t load status");
    assert.equal(nodes["refresh-status"].disabled, false);
});

test("new category IDs handle accents, non-Latin names, numbers, collisions and length limits", () => {
    const { categoryId } = settings();
    assert.equal(categoryId("Café & travel", new Set()), "cafe_travel");
    assert.equal(categoryId("123", new Set()), "category_123");
    assert.equal(categoryId("旅行", new Set()), "category");
    assert.equal(categoryId("Travel", new Set(["travel", "travel_2"])), "travel_3");
    const label = "a".repeat(100);
    const original = categoryId(label, new Set());
    const duplicate = categoryId(label, new Set([original]));
    assert.equal(original.length, 64);
    assert.equal(duplicate.length, 64);
    assert.match(duplicate, /_2$/);
});

test("draft edits never mutate loaded settings; label changes preserve category IDs", () => {
    const { SettingsDraft } = settings();
    const source = fixture();
    const draft = new SettingsDraft(source);
    assert.equal(draft.dirty, false);
    draft.categories[0].label = " Renamed ";
    assert.equal(draft.dirty, true);
    assert.equal(draft.payload().categories[0].id, "custom");
    assert.equal(draft.payload().categories[0].label, "Renamed");
    assert.deepEqual(source, fixture());
    draft.categories[0].label = "Custom";
    assert.equal(draft.dirty, false);
});

test("adding and removing categories rejects duplicate names and dangling rule references", () => {
    const { SettingsDraft } = settings();
    const draft = new SettingsDraft(fixture());
    const added = draft.addCategory(" Café ");
    assert.deepEqual(plain(added), { id: "cafe", label: "Café" });
    for (const label of ["", "  ", "CUSTOM", "a".repeat(81)]) assert.throws(() => draft.addCategory(label));
    draft.addRule();
    assert.throws(() => draft.removeCategory("custom"), /rules/);
    draft.rules[0].category = "travel";
    draft.removeCategory("custom");
    assert.equal(draft.categories.some(category => category.id === "custom"), false);
    const empty = new SettingsDraft({ categories: [], rules: [] });
    assert.throws(() => empty.addRule(), /category/);
});

test("rule priority changes retain categories, types, keywords and boundary ordering", () => {
    const { SettingsDraft } = settings();
    const source = fixture();
    source.rules = [
        { category: "custom", keywords: ["fictional employer"], transaction_type: "credit" },
        { category: "travel", keywords: ["example transport"], transaction_type: null },
    ];
    const draft = new SettingsDraft(source);
    draft.moveRule(1, -1);
    assert.deepEqual(plain(draft.payload().rules), [...source.rules].reverse());
    draft.moveRule(0, -1);
    draft.moveRule(1, 1);
    assert.deepEqual(plain(draft.payload().rules), [...source.rules].reverse());
    assert.equal(draft.dirty, true);
    draft.moveRule(0, 1);
    assert.equal(draft.dirty, false);
});

test("validation catches empty or duplicate labels and incomplete rules before saving", () => {
    const { SettingsDraft } = settings();
    const draft = new SettingsDraft(fixture());
    draft.categories[0].label = " ";
    assert.match(draft.validationError(), /name/);
    draft.categories[0].label = "Travel";
    assert.match(draft.validationError(), /duplicate/);
    draft.categories[0].label = "Custom";
    draft.addRule();
    assert.match(draft.validationError(), /keywords/);
    draft.rules[0].keywords = ["example"];
    draft.rules[0].category = "missing";
    assert.match(draft.validationError(), /available category/);
    draft.rules[0].category = "custom";
    draft.rules[0].transaction_type = "invalid";
    assert.match(draft.validationError(), /Either/);
    draft.rules[0].transaction_type = "debit";
    assert.equal(draft.validationError(), "");
    for (const keywords of [[" "], ["a".repeat(201)], Array(101).fill("example")]) {
        draft.rules[0].keywords = keywords;
        assert.match(draft.validationError(), /keywords/);
    }
});

test("loading failure can be retried and does not create an empty replacement draft", async () => {
    const { SettingsSession } = settings();
    let fail = true;
    const session = new SettingsSession(async () => {
        if (fail) throw new Error("Synthetic unavailable server");
        return fixture();
    }, () => {});
    await session.load();
    assert.equal(session.state, "error");
    assert.equal(session.draft, null);
    assert.equal(session.busy, false);
    fail = false;
    await session.load();
    assert.equal(session.state, "ready");
    assert.equal(session.draft.dirty, false);
});

test("edits stay local until explicit Save; saving resets the dirty state", async () => {
    const { SettingsSession } = settings();
    const requests = [];
    const session = new SettingsSession(async (method, payload) => {
        requests.push({ method, payload });
        return payload || fixture();
    }, () => {});
    await session.load();
    await session.save();
    assert.equal(requests.length, 1);
    session.draft.addCategory("Extra");
    session.changed();
    assert.equal(requests.length, 1);
    assert.equal(session.message, "Unsaved changes");
    await session.save();
    assert.equal(requests[1].method, "PUT");
    assert.equal(requests[1].payload.categories.at(-1).label, "Extra");
    assert.equal(session.draft.dirty, false);
    assert.equal(session.message, "Changes saved");
});

test("save failure keeps edits for retry, and invalid rules never send a write", async () => {
    const { SettingsSession } = settings();
    let fail = true, writes = 0;
    const session = new SettingsSession(async (method, payload) => {
        if (method === "GET") return fixture();
        writes++;
        if (fail) throw new Error("Synthetic write failure");
        return payload;
    }, () => {});
    await session.load();
    session.draft.addRule();
    await session.save();
    assert.equal(writes, 0);
    assert.match(session.message, /keywords/);
    session.draft.rules[0].keywords = ["fictional employer"];
    await session.save();
    assert.equal(writes, 1);
    assert.equal(session.draft.dirty, true);
    assert.match(session.message, /still here/);
    fail = false;
    await session.save();
    assert.equal(writes, 2);
    assert.equal(session.draft.dirty, false);
});

test("busy sessions prevent overlapping saves and discard requests", async () => {
    const { SettingsSession } = settings();
    const pending = deferred();
    let writes = 0, reads = 0;
    const session = new SettingsSession(async (method) => {
        if (method === "GET") { reads++; return fixture(); }
        writes++;
        return pending.promise;
    }, () => {});
    await session.load();
    session.draft.categories[0].label = "Renamed";
    const saving = session.save();
    assert.equal(session.busy, true);
    await session.save();
    await session.load();
    assert.equal(writes, 1);
    assert.equal(reads, 1);
    pending.resolve(session.draft.payload());
    await saving;
    assert.equal(session.busy, false);
    assert.equal(session.draft.dirty, false);
});

test("Discard reloads current server settings; failed reload preserves the unsaved draft", async () => {
    const { SettingsSession } = settings();
    let source = fixture(), fail = false;
    const session = new SettingsSession(async () => {
        if (fail) throw new Error("Synthetic read failure");
        return source;
    }, () => {});
    await session.load();
    session.draft.categories[0].label = "Unsaved";
    fail = true;
    await session.load();
    assert.equal(session.draft.categories[0].label, "Unsaved");
    assert.equal(session.draft.dirty, true);
    fail = false;
    source = { categories: [{ id: "server", label: "Current server label" }], rules: [] };
    await session.load();
    assert.deepEqual(plain(session.draft.payload()), source);
    assert.equal(session.draft.dirty, false);
});

test("settings requests use JSON, avoid caches and present safe server errors", async () => {
    const { context, requestSettings } = settings();
    const requests = [];
    context.fetch = async (url, options) => {
        requests.push({ url, options });
        return { ok: true, json: async () => fixture() };
    };
    await requestSettings("GET");
    await requestSettings("PUT", fixture());
    assert.equal(requests[0].url, "/settings/categories");
    assert.equal(requests[0].options.cache, "no-store");
    assert.equal(requests[0].options.body, undefined);
    assert.equal(requests[1].options.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(requests[1].options.body), fixture());
    context.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: "Could not save category settings" }) });
    await assert.rejects(requestSettings("PUT", fixture()), /Could not save category settings/);
    context.fetch = async () => ({ ok: false, status: 422, json: async () => ({ detail: [{ input: "fictional private keyword" }] }) });
    await assert.rejects(requestSettings("PUT", fixture()), error => !error.message.includes("fictional private keyword"));
    context.fetch = async () => { throw new Error("Synthetic network failure"); };
    await assert.rejects(requestSettings("GET"), /reach the server/);
});
