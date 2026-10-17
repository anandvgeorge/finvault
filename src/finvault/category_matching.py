"""Config-driven keyword matching against email body text only.

This module performs no file, database, or network operations. Callers supply
validated settings and the plain-text body (including text extracted from HTML).
"""

from typing import Literal, Protocol

from finvault.models.category import CategoryId, CategorySettings

TransactionType = Literal["debit", "credit"]


def _normalize(text: str) -> str:
    """Ignore case and email line wrapping when matching literal phrases."""
    return " ".join(text.split()).casefold()


class CategoryMatcher(Protocol):
    def match(self, body: str, transaction_type: TransactionType) -> CategoryId | None:
        """Return a matching category ID, or None for an uncategorised body."""
        ...


class KeywordCategoryMatcher:
    def __init__(self, settings: CategorySettings):
        self._rules = tuple(
            (rule.category, rule.transaction_type, tuple(_normalize(keyword) for keyword in rule.keywords))
            for rule in settings.rules
        )

    def match(self, body: str, transaction_type: TransactionType) -> CategoryId | None:
        if transaction_type not in ("debit", "credit"):
            raise ValueError("Transaction type must be debit or credit")

        normalized_body = _normalize(body)
        for category, required_type, keywords in self._rules:
            if required_type is not None and required_type != transaction_type:
                continue
            if any(keyword in normalized_body for keyword in keywords):
                return category
        return None
