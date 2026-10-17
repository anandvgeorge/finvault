"""Ingestion checks with fake Gmail responses and an in-memory database."""

import unittest
from unittest.mock import Mock, call, patch

from finvault.database.database import Database
from finvault.ingestion.email_ingestor import EmailIngestor
from tests.fixtures import email
from finvault.__main__ import main


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
                patch("finvault.__main__.GmailClient"), \
                patch("finvault.__main__.Database", return_value=database), \
                patch("finvault.__main__.EmailIngestor") as ingestor, \
                patch("finvault.__main__.TransactionProcessor") as processor:
            if fail:
                ingestor.return_value.ingest_label.side_effect = RuntimeError("Synthetic fetch failure")
            main()
            processor.return_value.process.assert_called_once()

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
