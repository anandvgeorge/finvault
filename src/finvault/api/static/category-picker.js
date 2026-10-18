"use strict";

function categoryPickerSections(options, groups, assignments) {
    const sections = groups.map((group, index) => ({ ...group, key: `group-${index}`, options: [] }));
    const independent = { id: null, label: "Independent categories", key: "independent", options: [] };
    const byId = new Map(sections.map(section => [section.id, section]));
    const direct = [];
    Array.from(options).forEach((option, index) => {
        const item = { kind: "option", index, key: `option-${index}`, label: option.textContent };
        if (option.value === "") direct.push(item);
        else {
            const groupId = Object.hasOwn(assignments, option.value) ? assignments[option.value] : null;
            const section = byId.get(groupId) ?? independent;
            item.sectionKey = section.key;
            section.options.push(item);
        }
    });
    const collator = new Intl.Collator("en", { sensitivity: "base", numeric: true });
    sections.push(independent);
    sections.forEach(section => section.options.sort((a, b) => collator.compare(a.label, b.label)));
    return { direct, sections: sections.filter(section => section.id !== null || section.options.length) };
}

// A tree popup keeps group expansion separate from assigning a leaf category.
// The native select remains the source of values and change events.
class GroupedCategoryMenu extends CategoryMenu {
    constructor(document, options = {}) {
        super(document, options);
        this.menu.setAttribute("role", "tree");
        this.menu.classList.add("grouped-category-menu");
        this.configure([], {});
    }

    configure(groups, assignments) {
        this.close();
        this.groups = groups.map(group => ({ id: group.id, label: group.label }));
        this.assignments = { ...assignments };
    }

    enhance(select) {
        const button = super.enhance(select);
        button.setAttribute("aria-haspopup", "tree");
        button.onblur = () => { if (this.button === button) this.close(); };
        return button;
    }

    open(select, button) {
        if (button.getAttribute("aria-disabled") === "true" || !select.options.length) return;
        this.close();
        this.select = select; this.button = button;
        this.search = ""; this.searchTime = 0;
        const structure = categoryPickerSections(select.options, this.groups, this.assignments);
        this.structure = structure;
        this.choices = [...structure.direct];
        structure.sections.forEach(section => {
            this.choices.push({ kind: "group", key: section.key, label: section.label, section });
            this.choices.push(...section.options);
        });
        const selected = this.choices.find(item => item.kind === "option" && item.index === select.selectedIndex);
        this.expandedGroup = selected?.sectionKey ?? null;
        this.menu.setAttribute("aria-label", select.getAttribute("aria-label"));
        button.setAttribute("aria-expanded", "true");
        this.menu.hidden = false;
        this.render(selected?.key);
    }

    option(item, parent, level) {
        const node = this.document.createElement("div");
        node.id = `${this.menu.id}-${item.key}`;
        node.className = "category-option";
        node.textContent = item.label;
        node.setAttribute("role", "treeitem");
        node.setAttribute("aria-level", String(level));
        node.setAttribute("aria-selected", String(item.index === this.select.selectedIndex));
        node.dataset.value = this.select.options[item.index].value;
        const entry = { ...item, node, display: node };
        node.onpointermove = event => { if (event.pointerType === "mouse") this.highlight(this.rows.indexOf(entry)); };
        node.onclick = () => this.activate(this.rows.indexOf(entry));
        this.rows.push(entry); parent.appendChild(node);
    }

    render(activeKey) {
        this.menu.replaceChildren(); this.rows = [];
        this.structure.direct.forEach(item => this.option(item, this.menu, 1));
        this.structure.sections.forEach(section => {
            const expanded = section.key === this.expandedGroup;
            const node = this.document.createElement("div"); node.className = "category-group";
            node.id = `${this.menu.id}-${section.key}`; node.dataset.groupId = section.id ?? "";
            node.setAttribute("role", "treeitem"); node.setAttribute("aria-level", "1");
            node.setAttribute("aria-label", section.label); node.setAttribute("aria-expanded", String(expanded));
            const heading = this.document.createElement("div"); heading.className = "category-group-heading";
            heading.textContent = section.label;
            const entry = { kind: "group", key: section.key, section, node, display: heading };
            this.rows.push(entry);
            heading.onpointermove = event => { if (event.pointerType === "mouse") this.highlight(this.rows.indexOf(entry)); };
            heading.onclick = () => this.activate(this.rows.indexOf(entry));
            const children = this.document.createElement("div"); children.setAttribute("role", "group");
            children.className = "category-group-options"; children.hidden = !expanded;
            if (expanded) {
                section.options.forEach(item => this.option(item, children, 2));
                if (!section.options.length) {
                    const empty = this.document.createElement("p"); empty.className = "category-menu-empty";
                    empty.textContent = "No categories"; children.appendChild(empty);
                }
            }
            node.appendChild(heading); node.appendChild(children); this.menu.appendChild(node);
        });
        this.position();
        const index = this.rows.findIndex(item => item.key === activeKey);
        this.highlight(index < 0 ? 0 : index);
    }

    highlight(index) {
        this.activeIndex = Math.max(0, Math.min(index, this.rows.length - 1));
        this.rows.forEach((item, position) => { item.display.dataset.active = String(position === this.activeIndex); });
        const active = this.rows[this.activeIndex];
        if (!active) return;
        this.button.setAttribute("aria-activedescendant", active.node.id);
        const rect = active.display.getBoundingClientRect(), menuRect = this.menu.getBoundingClientRect();
        if (rect.top < menuRect.top) this.menu.scrollTop -= menuRect.top - rect.top;
        else if (rect.bottom > menuRect.bottom) this.menu.scrollTop += rect.bottom - menuRect.bottom;
    }

    activate(index) {
        this.highlight(index);
        const item = this.rows[this.activeIndex];
        if (item.kind === "group") {
            this.expandedGroup = this.expandedGroup === item.key ? null : item.key;
            this.render(item.key);
        } else {
            const button = this.button;
            this.close(true); button.focus();
        }
    }

    close(commit = false) {
        if (!this.button) return;
        const select = this.select, item = this.rows?.[this.activeIndex];
        const assign = commit && item?.kind === "option";
        const changed = assign && select.selectedIndex !== item.index;
        if (assign) {
            select.selectedIndex = item.index;
            this.button.textContent = select.options[item.index].textContent;
        }
        super.close();
        this.rows = [];
        if (changed) select.dispatchEvent(new this.view.Event("change", { bubbles: true }));
    }

    keydown(event, select, button) {
        if (button.getAttribute("aria-disabled") === "true") {
            if (event.key !== "Tab") event.preventDefault();
            return;
        }
        const key = event.key, open = this.button === button;
        if (open && event.altKey && key === "ArrowUp") { event.preventDefault(); this.close(); return; }
        if (key === "Escape" || key === "Tab") {
            if (open) { if (key === "Escape") event.preventDefault(); this.close(); }
            return;
        }
        const navigation = ["Enter", " ", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"];
        if (navigation.includes(key)) {
            event.preventDefault();
            if (!open) { this.open(select, button); if (!this.button) return; }
            const item = this.rows[this.activeIndex];
            if (open && (key === "Enter" || key === " ")) this.activate(this.activeIndex);
            else if (key === "Home") this.highlight(0);
            else if (key === "End") this.highlight(this.rows.length - 1);
            else if (open && (key === "ArrowDown" || key === "ArrowUp")) this.highlight(this.activeIndex + (key === "ArrowDown" ? 1 : -1));
            else if (key === "PageDown" || key === "PageUp") this.highlight(this.activeIndex + (key === "PageDown" ? 10 : -10));
            else if (key === "ArrowRight" && item.kind === "group") {
                if (this.expandedGroup !== item.key) { this.expandedGroup = item.key; this.render(item.key); }
                else if (item.section.options.length) this.highlight(this.activeIndex + 1);
            } else if (key === "ArrowLeft") {
                if (item.kind === "group" && this.expandedGroup === item.key) { this.expandedGroup = null; this.render(item.key); }
                else if (item.sectionKey) this.highlight(this.rows.findIndex(row => row.key === item.sectionKey));
            }
            return;
        }
        if (key.length !== 1 || event.ctrlKey || event.metaKey || event.altKey) return;
        event.preventDefault();
        if (!open) this.open(select, button);
        if (!this.button) return;
        const now = Date.now();
        this.search = now - this.searchTime < 700 ? this.search + key.toLowerCase() : key.toLowerCase();
        this.searchTime = now;
        const repeated = [...this.search].every(character => character === this.search[0]);
        const query = repeated ? this.search[0] : this.search;
        const activeKey = this.rows[this.activeIndex]?.key;
        const start = repeated ? this.choices.findIndex(item => item.key === activeKey) + 1 : 0;
        for (let offset = 0; offset < this.choices.length; offset++) {
            const item = this.choices[(start + offset) % this.choices.length];
            if (!item.label.toLowerCase().startsWith(query)) continue;
            if (item.sectionKey) this.expandedGroup = item.sectionKey;
            this.render(item.key); break;
        }
    }
}
