"""Ingestion checks with fake Gmail responses and an in-memory database."""

import unittest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import Mock, call, patch

from finvault.categories import CategoryConfigError, TomlCategoryStore
from finvault.database.database import Database
from finvault.ingestion.email_ingestor import EmailIngestor
from tests.fixtures import email
from finvault.__main__ import main
from finvault.models.category import Category, CategoryRule, CategorySettings


class EmailIngestorTests(unittest.TestCase):
    def setUp(self):
        self.database = Database(":memory:")
        self.addCleanup(self.database.close)
        self.gmail = Mock()
        self.ingestor = EmailIngestor(self.gmail, self.database)

    def test_repeated_ingestion_and_overlapping_labels_do_not_refetch_emails(self):
        self.database.save_email(email("fixture-existing"), "Credit")
        self.gmail.get_messages.return_value = [
            {"id": "fixture-existing"}, {"id": "fixture-new"},
        ]
        self.gmail.get_email.return_value = email("fixture-new")
        self.ingestor.ingest_label("UPI")
        self.ingestor.ingest_label("UPI")
        self.ingestor.ingest_label("Credit")
        self.gmail.get_email.assert_called_once_with("fixture-new")
        self.assertEqual(len(self.database.get_emails()), 2)

    def test_empty_label_does_not_fetch_or_store_messages(self):
        self.gmail.get_messages.return_value = []
        self.ingestor.ingest_label("UPI")
        self.gmail.get_email.assert_not_called()
        self.assertEqual(self.database.get_emails(), [])

    def test_retry_after_fetch_failure_preserves_progress_without_duplicates(self):
        self.gmail.get_messages.return_value = [
            {"id": "fixture-one"}, {"id": "fixture-two"},
        ]
        self.gmail.get_email.side_effect = [
            email("fixture-one"), RuntimeError("Synthetic fetch failure"),
            email("fixture-two"),
        ]
        with self.assertRaisesRegex(RuntimeError, "Synthetic fetch failure"):
            self.ingestor.ingest_label("UPI")
        self.assertTrue(self.database.email_exists("fixture-one"))
        self.assertFalse(self.database.email_exists("fixture-two"))
        self.ingestor.ingest_label("UPI")
        self.assertEqual(len(self.database.get_emails()), 2)
        self.assertEqual(self.gmail.get_email.call_args_list, [
            call("fixture-one"), call("fixture-two"), call("fixture-two"),
        ])


class IngestionCompletionTests(unittest.TestCase):
    def run_main(self, database, *, fail=False):
        with patch("finvault.__main__.setup_logging"), \
                patch("finvault.__main__.TomlCategoryStore") as store, \
                patch("finvault.__main__.GmailClient"), \
                patch("finvault.__main__.Database", return_value=database), \
                patch("finvault.__main__.EmailIngestor") as ingestor, \
                patch("finvault.__main__.TransactionProcessor") as processor:
            store.return_value.load.return_value = CategorySettings(categories=())
            if fail:
                ingestor.return_value.ingest_label.side_effect = RuntimeError("Synthetic fetch failure")
            main()
            processor.return_value.process.assert_called_once()
            store.return_value.load.assert_called_once()

    def test_empty_completed_run_records_completion_after_processing(self):
        database = Mock()
        self.run_main(database)
        self.assertEqual(database.method_calls, [call.record_ingestion_completed(), call.close()])

    def test_failed_run_keeps_previous_completion_and_closes_database(self):
        database = Mock()
        with self.assertRaisesRegex(RuntimeError, "Synthetic fetch failure"):
            self.run_main(database, fail=True)
        database.record_ingestion_completed.assert_not_called()
        database.close.assert_called_once()

    def test_invalid_category_config_stops_before_gmail_or_database_access(self):
        with patch("finvault.__main__.setup_logging"), \
                patch("finvault.__main__.TomlCategoryStore") as store, \
                patch("finvault.__main__.GmailClient") as gmail, \
                patch("finvault.__main__.Database") as database:
            store.return_value.load.side_effect = CategoryConfigError("Invalid category settings; check categories.toml")
            with self.assertLogs("finvault.__main__", level="ERROR"), self.assertRaises(SystemExit) as caught:
                main()
            self.assertEqual(caught.exception.code, 1)
            gmail.assert_not_called()
            database.assert_not_called()


class AutomaticCategoryIngestionTests(unittest.TestCase):
    def test_saved_rules_apply_next_run_without_reprocessing_or_overwriting_old_transactions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.db"
            store = TomlCategoryStore(Path(directory) / "categories.toml")
            categories = (Category(id="custom", label="Example category"), Category(id="another", label="Another category"))
            store.save(CategorySettings(categories=categories, rules=(
                CategoryRule(category="custom", keywords=("example merchant",), transaction_type="debit"),
            )))
            fake = Mock()
            ids = ["fixture-first"]
            fake.get_messages.side_effect = lambda label: [{"id": message_id} for message_id in ids] if label == "Debit card" else []
            fake.get_email.side_effect = lambda message_id: email(message_id, (
                "Thank you for using your HDFC Bank Debit Card ending 0000 for Rs 42.50 at Example Merchant"
            ))
            tracked_store = Mock(wraps=store)
            with patch("finvault.__main__.setup_logging"), \
                    patch("finvault.__main__.GmailClient", return_value=fake), \
                    patch("finvault.__main__.Database", side_effect=lambda: Database(str(path))), \
                    patch("finvault.__main__.TomlCategoryStore", return_value=tracked_store):
                main()
                with sqlite3.connect(path) as connection:
                    self.assertEqual(connection.execute("SELECT category FROM transactions").fetchall(), [("custom",)])
                # Simulate a saved Settings edit between scheduled runs.
                store.save(CategorySettings(categories=categories, rules=(
                    CategoryRule(category="another", keywords=("example merchant",), transaction_type="debit"),
                )))
                ids.append("fixture-second")
                main()
            self.assertEqual(tracked_store.load.call_count, 2)
            self.assertEqual(fake.get_email.call_args_list, [call("fixture-first"), call("fixture-second")])
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute("SELECT email_id, category FROM transactions ORDER BY id").fetchall(),
                                 [("fixture-first", "custom"), ("fixture-second", "another")])
                self.assertIsNotNone(connection.execute("SELECT value FROM app_metadata WHERE key='last_ingestion_at'").fetchone())
