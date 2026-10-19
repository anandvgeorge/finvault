"""Preview or apply body-only rules to saved transactions."""

import argparse
from collections.abc import Collection
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import sqlite3

from finvault.categories import CategoryConfigError, TomlCategoryStore
from finvault.category_matching import CategoryMatcher, KeywordCategoryMatcher


def calendar_month(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", value):
        raise ValueError("Month must be a valid YYYY-MM value")
    try:
        date.fromisoformat(value + "-01")
    except ValueError:
        raise ValueError("Month must be a valid YYYY-MM value") from None
    return value


@dataclass(frozen=True)
class CategoryProposal:
    transaction_id: int
    transaction_date: str
    category: str
    previous_category: str | None = None


@dataclass(frozen=True)
class CategoryPreview:
    considered: int
    proposals: tuple[CategoryProposal, ...]
    unmatched: int
    missing_body: int
    unsupported_type: int
    matched: int


def preview_categories(
    connection: sqlite3.Connection, matcher: CategoryMatcher, *, month: str | None = None,
    source_categories: Collection[str | None] = (None,),
) -> CategoryPreview:
    """Only body text and parsed transaction type contribute to suggestions."""
    selected = set(source_categories)
    parameters = tuple(sorted(category for category in selected if category is not None))
    conditions = ["t.category IS NULL"] if None in selected else []
    if parameters:
        placeholders = ", ".join("?" for _ in parameters)
        conditions.append(f"t.category IN ({placeholders})")
    where = "(" + " OR ".join(conditions) + ")" if conditions else "0 = 1"
    if month is not None:
        where += " AND substr(t.transaction_date, 1, 7) = ?"
        parameters += (calendar_month(month),)
    rows = connection.execute(
        f"""
        SELECT t.id, t.transaction_date, t.transaction_type, e.body_text, t.category
        FROM transactions t
        LEFT JOIN emails e ON e.gmail_id = t.email_id
        WHERE {where}
        ORDER BY t.transaction_date, t.id
        """,
        parameters,
    ).fetchall()
    proposals = []
    unmatched = missing_body = unsupported_type = matched = 0
    for transaction_id, timestamp, kind, body, previous_category in rows:
        if kind not in ("debit", "credit"):
            unsupported_type += 1
            continue
        if not body or not body.strip():
            missing_body += 1
            continue
        category = matcher.match(body, kind)
        if category is None:
            unmatched += 1
            continue
        matched += 1
        if category != previous_category:
            proposals.append(CategoryProposal(transaction_id, timestamp, category, previous_category))
    return CategoryPreview(len(rows), tuple(proposals), unmatched, missing_body, unsupported_type, matched)


def apply_categories(connection: sqlite3.Connection, proposals: tuple[CategoryProposal, ...]) -> int:
    """Apply changes atomically, preserving assignments edited since the preview."""
    updated = 0
    with connection:
        for proposal in proposals:
            cursor = connection.execute(
                "UPDATE transactions SET category = ? WHERE id = ? AND category IS ?",
                (proposal.category, proposal.transaction_id, proposal.previous_category),
            )
            updated += cursor.rowcount
    return updated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview saved category rules against stored transactions. Defaults to Uncategorized only. No Gmail access or reparsing.",
    )
    parser.add_argument("--database", type=Path, default=Path("data/finvault.db"), help="Existing SQLite database (default: data/finvault.db)")
    parser.add_argument("--config", type=Path, help="Private category config (default: FINVAULT_CATEGORIES_CONFIG or categories.toml)")

    def month_argument(value):
        try:
            return calendar_month(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error)) from None

    parser.add_argument("--month", type=month_argument, help="Transaction month YYYY-MM; omit to include all months")
    parser.add_argument("--apply", action="store_true", help="Save proposed category changes; default is a read-only preview")
    parser.add_argument("--category", action="append", default=[], help="Existing category ID to recheck; repeat for multiple categories")
    parser.add_argument("--include-uncategorized", action="store_true", help="Include Uncategorized alongside the selected categories (the default when no --category is given)")
    args = parser.parse_args(argv)
    try:
        settings = TomlCategoryStore(args.config).load()
        matcher = KeywordCategoryMatcher(settings)
    except CategoryConfigError as error:
        parser.exit(1, f"Error: {error}\n")
    known = {category.id for category in settings.categories}
    if set(args.category) - known:
        parser.error("Every --category must be an ID in your saved category settings")
    selected = list(dict.fromkeys(args.category))
    if args.include_uncategorized or not selected:
        selected.append(None)

    # URI modes refuse missing databases. Preview cannot write or create tables.
    uri = args.database.expanduser().resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro")
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
            preview = preview_categories(connection, matcher, month=args.month, source_categories=selected)
            updated = apply_categories(connection, preview.proposals) if args.apply else None
    except sqlite3.Error:
        parser.exit(1, "Error: Could not read or update categories; check the database path, permissions, and schema.\n")

    scope = ", ".join(category or "Uncategorized" for category in selected)
    print(f"Scope: {args.month or 'all months'}; {scope}")
    print("ID | Date | Current category -> Proposed category")
    for proposal in preview.proposals:
        print(f"{proposal.transaction_id} | {proposal.transaction_date[:10]} | {proposal.previous_category or 'Uncategorized'} -> {proposal.category}")
    print(
        f"Considered: {preview.considered}; Matches: {preview.matched}; "
        f"No match: {preview.unmatched}; Missing body: {preview.missing_body}; "
        f"Unsupported type: {preview.unsupported_type}"
    )
    print(f"Changes: {len(preview.proposals)}; No matching rule keeps the current category.")
    if updated is None:
        print("Preview only. Add --apply to save assignments using the current saved rules.")
    else:
        print(f"Updated: {updated}; Skipped (assignment changed or transaction removed): {len(preview.proposals) - updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
