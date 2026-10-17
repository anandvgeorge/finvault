"""Preview or apply body-only rules to saved, uncategorised transactions."""

import argparse
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


@dataclass(frozen=True)
class CategoryPreview:
    considered: int
    proposals: tuple[CategoryProposal, ...]
    unmatched: int
    missing_body: int
    unsupported_type: int


def preview_categories(
    connection: sqlite3.Connection, matcher: CategoryMatcher, *, month: str | None = None,
) -> CategoryPreview:
    """Only body text and parsed transaction type contribute to suggestions."""
    parameters = ()
    where = "t.category IS NULL"
    if month is not None:
        where += " AND substr(t.transaction_date, 1, 7) = ?"
        parameters = (calendar_month(month),)
    rows = connection.execute(
        f"""
        SELECT t.id, t.transaction_date, t.transaction_type, e.body_text
        FROM transactions t
        LEFT JOIN emails e ON e.gmail_id = t.email_id
        WHERE {where}
        ORDER BY t.transaction_date, t.id
        """,
        parameters,
    ).fetchall()
    proposals = []
    unmatched = missing_body = unsupported_type = 0
    for transaction_id, timestamp, kind, body in rows:
        if kind not in ("debit", "credit"):
            unsupported_type += 1
            continue
        if not body or not body.strip():
            missing_body += 1
            continue
        category = matcher.match(body, kind)
        if category is None:
            unmatched += 1
        else:
            proposals.append(CategoryProposal(transaction_id, timestamp, category))
    return CategoryPreview(len(rows), tuple(proposals), unmatched, missing_body, unsupported_type)


def apply_categories(connection: sqlite3.Connection, proposals: tuple[CategoryProposal, ...]) -> int:
    """Apply atomically, preserving categories assigned since the preview."""
    updated = 0
    with connection:
        for proposal in proposals:
            cursor = connection.execute(
                "UPDATE transactions SET category = ? WHERE id = ? AND category IS NULL",
                (proposal.category, proposal.transaction_id),
            )
            updated += cursor.rowcount
    return updated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview saved category rules against existing uncategorised transactions. No Gmail access or reparsing.",
    )
    parser.add_argument("--database", type=Path, default=Path("data/finvault.db"), help="Existing SQLite database (default: data/finvault.db)")
    parser.add_argument("--config", type=Path, help="Private category config (default: FINVAULT_CATEGORIES_CONFIG or categories.toml)")

    def month_argument(value):
        try:
            return calendar_month(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error)) from None

    parser.add_argument("--month", type=month_argument, help="Transaction month YYYY-MM; omit to include all months")
    parser.add_argument("--apply", action="store_true", help="Save matching categories; default is a read-only preview")
    args = parser.parse_args(argv)
    try:
        matcher = KeywordCategoryMatcher(TomlCategoryStore(args.config).load())
    except CategoryConfigError as error:
        parser.exit(1, f"Error: {error}\n")

    # URI modes refuse missing databases. Preview cannot write or create tables.
    uri = args.database.expanduser().resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro")
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
            preview = preview_categories(connection, matcher, month=args.month)
            updated = apply_categories(connection, preview.proposals) if args.apply else None
    except sqlite3.Error:
        parser.exit(1, "Error: Could not read or update categories; check the database path, permissions, and schema.\n")

    print(f"Scope: {args.month or 'all months'}; existing Uncategorized transactions only")
    print("ID | Date | Proposed category")
    for proposal in preview.proposals:
        print(f"{proposal.transaction_id} | {proposal.transaction_date[:10]} | {proposal.category}")
    print(
        f"Considered: {preview.considered}; Matches: {len(preview.proposals)}; "
        f"No match: {preview.unmatched}; Missing body: {preview.missing_body}; "
        f"Unsupported type: {preview.unsupported_type}"
    )
    if updated is None:
        print("Preview only. Add --apply to save assignments using the current saved rules.")
    else:
        print(f"Updated: {updated}; Skipped (already assigned or removed): {len(preview.proposals) - updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
