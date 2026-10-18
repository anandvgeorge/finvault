"use strict";

class CategorySelection {
    constructor() { this.current = { kind: "all" }; }

    update(summary) {
        this.summary = summary;
        const current = this.current;
        if (current.kind === "group" && !summary.groups.some(group => group.id === current.id)) this.clear();
        if (current.kind === "category") {
            const available = current.groupId
                ? summary.groups.find(group => group.id === current.groupId)?.categories
                : summary.independent;
            if (!available?.some(category => category.id === current.id)) this.clear();
        }
    }

    clear() { this.current = { kind: "all" }; }

    group() {
        const current = this.current;
        const id = current.kind === "group" ? current.id : current.groupId;
        return this.summary?.groups.find(group => group.id === id);
    }

    toggleGroup(id) {
        this.current = this.group()?.id === id ? { kind: "all" } : { kind: "group", id };
    }

    toggleCategory(id, groupId = null) {
        this.current = this.current.kind === "category" && this.current.id === id
            ? groupId ? { kind: "group", id: groupId } : { kind: "all" }
            : { kind: "category", id, groupId };
    }

    toggleUncategorized() {
        this.current = { kind: this.current.kind === "uncategorized" ? "all" : "uncategorized" };
    }

    label() {
        const current = this.current;
        if (current.kind === "all") return "All categories";
        if (current.kind === "uncategorized") return "Uncategorized";
        const group = this.group();
        if (current.kind === "group") return group.label;
        const category = (group?.categories ?? this.summary.independent).find(category => category.id === current.id);
        return group ? `${group.label} › ${category.label}` : category.label;
    }

    apply(transactions) {
        const current = this.current;
        if (current.kind === "all") return transactions;
        if (current.kind === "uncategorized") return transactions.filter(row => row.category == null);
        if (current.kind === "category") return transactions.filter(row => row.category === current.id);
        const categories = new Set(this.group().categories.map(category => category.id));
        return transactions.filter(row => categories.has(row.category));
    }
}

class AnalysisView {
    constructor(document, amounts, onSelectionChange = () => {}) {
        this.document = document;
        this.amounts = amounts ?? new AmountVisibility(document.getElementById("toggle-totals"), "totals");
        this.onSelectionChange = onSelectionChange;
        this.selection = new CategorySelection();
        this.results = document.getElementById("analysis-results");
        this.message = document.getElementById("analysis-message");
        this.groups = document.getElementById("group-cards");
        this.others = document.getElementById("other-cards");
        this.details = document.getElementById("subcategory-cards");
        this.otherSection = document.getElementById("other-section");
        this.detailSection = document.getElementById("subcategory-section");
        this.fields = { main: [], other: [], details: [] };
        this.buttons = { main: new Map(), other: new Map(), details: new Map() };
        document.getElementById("clear-category-selection").onclick = () => {
            this.selection.clear(); this.changed();
            this.buttons.main.values().next().value?.button.focus();
        };
        document.getElementById("all-subcategories").onclick = () => {
            const group = this.selection.group();
            if (group) { this.selection.current = { kind: "group", id: group.id }; this.changed(); }
        };
    }

    node(tag, className, text) {
        const element = this.document.createElement(tag);
        element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }

    count(value) { return `${value.toLocaleString("en-IN")} transaction${value === 1 ? "" : "s"}`; }

    setAmount(element, value, scope) {
        this.fields[scope].push(element);
        this.amounts.set(element, value);
    }

    amountLines(totals, compact, scope) {
        const list = this.node("dl", "card-amounts");
        const entries = [["debit", "Debits"], ["credit", "Credits"]]
            .filter(([type]) => !compact || Number(totals[`total_${type}s`]) !== 0);
        if (!entries.length) entries.push(["total", "Total"]);
        for (const [type, label] of entries) {
            const line = this.node("div", `amount-line ${type}`);
            const value = this.node("dd", "");
            this.setAmount(value, type === "total" ? "0" : totals[`total_${type}s`], scope);
            line.appendChild(this.node("dt", "", label));
            line.appendChild(value);
            list.appendChild(line);
        }
        return list;
    }

    card(entry, compact, scope, key, action) {
        const card = this.node("article", "analysis-card");
        const heading = this.node("h3", "");
        const button = this.node("button", "analysis-card-button", entry.label);
        button.type = "button";
        button.setAttribute("aria-controls", scope === "main" ? "subcategory-section transactions-table" : "transactions-table");
        button.onclick = () => {
            action(); this.changed();
            if (scope === "details") this.buttons.details.get(key)?.button.focus();
        };
        heading.appendChild(button);
        card.appendChild(heading);
        this.buttons[scope].set(key, { button, card });
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
            this.setAmount(percentage, entry.income_percentage ?? null, scope);
            count.appendChild(percentage);
        } else count.textContent = this.count(entry.transaction_count);
        card.appendChild(count);
        card.appendChild(this.amountLines(entry, compact, scope));
        return card;
    }

    clearScope(scope, container) {
        for (const field of this.fields[scope]) this.amounts.remove(field);
        this.fields[scope] = [];
        this.buttons[scope].clear();
        container.replaceChildren();
    }

    clear() {
        this.clearScope("main", this.groups);
        this.clearScope("other", this.others);
        this.clearScope("details", this.details);
        this.otherSection.hidden = this.detailSection.hidden = true;
    }

    changed() { this.renderSelection(); this.onSelectionChange(); }

    renderSelection() {
        const current = this.selection.current;
        const selectedGroup = this.selection.group();
        this.document.getElementById("category-selection-label").textContent = this.selection.label();
        this.document.getElementById("clear-category-selection").hidden = current.kind === "all";
        for (const [id, { button, card }] of this.buttons.main) {
            const selected = selectedGroup?.id === id;
            button.setAttribute("aria-pressed", String(selected));
            button.setAttribute("aria-expanded", String(selected));
            card.dataset.selected = String(selected);
        }
        for (const [id, { button, card }] of this.buttons.other) {
            const selected = id === null ? current.kind === "uncategorized"
                : current.kind === "category" && !current.groupId && current.id === id;
            button.setAttribute("aria-pressed", String(selected));
            card.dataset.selected = String(selected);
        }
        this.clearScope("details", this.details);
        this.detailSection.hidden = !selectedGroup;
        if (!selectedGroup) return;
        this.document.getElementById("subcategory-heading").textContent = `${selectedGroup.label} subcategories`;
        this.document.getElementById("all-subcategories").setAttribute("aria-pressed", String(current.kind === "group"));
        for (const category of selectedGroup.categories) {
            const card = this.card(category, true, "details", category.id,
                () => this.selection.toggleCategory(category.id, selectedGroup.id));
            const selected = current.kind === "category" && current.id === category.id;
            card.dataset.selected = String(selected);
            this.buttons.details.get(category.id).button.setAttribute("aria-pressed", String(selected));
            this.details.appendChild(card);
        }
        if (!selectedGroup.categories.length) this.details.appendChild(this.node("p", "analysis-empty", "No subcategories in this group."));
    }

    update(result) {
        this.clear();
        const loading = result.status === "loading";
        this.results.setAttribute("aria-busy", String(loading));
        this.message.hidden = false;
        if (result.status !== "ready") {
            this.message.textContent = loading ? "Loading categories…" : "Unable to load categories.";
            return;
        }
        const { summary } = result;
        this.selection.update(summary);
        this.document.getElementById("income-note").textContent = summary.income_group
            ? "Choose a card to see its subcategories and transactions. Percentages are of income for the selected period."
            : "Choose a card to filter transactions. Choose your Income main category in Settings to calculate percentages.";
        this.message.hidden = summary.totals.transaction_count !== 0;
        this.message.textContent = "No transactions for this selection.";
        for (const group of summary.groups) {
            this.groups.appendChild(this.card(group, true, "main", group.id, () => this.selection.toggleGroup(group.id)));
        }
        if (!summary.groups.length) {
            const hint = this.node("p", "analysis-empty", "No main categories configured. Add them in ");
            const link = this.node("a", "", "Settings"); link.href = "/settings";
            hint.appendChild(link); hint.appendChild(this.document.createTextNode("."));
            this.groups.appendChild(hint);
        }
        for (const entry of summary.independent.filter(entry => entry.transaction_count > 0)) {
            this.others.appendChild(this.card(entry, false, "other", entry.id, () => this.selection.toggleCategory(entry.id)));
        }
        this.others.appendChild(this.card({ ...summary.uncategorized, label: "Uncategorized" }, false, "other", null,
            () => this.selection.toggleUncategorized()));
        this.otherSection.hidden = false;
        this.renderSelection();
    }
}
