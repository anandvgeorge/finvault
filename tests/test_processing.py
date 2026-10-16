"""Incremental processing with synthetic emails and an in-memory database."""

import unittest
from unittest.mock import Mock, call

from finvault.database.database import Database
from finvault.parsers.hdfc import HDFCParser
from finvault.processing.transaction_processor import TransactionProcessor
from tests.fixtures import email, transaction


class TransactionProcessorTests(unittest.TestCase):
    def setUp(self):
        self.database = Database(":memory:")
        self.addCleanup(self.database.close)
        self.processor = TransactionProcessor(self.database)
        self.processor.parser = Mock(wraps=HDFCParser())

    def purchase(self, message_id):
        return email(message_id, (
            "Thank you for using your HDFC Bank Debit Card ending 0000 "
            "for Rs 42.50 at Example Merchant"
        ))

    def test_existing_transactions_are_never_reparsed_and_new_emails_are_processed(self):
        stored = self.purchase("fixture-stored")
        pending = self.purchase("fixture-pending")
        self.database.save_email(stored, "Credit")
        self.database.save_transaction(transaction(stored.gmail_id))
        original_row = self.database.connection.execute(
            "SELECT * FROM transactions WHERE email_id = ?", (stored.gmail_id,),
        ).fetchone()
        self.database.save_email(pending, "Credit")

        self.processor.process()
        self.processor.process()
        self.processor.parser.parse.assert_called_once_with(pending, "Credit")
        self.assertEqual(self.database.connection.execute(
            "SELECT * FROM transactions WHERE email_id = ?", (stored.gmail_id,),
        ).fetchone(), original_row)

        later = self.purchase("fixture-later")
        self.database.save_email(later, "Credit")
        self.processor.process()
        self.assertEqual(self.processor.parser.parse.call_args_list, [
            call(pending, "Credit"), call(later, "Credit"),
        ])
        self.assertEqual(self.database.connection.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0], 3)

    def test_failed_parse_is_retried_until_a_transaction_is_saved(self):
        pending = email("fixture-pending", "Synthetic future bank format")
        self.database.save_email(pending, "Credit")
        parser = Mock()
        parser.parse.side_effect = [None, transaction(pending.gmail_id)]
        parser.get_skip_reason.return_value = None
        self.processor.parser = parser

        with self.assertLogs("finvault.processing.transaction_processor", level="WARNING"):
            self.processor.process()
        self.assertEqual(len(self.database.get_emails(unprocessed_only=True)), 1)
        self.processor.process()
        self.processor.process()
        self.assertEqual(parser.parse.call_count, 2)
        self.assertEqual(self.database.get_emails(unprocessed_only=True), [])

    def test_empty_pending_queue_does_not_invoke_parser(self):
        self.database.save_email(self.purchase("fixture-stored"), "Credit")
        self.database.save_transaction(transaction("fixture-stored"))
        with self.assertLogs("finvault.processing.transaction_processor", level="INFO") as logs:
            self.processor.process()
        self.processor.parser.parse.assert_not_called()
        self.assertIn("0 parsed, 0 skipped, 0 failed", logs.output[-1])
