"use strict";

function manualEntryTime(now = new Date()) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(now).map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

const MANUAL_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

function manualCalendarMonth(year, month) {
    const date = new Date(0);
    date.setUTCFullYear(year, month, 0);
    const days = date.getUTCDate();
    date.setUTCDate(1);
    return { days, start: (date.getUTCDay() + 6) % 7 };
}

function manualCalendarDate(year, month, day) {
    return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function manualValidationMessage(field) {
    if (!field.willValidate) return "";
    const label = (field.getAttribute("aria-label") || field.labels?.[0]?.textContent || "Value").trim();
    const validity = field.validity;
    if (validity.valueMissing || (field.required && field.type === "text" && !field.value.trim())) return `${label} is required.`;
    if (validity.badInput) return `Enter a valid number for ${label.toLowerCase()}.`;
    if (validity.rangeUnderflow || validity.rangeOverflow) {
        if (field.min !== "" && field.max !== "") return `${label}: enter a number from ${field.min} to ${field.max}.`;
        return `${label} must be ${validity.rangeUnderflow ? `at least ${field.min}` : `at most ${field.max}`}.`;
    }
    if (validity.stepMismatch && field.id === "manual-amount") return "Use no more than two decimal places.";
    return validity.valid ? "" : `Check ${label.toLowerCase()}.`;
}

class ManualValidation {
    constructor(form) {
        this.form = form; this.errors = new Map(); this.onChange = () => {};
        const update = event => {
            if (this.errors.has(event.target)) { this.show(event.target, manualValidationMessage(event.target)); this.onChange(); }
        };
        form.addEventListener("input", update);
        form.addEventListener("change", update);
    }

    show(field, message) {
        let entry = this.errors.get(field);
        if (!message) {
            if (!entry) return;
            entry.node.remove(); field.removeAttribute("aria-invalid");
            if (entry.describedBy) field.setAttribute("aria-describedby", entry.describedBy);
            else field.removeAttribute("aria-describedby");
            this.errors.delete(field); return;
        }
        if (!entry) {
            const node = field.ownerDocument.createElement("p");
            node.className = "manual-field-error"; node.id = `${field.id}-error`;
            const anchor = field.closest(".manual-calendar-header, .manual-calendar-time") || field;
            anchor.insertAdjacentElement("afterend", node);
            entry = { node, describedBy: field.getAttribute("aria-describedby") };
            this.errors.set(field, entry);
            field.setAttribute("aria-describedby", [entry.describedBy, node.id].filter(Boolean).join(" "));
        }
        field.setAttribute("aria-invalid", "true"); entry.node.textContent = message;
    }

    validate(fields = this.form.querySelectorAll("input, select"), focus = true) {
        this.firstInvalid = null;
        for (const field of fields) {
            const message = manualValidationMessage(field);
            this.show(field, message);
            if (message && !this.firstInvalid) this.firstInvalid = field;
        }
        this.onChange();
        if (focus) this.firstInvalid?.focus();
        return !this.firstInvalid;
    }

    clear() { for (const field of [...this.errors.keys()]) this.show(field, ""); }
}

class ManualDatePicker {
    constructor(document, validation) {
        this.document = document; this.validation = validation;
        const get = id => document.getElementById(id);
        this.input = get("manual-date"); this.button = get("manual-date-trigger");
        this.value = get("manual-date-value"); this.panel = get("manual-calendar");
        this.monthLabel = get("manual-calendar-month"); this.yearInput = get("manual-calendar-year");
        this.days = get("manual-calendar-days"); this.hour = get("manual-calendar-hour"); this.minute = get("manual-calendar-minute");
        this.previous = get("manual-calendar-previous"); this.next = get("manual-calendar-next");
        this.button.onclick = () => this.panel.hidden ? this.open() : this.commit();
        this.previous.onclick = () => this.moveMonth(-1);
        this.next.onclick = () => this.moveMonth(1);
        this.yearInput.oninput = () => {
            if (this.yearInput.checkValidity()) { this.year = Number(this.yearInput.value); this.render(); }
        };
        get("manual-calendar-today").onclick = () => {
            this.selected = manualEntryTime().slice(0, 10);
            [this.year, this.month] = this.selected.split("-").map(Number);
            this.render(Number(this.selected.slice(8)));
        };
        get("manual-calendar-done").onclick = () => { if (this.commit()) this.button.focus({ preventScroll: true }); };
        this.days.onkeydown = event => this.keydown(event);
        document.addEventListener("scroll", event => { if (!this.panel.contains(event.target)) this.position(); }, true);
        document.defaultView.addEventListener("resize", () => this.position());
        document.defaultView.visualViewport?.addEventListener("resize", () => this.position());
        document.defaultView.visualViewport?.addEventListener("scroll", () => this.position());
    }

    reset() { this.close(); this.updateLabel(); }

    updateLabel() {
        const [date, time] = this.input.value.split("T");
        const [year, month, day] = date.split("-").map(Number);
        this.value.textContent = `${day} ${MANUAL_MONTH_NAMES[month - 1].slice(0, 3)} ${year} · ${time.slice(0, 5)}`;
    }

    open() {
        const [date, time] = this.input.value.split("T");
        this.selected = date;
        [this.year, this.month] = date.split("-").map(Number);
        [this.hour.value, this.minute.value] = time.split(":");
        this.panel.hidden = false; this.panel.disabled = false;
        this.panel.showPopover?.();
        this.button.setAttribute("aria-expanded", "true");
        this.render(Number(date.slice(8)));
    }

    close() {
        if (this.panel.hidePopover && this.panel.matches(":popover-open")) this.panel.hidePopover();
        this.panel.hidden = true; this.panel.disabled = true;
        this.button.setAttribute("aria-expanded", "false");
    }

    commit() {
        if (this.panel.hidden) return true;
        if (!this.validation.validate([this.yearInput, this.hour, this.minute])) return false;
        this.input.value = `${this.selected}T${String(Number(this.hour.value)).padStart(2, "0")}:${String(Number(this.minute.value)).padStart(2, "0")}`;
        this.updateLabel(); this.close();
        return true;
    }

    position() {
        if (this.panel.hidden) return;
        const view = this.document.defaultView, viewport = view.visualViewport;
        const left = viewport?.offsetLeft ?? 0, top = viewport?.offsetTop ?? 0;
        const width = viewport?.width ?? view.innerWidth, height = viewport?.height ?? view.innerHeight;
        const rect = this.button.getBoundingClientRect();
        const panelWidth = Math.min(300, width - 16);
        this.panel.style.width = `${panelWidth}px`; this.panel.style.maxHeight = `${Math.max(0, height - 16)}px`;
        const panelHeight = this.panel.getBoundingClientRect().height;
        const below = top + height - rect.bottom - 8, above = rect.top - top - 8;
        const preferredTop = below >= panelHeight || below >= above ? rect.bottom + 6 : rect.top - panelHeight - 6;
        this.panel.style.left = `${Math.max(left + 8, Math.min(rect.left, left + width - panelWidth - 8))}px`;
        this.panel.style.top = `${Math.max(top + 8, Math.min(preferredTop, top + height - panelHeight - 8))}px`;
    }

    moveMonth(delta) {
        const index = (this.year - 1) * 12 + this.month - 1 + delta;
        if (index < 0 || index >= 9999 * 12) return;
        this.year = Math.floor(index / 12) + 1; this.month = index % 12 + 1;
        this.render();
    }

    render(focusDay = null) {
        const { days, start } = manualCalendarMonth(this.year, this.month);
        const monthName = MANUAL_MONTH_NAMES[this.month - 1];
        this.monthLabel.textContent = monthName; this.yearInput.value = this.year;
        this.days.setAttribute("aria-label", `${monthName} ${this.year}`);
        this.previous.disabled = this.year === 1 && this.month === 1;
        this.next.disabled = this.year === 9999 && this.month === 12;
        this.days.replaceChildren();
        const today = manualEntryTime().slice(0, 10);
        const activeDay = focusDay ?? (this.selected.startsWith(manualCalendarDate(this.year, this.month, 1).slice(0, 8))
            ? Number(this.selected.slice(8)) : 1);
        for (let index = 0; index < 42; index++) {
            const day = index - start + 1;
            if (day < 1 || day > days) {
                const blank = this.document.createElement("span"); blank.setAttribute("aria-hidden", "true");
                this.days.appendChild(blank); continue;
            }
            const date = manualCalendarDate(this.year, this.month, day);
            const button = this.document.createElement("button");
            button.type = "button"; button.className = "manual-calendar-day"; button.dataset.day = day;
            button.textContent = day; button.tabIndex = day === activeDay ? 0 : -1;
            button.setAttribute("aria-label", `${day} ${monthName} ${this.year}`);
            button.setAttribute("aria-pressed", String(date === this.selected));
            if (date === today) button.setAttribute("aria-current", "date");
            button.onclick = () => { this.selected = date; this.render(day); };
            this.days.appendChild(button);
        }
        this.position();
        if (focusDay !== null) this.days.querySelector(`[data-day="${focusDay}"]`)?.focus({ preventScroll: true });
    }

    keydown(event) {
        const day = Number(event.target.dataset.day);
        if (!day) return;
        const steps = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
        const date = new Date(`${manualCalendarDate(this.year, this.month, day)}T00:00:00Z`);
        if (Object.hasOwn(steps, event.key)) date.setUTCDate(day + steps[event.key]);
        else if (event.key === "Home") date.setUTCDate(day - (date.getUTCDay() + 6) % 7);
        else if (event.key === "End") date.setUTCDate(day + 6 - (date.getUTCDay() + 6) % 7);
        else if (event.key === "PageUp" || event.key === "PageDown") {
            const delta = event.key === "PageUp" ? -1 : 1;
            const index = (this.year - 1) * 12 + this.month - 1 + delta;
            if (index < 0 || index >= 9999 * 12) return;
            const year = Math.floor(index / 12) + 1, month = index % 12 + 1;
            date.setUTCDate(1); date.setUTCFullYear(year, month - 1, Math.min(day, manualCalendarMonth(year, month).days));
        } else return;
        event.preventDefault();
        if (date.getUTCFullYear() < 1 || date.getUTCFullYear() > 9999) return;
        this.year = date.getUTCFullYear(); this.month = date.getUTCMonth() + 1;
        this.render(date.getUTCDate());
    }
}

async function saveManualTransaction(payload) {
    let response;
    try {
        response = await fetch("/transactions", {
            method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    } catch {
        throw new Error("Connection lost. The transaction may have been saved. Refresh the dashboard before trying again.");
    }
    if (!response.ok) {
        let detail;
        try { detail = (await response.json()).detail; } catch {}
        throw new Error(typeof detail === "string" ? detail : response.status === 422
            ? "Check the amount, merchant, date/time and other required fields."
            : `Couldn’t save the transaction (${response.status}). Try again.`);
    }
    let saved;
    try { saved = await response.json(); } catch {}
    if (!Number.isInteger(saved?.id) || saved.id <= 0 || typeof saved.transaction_date !== "string") {
        throw new Error("Couldn’t confirm the saved transaction. Refresh the dashboard before trying again.");
    }
    return saved;
}

function initializeManualEntry(document, { getCatalog, onSaved, onMessage }) {
    const dialog = document.getElementById("manual-transaction-dialog"), form = document.getElementById("manual-transaction-form");
    const open = document.getElementById("add-transaction"), fields = document.getElementById("manual-transaction-fields");
    const save = document.getElementById("save-manual-transaction"), cancel = document.getElementById("cancel-manual-transaction");
    const error = document.getElementById("manual-entry-error"), retry = document.getElementById("manual-category-retry");
    const input = name => document.getElementById(`manual-${name}`);
    const menus = [], buttons = [];
    const validation = new ManualValidation(form);
    const calendar = new ManualDatePicker(document, validation);
    validation.onChange = () => calendar.position();
    let categoryMenu, saving = false, loading = false, ready = false, draft = false, version = 0;
    for (const name of ["type", "source", "category"]) {
        const menu = name === "category"
            ? new GroupedCategoryMenu(document, { id: "manual-category-menu" })
            : new CategoryMenu(document, { id: `manual-${name}-menu` });
        // Popup content must belong to the dialog's top layer to be interactive.
        dialog.appendChild(menu.menu);
        const button = menu.enhance(input(name)); button.id = `manual-${name}-trigger`;
        const toggleMenu = button.onclick;
        button.onclick = () => { if (calendar.commit()) toggleMenu(); };
        document.getElementById(`manual-${name}-label`).htmlFor = button.id;
        if (name === "category") categoryMenu = menu;
        menus.push(menu); buttons.push(button);
    }
    function state() {
        fields.disabled = saving || loading || !ready;
        save.disabled = fields.disabled; cancel.disabled = saving; open.disabled = saving;
        retry.disabled = saving || loading;
        save.textContent = saving ? "Saving…" : "Save transaction";
        form.setAttribute("aria-busy", String(saving || loading));
        for (const button of buttons) button.setAttribute("aria-disabled", String(fields.disabled));
        if (fields.disabled) { menus.forEach(menu => menu.close()); calendar.close(); }
    }
    async function loadCatalog() {
        const request = ++version;
        ready = false; loading = true; retry.hidden = true;
        error.textContent = "Loading categories…"; error.dataset.loading = "true"; state();
        try {
            const catalog = await getCatalog();
            if (request !== version || !dialog.open) return;
            const selected = input("category").value;
            categoryMenu.configure(catalog.groups, catalog.categoryGroups);
            input("category").replaceChildren();
            for (const [value, label] of [["", "Uncategorized"], ...catalog.categories.map(category => [category.id, category.label])]) {
                const option = document.createElement("option"); option.value = value; option.textContent = label;
                input("category").appendChild(option);
            }
            input("category").value = catalog.categories.some(category => category.id === selected) ? selected : "";
            buttons[2].textContent = input("category").selectedOptions[0].textContent;
            ready = true; error.textContent = "";
        } catch {
            if (request !== version || !dialog.open) return;
            error.textContent = "Couldn’t load categories. Try again."; retry.hidden = false;
        } finally {
            if (request === version) {
                loading = false; error.dataset.loading = "false"; state();
                if (ready && dialog.open) input("amount").focus({ preventScroll: true });
            }
        }
    }
    open.onclick = () => {
        if (saving) return;
        if (!draft) {
            form.reset(); input("date").value = manualEntryTime();
            for (const [index, name] of ["type", "source"].entries()) buttons[index].textContent = input(name).selectedOptions[0].textContent;
            draft = true;
        }
        validation.clear(); calendar.reset();
        dialog.showModal(); void loadCatalog();
    };
    retry.onclick = () => { void loadCatalog(); };
    cancel.onclick = () => { if (!saving) dialog.close(); };
    dialog.oncancel = event => {
        if (saving) event.preventDefault();
        else if (!calendar.panel.hidden) { event.preventDefault(); calendar.close(); calendar.button.focus({ preventScroll: true }); }
    };
    dialog.onclose = () => { ++version; loading = false; menus.forEach(menu => menu.close()); calendar.close(); };
    form.onsubmit = async event => {
        event.preventDefault();
        if (saving || loading || !ready) return;
        error.textContent = "";
        if (!validation.validate() || !calendar.commit()) return;
        const payload = {
            amount: input("amount").value, transaction_type: input("type").value,
            merchant: input("merchant").value.trim(), transaction_date: input("date").value,
            source: input("source").value, category: input("category").value || null,
            account: input("account").value.trim(), reference: input("reference").value.trim() || null,
        };
        saving = true; error.textContent = ""; state();
        try {
            const saved = await saveManualTransaction(payload);
            draft = false; dialog.close();
            onMessage("Transaction saved. Updating dashboard…");
            try { await onSaved(saved); }
            catch { onMessage("Transaction saved. Couldn’t refresh the dashboard; refresh the page to see it."); }
        } catch (failure) { error.textContent = failure.message; }
        finally { saving = false; state(); }
    };
    state();
}

function initializeManualDeletion(document, { onMessage, onDeleted }) {
    const dialog = document.getElementById("delete-transaction-dialog");
    const cancel = document.getElementById("cancel-delete-transaction");
    const confirm = document.getElementById("confirm-delete-transaction");
    const error = document.getElementById("delete-transaction-error");
    let transaction = null, busy = false;
    cancel.onclick = () => { if (!busy) dialog.close(); };
    dialog.oncancel = event => { if (busy) event.preventDefault(); };
    confirm.onclick = async () => {
        if (busy || !transaction) return;
        busy = true; confirm.disabled = cancel.disabled = true;
        confirm.textContent = "Deleting…"; error.textContent = "";
        try {
            const response = await fetch(`/transactions/${transaction.id}`, { method: "DELETE", cache: "no-store" });
            if (!response.ok) {
                let detail;
                try { detail = (await response.json()).detail; } catch {}
                throw new Error(typeof detail === "string" ? detail : "Couldn’t delete the transaction. Try again.");
            }
            dialog.close();
            onMessage("Transaction deleted. Updating dashboard…");
            try { await onDeleted(); onMessage("Transaction deleted."); }
            catch { onMessage("Transaction deleted. Refresh the page to update the dashboard."); }
        } catch (failure) {
            error.textContent = failure instanceof TypeError
                ? "Connection lost. Refresh the dashboard to check whether the entry was deleted." : failure.message;
        } finally {
            busy = false; confirm.disabled = cancel.disabled = false; confirm.textContent = "Delete transaction";
        }
    };
    return { open(entry) {
        if (busy || entry.is_manual !== true) return;
        transaction = entry; error.textContent = "";
        document.getElementById("delete-transaction-description").textContent = `${entry.merchant} · ${entry.transaction_date.slice(0, 10)}`;
        dialog.showModal(); cancel.focus();
    } };
}
