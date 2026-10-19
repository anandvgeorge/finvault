"""Offline backfill checks with temporary SQLite and fictional email bodies."""

from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from finvault.backfill_categories import apply_categories, calendar_month, main, preview_categories
from finvault.categories import TomlCategoryStore
from finvault.category_matching import KeywordCategoryMatcher
from finvault.database.database import Database
from finvault.models.category import Category, CategoryGroup, CategoryRule, CategorySettings
from tests.fixtures import email, transaction


class CategoryBackfillTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "fixture.db"
        self.database = Database(str(self.path))
        self.addCleanup(self.database.close)
        self.store = TomlCategoryStore(self.root / "categories.toml")
        self.settings = CategorySettings(
            categories=(Category(id="custom", label="Example category"), Category(id="another", label="Another category")),
            rules=(
                CategoryRule(category="custom", keywords=("fictional phrase",), transaction_type="debit"),
                CategoryRule(category="another", keywords=("fictional phrase",), transaction_type="credit"),
            ),
            groups=(CategoryGroup(id="spending_fixture", label="Lifestyle"),),
            category_groups={"custom": "spending_fixture", "another": "spending_fixture"},
        )
        self.store.save(self.settings)
        self.matcher = KeywordCategoryMatcher(self.settings)

    def save(self, name, *, body="A FICTIONAL\nphrase payment", date="2001-01-02T12:00:00+05:30", category=None, kind="debit"):
        self.database.save_email(email(name, body), "UPI")
        self.database.save_transaction(transaction(
            name, transaction_date=datetime.fromisoformat(date), category=category, transaction_type=kind,
        ))

    def connect(self, mode="ro"):
        return sqlite3.connect(self.path.as_uri() + f"?mode={mode}", uri=True)

    def categories(self):
        return dict(self.database.connection.execute("SELECT email_id, category FROM transactions"))

    def command(self, *options):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["--database", str(self.path), "--config", str(self.store.path), *options])
        self.assertEqual(result, 0)
        return output.getvalue()

    def test_default_preview_uses_read_only_connection_and_changes_no_files(self):
        self.save("fixture-match")
        self.save("fixture-no-match", body="An unrelated transaction")
        self.save("fixture-assigned", category="another")
        original = self.path.read_bytes(), self.store.path.read_bytes()
        real_connect = sqlite3.connect
        with patch("finvault.backfill_categories.sqlite3.connect", wraps=real_connect) as connect:
            output = self.command("--month", "2001-01")
        self.assertTrue(connect.call_args.args[0].endswith("?mode=ro"))
        self.assertIn("Considered: 2; Matches: 1; No match: 1", output)
        self.assertIn("1 | 2001-01-02 | Uncategorized -> custom", output)
        self.assertIn("Preview only", output)
        self.assertNotIn("fictional phrase", output.casefold())
        self.assertNotIn("Example Merchant", output)
        self.assertEqual((self.path.read_bytes(), self.store.path.read_bytes()), original)
        self.assertEqual(self.categories(), {"fixture-match": None, "fixture-no-match": None, "fixture-assigned": "another"})

    def test_month_filter_respects_year_and_both_calendar_boundaries(self):
        self.save("fixture-before", date="2000-12-31T23:59:59+05:30")
        self.save("fixture-start", date="2001-01-01T00:00:00+05:30")
        self.save("fixture-end", date="2001-01-31T23:59:59+05:30", kind="credit")
        self.save("fixture-after", date="2001-02-01T00:00:00+05:30")
        self.save("fixture-another-year", date="2002-01-02T12:00:00+05:30")
        with closing(self.connect()) as connection:
            preview = preview_categories(connection, self.matcher, month="2001-01")
        self.assertEqual(preview.considered, 2)
        self.assertEqual([(item.transaction_id, item.category) for item in preview.proposals], [(2, "custom"), (3, "another")])
        output = self.command("--month", "2001-01", "--apply")
        self.assertIn("Updated: 2", output)
        self.assertEqual(self.categories(), {"fixture-before": None, "fixture-start": "custom", "fixture-end": "another", "fixture-after": None, "fixture-another-year": None})

    def test_apply_changes_only_category_preserves_source_data_and_is_idempotent(self):
        self.save("fixture-match")
        self.save("fixture-no-match", body="Unrelated body")
        self.save("fixture-assigned", category="category_removed_from_config")
        self.database.record_ingestion_completed()
        columns = "id, amount, currency, transaction_type, merchant, transaction_date, account, source, reference, email_id"
        original = self.database.connection.execute(f"SELECT {columns} FROM transactions ORDER BY id").fetchall()
        emails = self.database.connection.execute("SELECT * FROM emails ORDER BY id").fetchall()
        metadata = self.database.runtime_status()
        self.assertIn("Updated: 1", self.command("--apply"))
        self.assertEqual(self.categories(), {"fixture-match": "custom", "fixture-no-match": None, "fixture-assigned": "category_removed_from_config"})
        self.assertEqual(self.database.connection.execute(f"SELECT {columns} FROM transactions ORDER BY id").fetchall(), original)
        self.assertEqual(self.database.connection.execute("SELECT * FROM emails ORDER BY id").fetchall(), emails)
        self.assertEqual(self.database.runtime_status(), metadata)
        self.assertIn("Updated: 0", self.command("--apply"))
        with closing(self.connect()) as reopened:
            self.assertEqual(reopened.execute("SELECT category FROM transactions WHERE id=1").fetchone(), ("custom",))

    def test_matching_uses_only_plain_body_not_sender_subject_merchant_or_html(self):
        record = email("fixture-metadata", "Unrelated body")
        record.sender = "Fictional Phrase <alerts@example.invalid>"
        record.subject = "Fictional Phrase"
        record.body_html = '<p data-tracking="fictional phrase">Unrelated body</p>'
        self.database.save_email(record, "UPI")
        self.database.save_transaction(transaction(record.gmail_id, merchant="Fictional Phrase"))
        self.assertIn("Matches: 0; No match: 1", self.command("--apply"))
        self.assertEqual(self.categories(), {"fixture-metadata": None})

    def test_missing_email_empty_body_and_whitespace_are_reported_and_preserved(self):
        self.save("fixture-empty", body="")
        self.save("fixture-whitespace", body=" \n\t")
        self.database.save_transaction(transaction("fixture-missing-email"))
        self.assertIn("Missing body: 3", self.command("--apply"))
        self.assertTrue(all(value is None for value in self.categories().values()))

    def test_unsupported_transaction_type_is_reported_without_failing_other_matches(self):
        self.save("fixture-unsupported", kind="unknown")
        self.save("fixture-match")
        output = self.command("--apply")
        self.assertIn("Unsupported type: 1", output)
        self.assertIn("Updated: 1", output)
        self.assertEqual(self.categories(), {"fixture-unsupported": None, "fixture-match": "custom"})

    def test_category_assigned_after_preview_is_preserved_at_update_time(self):
        self.save("fixture-concurrent")
        with closing(self.connect()) as connection:
            preview = preview_categories(connection, self.matcher)
        self.database.update_transaction_category(1, "another")
        with closing(self.connect("rw")) as connection:
            self.assertEqual(apply_categories(connection, preview.proposals), 0)
        self.assertEqual(self.categories(), {"fixture-concurrent": "another"})

    def test_selected_categories_change_only_on_a_match_without_reparsing(self):
        self.save("fixture-replace", category="another")
        self.save("fixture-clear", category="custom", body="No matching rule")
        self.save("fixture-unchanged", category="custom")
        self.save("fixture-empty", category="another", body="")
        self.save("fixture-unsupported", category="another", kind="unknown")
        self.save("fixture-outside", category="another", date="2001-02-01T00:00:00+05:30")
        self.save("fixture-protected", category="protected")
        self.save("fixture-protected-no-match", category="protected", body="No matching rule")
        self.store.save(self.settings.model_copy(update={
            "categories": self.settings.categories + (Category(id="protected", label="Salary"),),
        }))
        before = list(self.database.connection.iterdump())
        with closing(self.connect()) as connection:
            preview = preview_categories(connection, self.matcher, month="2001-01", source_categories=[None, "custom", "another"])
        self.assertEqual((preview.considered, preview.matched, preview.unmatched, preview.missing_body, preview.unsupported_type), (5, 2, 1, 1, 1))
        self.assertEqual([(p.transaction_id, p.previous_category, p.category) for p in preview.proposals], [(1, "another", "custom")])
        self.assertEqual(list(self.database.connection.iterdump()), before)
        columns = "id, amount, currency, transaction_type, merchant, transaction_date, account, source, reference, email_id"
        transactions = self.database.connection.execute(f"SELECT {columns} FROM transactions ORDER BY id").fetchall()
        emails = self.database.connection.execute("SELECT * FROM emails ORDER BY id").fetchall()
        output = self.command("--category", "custom", "--category", "another", "--include-uncategorized", "--month", "2001-01", "--apply")
        self.assertIn("No matching rule keeps the current category", output)
        self.assertIn("Updated: 1", output)
        self.assertEqual(self.categories(), {
            "fixture-replace": "custom", "fixture-clear": "custom", "fixture-unchanged": "custom",
            "fixture-empty": "another", "fixture-unsupported": "another", "fixture-outside": "another",
            "fixture-protected": "protected", "fixture-protected-no-match": "protected",
        })
        self.assertEqual(self.database.connection.execute(f"SELECT {columns} FROM transactions ORDER BY id").fetchall(), transactions)
        self.assertEqual(self.database.connection.execute("SELECT * FROM emails ORDER BY id").fetchall(), emails)
        self.assertIn("Updated: 0", self.command("--category", "custom", "--category", "another", "--include-uncategorized", "--month", "2001-01", "--apply"))

    def test_selected_category_updates_preserve_concurrent_edits_and_roll_back(self):
        self.save("fixture-replace", category="another")
        self.save("fixture-clear", category="custom", kind="credit")
        with closing(self.connect()) as connection:
            preview = preview_categories(connection, self.matcher, source_categories=[None, "custom", "another"])
        self.database.update_transaction_category(1, None)
        self.database.update_transaction_category(2, "another")
        with closing(self.connect("rw")) as connection:
            self.assertEqual(apply_categories(connection, preview.proposals), 0)
        self.assertEqual(self.categories(), {"fixture-replace": None, "fixture-clear": "another"})
        self.database.update_transaction_category(2, "custom")
        self.database.connection.execute("""
            CREATE TRIGGER reject_reapply BEFORE UPDATE OF category ON transactions
            WHEN NEW.id = 2 BEGIN SELECT RAISE(ABORT, 'Synthetic failure'); END
        """)
        self.database.connection.commit()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.command("--category", "custom", "--include-uncategorized", "--apply")
        self.assertEqual(self.categories(), {"fixture-replace": None, "fixture-clear": "custom"})

    def test_source_selection_is_explicit_and_can_exclude_uncategorized(self):
        self.save("fixture-uncategorized")
        self.save("fixture-assigned", category="another")
        for selected, considered in (([None], 1), (["another"], 1), ([None, "another"], 2), ([], 0)):
            with self.subTest(selected=selected), closing(self.connect()) as connection:
                preview = preview_categories(connection, self.matcher, source_categories=selected)
                self.assertEqual(preview.considered, considered)
        self.assertIn("Updated: 1", self.command("--category", "another", "--apply"))
        self.assertEqual(self.categories(), {"fixture-uncategorized": None, "fixture-assigned": "custom"})
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.command("--category", "unknown_category", "--apply")

    def test_failure_rolls_back_the_whole_batch_and_reports_no_private_content(self):
        self.save("fixture-first")
        self.save("fixture-second")
        self.database.connection.execute("""
            CREATE TRIGGER reject_category BEFORE UPDATE OF category ON transactions
            WHEN NEW.id = 2 BEGIN SELECT RAISE(ABORT, 'Synthetic failure'); END
        """)
        self.database.connection.commit()
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit) as caught:
            self.command("--apply")
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("Could not read or update categories", output.getvalue())
        self.assertNotIn("fictional phrase", output.getvalue())
        self.assertEqual(self.categories(), {"fixture-first": None, "fixture-second": None})

    def test_missing_database_is_never_created_in_preview_or_apply_mode(self):
        missing = self.root / "does-not-exist.db"
        for options in ([], ["--apply"]):
            with self.subTest(options=options), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["--database", str(missing), "--config", str(self.store.path), *options])
            self.assertFalse(missing.exists())

    def test_invalid_month_is_rejected_before_reading_config(self):
        for month in ("2001-1", "2001-00", "2001-13", "0000-01", "2001-01 OR 1=1"):
            with self.subTest(month=month), patch("finvault.backfill_categories.TomlCategoryStore") as store:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    main(["--month", month])
                self.assertEqual(caught.exception.code, 2)
                store.assert_not_called()
        self.assertEqual(calendar_month("0001-01"), "0001-01")

    def test_invalid_config_is_reported_safely_and_does_not_change_categories(self):
        self.save("fixture-match")
        self.store.path.write_text('[[rules]]\ncategory="missing"\nkeywords=["fictional private keyword"]\n')
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit):
            self.command("--apply")
        self.assertIn("Invalid category settings", output.getvalue())
        self.assertNotIn("fictional private keyword", output.getvalue())
        self.assertEqual(self.categories(), {"fixture-match": None})

    def test_empty_rules_produce_no_assignments(self):
        self.save("fixture-match")
        self.store.save(CategorySettings(categories=self.settings.categories))
        self.assertIn("Updated: 0", self.command("--apply"))
        self.assertEqual(self.categories(), {"fixture-match": None})

    def test_module_command_supports_explicit_paths_from_another_directory(self):
        self.save("fixture-match")
        result = subprocess.run(
            [sys.executable, "-m", "finvault.backfill_categories", "--database", str(self.path),
             "--config", str(self.store.path), "--month", "2001-01"],
            cwd=self.root, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Matches: 1", result.stdout)
        self.assertIn("Preview only", result.stdout)
        self.assertEqual(self.categories(), {"fixture-match": None})
