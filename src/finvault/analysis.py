"""Read-only summaries using the current category-to-group assignments."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from finvault.models.category import CategorySettings


@dataclass
class _Totals:
    debits: Decimal = Decimal("0.00")
    credits: Decimal = Decimal("0.00")
    count: int = 0

    def add(self, amount: Decimal, transaction_type: str) -> None:
        if transaction_type == "debit":
            self.debits += amount
        elif transaction_type == "credit":
            self.credits += amount
        else:
            raise ValueError("Unknown transaction type")
        self.count += 1

    def as_dict(self) -> dict:
        # Decimal strings preserve the stored monetary precision in JSON.
        return {
            "total_debits": format(self.debits, "f"),
            "total_credits": format(self.credits, "f"),
            "transaction_count": self.count,
        }


def summarize_groups(
    rows: Iterable[tuple[str | None, str, str]], settings: CategorySettings,
) -> dict:
    """Account for each (category, type, amount) row exactly once.

    Configured groups and independent categories retain their config order,
    including empty ones. Removed category IDs stay visible as independent
    entries; they are distinct from genuinely uncategorized transactions.
    """
    totals, uncategorized = _Totals(), _Totals()
    groups = {group.id: _Totals() for group in settings.groups}
    labels = {category.id: category.label for category in settings.categories}
    independent = {
        category.id: _Totals() for category in settings.categories
        if category.id not in settings.category_groups
    }
    for category, transaction_type, stored_amount in rows:
        amount = Decimal(stored_amount)
        if category is None:
            target = uncategorized
        elif category in settings.category_groups:
            target = groups[settings.category_groups[category]]
        else:
            if category not in independent:
                independent[category] = _Totals()
            target = independent[category]
        target.add(amount, transaction_type)
        totals.add(amount, transaction_type)

    income_group = settings.resolved_income_group
    income = groups[income_group].credits if income_group is not None else None

    def percentage(group_id: str, values: _Totals) -> str | None:
        if income is None or income <= 0:
            return None
        amount = values.credits if group_id == income_group else values.debits
        return str((amount / income * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))

    return {
        "income_group": income_group,
        "income_total": format(income, "f") if income is not None else None,
        "totals": totals.as_dict(),
        "groups": [
            {"id": group.id, "label": group.label, **groups[group.id].as_dict(),
             "income_percentage": percentage(group.id, groups[group.id])}
            for group in settings.groups
        ],
        "independent": [
            {"id": category, "label": labels.get(category, f"{category} (unavailable)"), **values.as_dict()}
            for category, values in independent.items()
        ],
        "uncategorized": uncategorized.as_dict(),
    }
