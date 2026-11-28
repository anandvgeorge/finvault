function initializeComments(document, onSaved) {
    const dialog = document.getElementById("comment-dialog");
    const form = document.getElementById("comment-form");
    const input = document.getElementById("transaction-comment");
    const error = document.getElementById("comment-error");
    const save = document.getElementById("save-comment");
    const cancel = document.getElementById("cancel-comment");
    let transaction, saving = false;

    cancel.onclick = () => dialog.close();
    dialog.oncancel = event => { if (saving) event.preventDefault(); };
    form.onsubmit = async event => {
        event.preventDefault();
        if (saving || !form.reportValidity()) return;
        saving = true;
        save.disabled = cancel.disabled = input.disabled = true;
        error.textContent = "Saving…";
        const comment = input.value.trim() || null;
        try {
            const response = await fetch(`/transactions/${transaction.id}/comment`, {
                method: "PATCH", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ comment }),
            });
            if (!response.ok) throw new Error("Save failed");
            const saved = await response.json();
            if (saved.id !== transaction.id || saved.comment !== comment) throw new Error("Unexpected response");
            transaction.comment = saved.comment;
            dialog.close();
            onSaved(saved);
        } catch {
            error.textContent = "Could not save the comment. Your draft is here. Try again.";
        } finally {
            saving = false;
            save.disabled = cancel.disabled = input.disabled = false;
        }
    };

    return {
        open(record) {
            if (saving) return;
            transaction = record;
            document.getElementById("comment-merchant").textContent = record.merchant;
            input.value = record.comment || "";
            error.textContent = "";
            dialog.showModal();
            input.focus();
        },
    };
}
