"use strict";

async function fetchAnalysisJSON(url, signal) {
    const response = await fetch(url, { signal, cache: "no-store" });
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    return response.json();
}

class AnalysisLoader {
    constructor(fetcher, onChange) {
        this.fetcher = fetcher;
        this.onChange = onChange;
        this.version = 0;
    }

    async load(query) {
        if (this.controller) this.controller.abort();
        const controller = new AbortController();
        this.controller = controller;
        const version = ++this.version;
        this.onChange({ status: "loading" });
        try {
            const summary = await this.fetcher(`/analysis/summary${query ? `?${query}` : ""}`, controller.signal);
            if (version === this.version) this.onChange({ status: "ready", summary });
        } catch (error) {
            if (version === this.version && error.name !== "AbortError") this.onChange({ status: "error" });
        }
    }
}

class AnalysisView {
    constructor(document) {
        this.document = document;
        this.amounts = new AmountVisibility(document.getElementById("toggle-totals"), "totals");
        this.results = document.getElementById("analysis-results");
        this.message = document.getElementById("analysis-message");
        this.summary = document.getElementById("selection-summary");
        this.groups = document.getElementById("group-cards");
        this.others = document.getElementById("other-cards");
        this.otherSection = document.getElementById("other-section");
    }

    node(tag, className, text) {
        const element = this.document.createElement(tag);
        element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }

    count(value) {
        return `${value.toLocaleString("en-IN")} transaction${value === 1 ? "" : "s"}`;
    }

    amountLines(totals, compact = false) {
        const list = this.node("dl", "card-amounts");
        const entries = [["debit", "Debits"], ["credit", "Credits"]]
            .filter(([type]) => !compact || Number(totals[`total_${type}s`]) !== 0);
        if (!entries.length) entries.push(["total", "Total"]);
        for (const [type, label] of entries) {
            const line = this.node("div", `amount-line ${type}`);
            const value = this.node("dd", "");
            this.amounts.set(value, type === "total" ? "0" : totals[`total_${type}s`]);
            line.appendChild(this.node("dt", "", label));
            line.appendChild(value);
            list.appendChild(line);
        }
        return list;
    }

    card(entry, compact = false) {
        const card = this.node("article", "analysis-card");
        card.appendChild(this.node("h3", "", entry.label));
        const count = this.node("p", compact ? "card-count card-meta" : "card-count");
        if (compact) {
            const label = this.node("span", "card-count-label");
            label.appendChild(this.node("span", "count-full", this.count(entry.transaction_count)));
            label.appendChild(this.node("span", "count-short", `${entry.transaction_count.toLocaleString("en-IN")} txn${entry.transaction_count === 1 ? "" : "s"}`));
            count.appendChild(label);
            const percentage = this.node("span", "income-percentage");
            percentage.dataset.format = "percentage";
            percentage.title = entry.income_percentage == null
                ? "Percentage unavailable without income for the selected period"
                : "Percent of income for the selected period";
            this.amounts.set(percentage, entry.income_percentage ?? null);
            count.appendChild(percentage);
        } else count.textContent = this.count(entry.transaction_count);
        card.appendChild(count);
        card.appendChild(this.amountLines(entry, compact));
        return card;
    }

    clear() {
        this.amounts.clear();
        for (const container of [this.summary, this.groups, this.others]) container.replaceChildren();
        this.summary.hidden = this.otherSection.hidden = true;
    }

    update(result) {
        this.clear();
        const loading = result.status === "loading";
        this.results.setAttribute("aria-busy", String(loading));
        this.message.hidden = false;
        if (result.status !== "ready") {
            this.message.textContent = loading ? "Loading analysis…" : "Unable to load analysis.";
            return;
        }
        const { summary } = result;
        this.document.getElementById("income-note").textContent = summary.income_group
            ? "Percentages are of income for the selected period."
            : "Choose your Income main category in Settings to calculate percentages.";
        this.message.hidden = summary.totals.transaction_count !== 0;
        this.message.textContent = "No transactions for this selection.";
        this.summary.hidden = false;
        this.summary.appendChild(this.node("p", "card-count", this.count(summary.totals.transaction_count)));
        this.summary.appendChild(this.amountLines(summary.totals));
        for (const group of summary.groups) this.groups.appendChild(this.card(group, true));
        if (!summary.groups.length) {
            const hint = this.node("p", "empty", "No main categories configured. Add them in ");
            const link = this.node("a", "", "Settings");
            link.href = "/settings";
            hint.appendChild(link);
            hint.appendChild(this.document.createTextNode("."));
            this.groups.appendChild(hint);
        }
        for (const entry of summary.independent.filter(entry => entry.transaction_count > 0)) {
            this.others.appendChild(this.card(entry));
        }
        this.others.appendChild(this.card({ ...summary.uncategorized, label: "Uncategorized" }));
        this.otherSection.hidden = false;
    }
}

async function initializeAnalysis(document = window.document, fetcher = fetchAnalysisJSON) {
    const view = new AnalysisView(document);
    const status = document.getElementById("data-status");
    const retry = document.getElementById("retry");
    let picker;
    const loader = new AnalysisLoader(fetcher, result => {
        view.update(result);
        retry.hidden = result.status !== "error";
        status.className = result.status === "error" ? "data-status error" : "data-status";
        status.textContent = result.status === "loading" ? "Loading selected data…"
            : result.status === "error" ? "Couldn’t load this selection. Try again." : "";
        retry.onclick = () => picker.refresh();
    });

    async function loadCalendar() {
        retry.hidden = true;
        status.className = "data-status";
        status.textContent = "Loading calendar…";
        view.update({ status: "loading" });
        try {
            const years = await fetcher("/years");
            picker = new PeriodPicker(document, years, query => loader.load(query));
            const loading = picker.refresh();
            picker.revealSelectedMonths();
            await loading;
        } catch (error) {
            view.update({ status: "error" });
            document.getElementById("selection-label").textContent = "Filters unavailable";
            status.className = "data-status error";
            status.textContent = "Couldn’t load filters. Try again.";
            retry.hidden = false;
            retry.onclick = loadCalendar;
        }
    }
    await loadCalendar();
}

document.addEventListener("DOMContentLoaded", () => { void initializeAnalysis(); });
