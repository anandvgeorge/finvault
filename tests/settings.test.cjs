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
    return { ...vm.runInContext("({ categoryId, SettingsDraft, SettingsSession, requestSettings, statusTime, refreshSystemStatus })", context), context };
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
