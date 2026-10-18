"use strict";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

class PeriodSelection {
    constructor(years, now = new Date()) {
        this.years = [...new Set(years)].sort((a, b) => b - a);
        this.selected = new Map();
        this.selectThisMonth(now);
    }

    selectThisMonth(now = new Date()) {
        if (!this.years.includes(now.getFullYear())) return false;
        this.selected.clear();
        this.selected.set(now.getFullYear(), new Set([now.getMonth() + 1]));
        return true;
    }

    isThisMonth(now = new Date()) {
        const months = this.selected.get(now.getFullYear());
        return this.selected.size === 1 && months?.size === 1 && months.has(now.getMonth() + 1);
    }

    toggleYear(year) {
        if (!this.years.includes(year)) return;
        if (this.selected.has(year)) this.selected.delete(year);
        else this.selected.set(year, new Set());
    }

    toggleMonth(year, month) {
        const months = this.selected.get(year);
        if (!months || month < 1 || month > 12) return;
        if (months.has(month)) months.delete(month);
        else months.add(month);
    }

    clear() { this.selected.clear(); }

    query() {
        const parameters = new URLSearchParams();
        for (const year of this.years) {
            if (!this.selected.has(year)) continue;
            parameters.append("years", year);
            for (const month of [...this.selected.get(year)].sort((a, b) => a - b)) {
                parameters.append("months", `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`);
            }
        }
        return parameters.toString();
    }

    label() {
        if (!this.selected.size) return "All time";
        return this.years.filter(year => this.selected.has(year)).map(year => {
            const months = [...this.selected.get(year)].sort((a, b) => a - b);
            return `${year}: ${months.length ? months.map(month => MONTHS[month - 1]).join(", ") : "Full year"}`;
        }).join(" · ");
    }
}

// The Analysis page uses the same state model and fixed-height rows as the dashboard.
class PeriodPicker {
    constructor(document, years, onChange) {
        this.document = document;
        this.selection = new PeriodSelection(years);
        this.onChange = onChange;
        this.rows = new Map();
        this.allButton = document.getElementById("all-periods");
        this.thisMonthButton = document.getElementById("this-month");
        const container = document.getElementById("period-years");
        container.replaceChildren();
        for (const year of this.selection.years) {
            const row = document.createElement("div");
            row.className = "period-row";
            const button = document.createElement("button");
            button.type = "button";
            button.className = "period-button year-button";
            button.textContent = year;
            button.setAttribute("aria-controls", `months-${year}`);
            button.onclick = () => { this.selection.toggleYear(year); return this.refresh(); };
            const months = document.createElement("div");
            months.id = `months-${year}`;
            months.className = "month-buttons";
            months.setAttribute("role", "group");
            months.setAttribute("aria-label", `Months in ${year}`);
            const buttons = new Map();
            MONTHS.forEach((name, index) => {
                const month = document.createElement("button");
                month.type = "button";
                month.className = "period-button";
                month.textContent = name;
                month.setAttribute("aria-label", `${MONTH_NAMES[index]} ${year}`);
                month.onclick = () => { this.selection.toggleMonth(year, index + 1); return this.refresh(); };
                months.appendChild(month);
                buttons.set(index + 1, month);
            });
            row.appendChild(button);
            row.appendChild(months);
            container.appendChild(row);
            this.rows.set(year, { button, months, buttons });
        }
        this.allButton.disabled = false;
        this.allButton.onclick = () => { this.selection.clear(); return this.refresh(); };
        this.thisMonthButton.onclick = () => {
            if (this.selection.selectThisMonth()) {
                const loading = this.refresh();
                this.revealSelectedMonths();
                return loading;
            }
        };
    }

    refresh() {
        const selection = this.selection;
        this.allButton.setAttribute("aria-pressed", String(selection.selected.size === 0));
        this.thisMonthButton.disabled = !selection.years.includes(new Date().getFullYear());
        this.thisMonthButton.title = this.thisMonthButton.disabled ? "No transactions for the current year." : "";
        this.thisMonthButton.setAttribute("aria-pressed", String(selection.isThisMonth()));
        this.document.getElementById("selection-label").textContent = selection.label();
        for (const [year, controls] of this.rows) {
            const active = selection.selected.has(year);
            controls.button.setAttribute("aria-pressed", String(active));
            controls.button.setAttribute("aria-expanded", String(active));
            controls.months.hidden = !active;
            for (const [month, button] of controls.buttons) {
                button.setAttribute("aria-pressed", String(active && selection.selected.get(year).has(month)));
            }
        }
        return this.onChange(selection.query());
    }

    revealSelectedMonths() {
        for (const [year, months] of this.selection.selected) {
            const controls = this.rows.get(year);
            const button = controls.buttons.get([...months][0]);
            if (button) controls.months.scrollLeft = button.offsetLeft - (controls.months.clientWidth - button.offsetWidth) / 2;
        }
    }
}

class AmountVisibility {
    constructor(button, label) {
        this.button = button;
        this.label = label;
        this.visible = true;
        this.amounts = new Map();
        button.onclick = () => {
            this.visible = !this.visible;
            this.updateButton();
            for (const [element, amount] of this.amounts) this.render(element, amount);
        };
        this.updateButton();
    }

    updateButton() {
        const action = `${this.visible ? "Hide" : "Show"} ${this.label}`;
        this.button.setAttribute("aria-label", action);
        this.button.title = action;
        this.button.dataset.hidden = String(!this.visible);
    }

    set(element, amount) {
        this.amounts.set(element, amount);
        this.render(element, amount);
    }

    clear() { this.amounts.clear(); }

    remove(element) { this.amounts.delete(element); }

    render(element, amount) {
        element.replaceChildren();
        if (element.id === "net-cashflow") {
            element.dataset.direction = !this.visible ? "hidden"
                : Number(amount) > 0 ? "positive" : Number(amount) < 0 ? "negative" : "neutral";
        }
        if (this.visible) {
            element.textContent = element.dataset.format === "percentage"
                ? amount == null ? "—" : `${Number(amount).toLocaleString("en-IN", { maximumFractionDigits: 1 })}%`
                : `₹${Number(amount).toLocaleString("en-IN")}`;
        } else {
            const mask = element.ownerDocument.createElement("span");
            mask.textContent = "••••";
            mask.setAttribute("role", "img");
            mask.setAttribute("aria-label", "Amount hidden");
            element.appendChild(mask);
        }
    }
}
