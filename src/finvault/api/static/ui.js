"use strict";

class CategoryMenu {
    constructor(document, { id = "category-menu", triggerClass = "category-trigger" } = {}) {
        this.document = document;
        this.view = document.defaultView;
        this.triggerClass = triggerClass;
        this.menu = document.createElement("div");
        this.menu.id = id;
        this.menu.className = "category-menu";
        this.menu.setAttribute("role", "listbox");
        this.menu.hidden = true;
        document.body.appendChild(this.menu);
        this.menu.onpointerdown = event => event.preventDefault();
        document.addEventListener("pointerdown", event => {
            if (this.button && event.target !== this.button && !this.menu.contains(event.target)) this.close();
        });
        document.addEventListener("scroll", event => {
            if (!this.menu.contains(event.target)) this.close();
        }, true);
        this.view.addEventListener("resize", () => this.close());
    }

    enhance(select) {
        const button = this.document.createElement("button");
        button.type = "button";
        button.className = this.triggerClass;
        button.textContent = select.options[select.selectedIndex].textContent;
        button.setAttribute("role", "combobox");
        button.setAttribute("aria-label", select.getAttribute("aria-label"));
        button.setAttribute("aria-haspopup", "listbox");
        button.setAttribute("aria-controls", this.menu.id);
        button.setAttribute("aria-expanded", "false");
        button.onclick = () => {
            if (button.getAttribute("aria-disabled") !== "true") {
                this.button === button ? this.close() : this.open(select, button);
            }
        };
        button.onkeydown = event => this.keydown(event, select, button);
        button.onblur = () => { if (this.button === button) this.close(true); };
        select.hidden = true;
        select.parentNode.appendChild(button);
        return button;
    }

    open(select, button) {
        if (button.getAttribute("aria-disabled") === "true") return;
        this.close();
        this.select = select;
        this.button = button;
        this.search = "";
        this.searchTime = 0;
        this.menu.replaceChildren();
        this.menu.setAttribute("aria-label", select.getAttribute("aria-label"));
        this.items = Array.from(select.options, (option, index) => {
            const item = this.document.createElement("div");
            item.id = `${this.menu.id}-${index}`;
            item.className = "category-option";
            item.textContent = option.textContent;
            item.setAttribute("role", "option");
            item.setAttribute("aria-selected", String(index === select.selectedIndex));
            item.onpointermove = () => this.highlight(index);
            item.onclick = () => { this.highlight(index); this.close(true); button.focus(); };
            this.menu.appendChild(item);
            return item;
        });
        button.setAttribute("aria-expanded", "true");
        this.menu.hidden = false;
        this.position();
        this.highlight(select.selectedIndex);
    }

    position() {
        const button = this.button;
        const rect = button.getBoundingClientRect();
        const width = Math.min(rect.width, this.view.innerWidth - 16);
        this.menu.style.width = `${width}px`;
        this.menu.style.maxHeight = "";
        const below = this.view.innerHeight - rect.bottom - 14;
        const above = rect.top - 14;
        const down = below >= Math.min(this.menu.scrollHeight, 280) || below >= above;
        this.menu.style.left = `${Math.max(8, Math.min(rect.left, this.view.innerWidth - width - 8))}px`;
        this.menu.style.maxHeight = `${Math.max(0, Math.min(280, down ? below : above))}px`;
        this.menu.style.top = `${down ? rect.bottom + 6 : rect.top - this.menu.getBoundingClientRect().height - 6}px`;
    }

    highlight(index) {
        this.activeIndex = Math.max(0, Math.min(index, this.items.length - 1));
        this.items.forEach((item, position) => { item.dataset.active = String(position === this.activeIndex); });
        const active = this.items[this.activeIndex];
        this.button.setAttribute("aria-activedescendant", active.id);
        if (active.offsetTop < this.menu.scrollTop) this.menu.scrollTop = active.offsetTop;
        else if (active.offsetTop + active.offsetHeight > this.menu.scrollTop + this.menu.clientHeight) {
            this.menu.scrollTop = active.offsetTop + active.offsetHeight - this.menu.clientHeight;
        }
    }

    close(commit = false) {
        if (!this.button) return;
        const select = this.select;
        const changed = commit && select.selectedIndex !== this.activeIndex;
        if (commit) {
            select.selectedIndex = this.activeIndex;
            this.button.textContent = select.options[this.activeIndex].textContent;
        }
        this.button.setAttribute("aria-expanded", "false");
        this.button.removeAttribute("aria-activedescendant");
        this.menu.hidden = true;
        this.menu.replaceChildren();
        this.button = this.select = null;
        // A change handler can disable the button or rerender the row.
        if (changed) select.dispatchEvent(new this.view.Event("change", { bubbles: true }));
    }

    keydown(event, select, button) {
        if (button.getAttribute("aria-disabled") === "true") {
            if (event.key !== "Tab") event.preventDefault();
            return;
        }
        const open = this.button === button;
        const key = event.key;
        if (key === "Escape") {
            if (open) { event.preventDefault(); this.close(); }
            return;
        }
        if (key === "Tab") { if (open) this.close(true); return; }
        if (["Enter", " ", "ArrowDown", "ArrowUp", "Home", "End", "PageUp", "PageDown"].includes(key)) {
            event.preventDefault();
            if (!open) this.open(select, button);
            else if (key === "Enter" || key === " " || (event.altKey && key === "ArrowUp")) { this.close(true); return; }
            if (key === "Home" || (!open && key === "ArrowUp")) this.highlight(0);
            else if (key === "End") this.highlight(this.items.length - 1);
            else if (open && key.startsWith("Arrow")) this.highlight(this.activeIndex + (key === "ArrowDown" ? 1 : -1));
            else if (key === "PageDown" || key === "PageUp") this.highlight(this.activeIndex + (key === "PageDown" ? 10 : -10));
        } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault();
            if (!open) this.open(select, button);
            const now = Date.now();
            this.search = now - this.searchTime < 700 ? this.search + key.toLowerCase() : key.toLowerCase();
            this.searchTime = now;
            const repeated = [...this.search].every(character => character === this.search[0]);
            const query = repeated ? this.search[0] : this.search;
            const start = repeated ? this.activeIndex + 1 : 0;
            for (let offset = 0; offset < this.items.length; offset++) {
                const index = (start + offset) % this.items.length;
                if (this.items[index].textContent.toLowerCase().startsWith(query)) { this.highlight(index); break; }
            }
        }
    }
}
