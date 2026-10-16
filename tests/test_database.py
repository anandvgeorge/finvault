"""Persistence checks using temporary SQLite files, never the real database."""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from finvault.database.database import Database
from tests.fixtures import email, transaction


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "nested" / "fixture.db"
        self.database = Database(str(self.path))
        self.addCleanup(self.database.close)

    def test_email_persists_after_reopening_with_unicode_and_sql_characters(self):
        record = email(body="Synthetic café payment: '); DROP TABLE emails; --")
        record.body_html = "<p>Synthetic café payment</p>"
        self.database.save_email(record, "UPI")
        reopened = Database(str(self.path))
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get_emails(), [(record, "UPI")])
        self.assertTrue(reopened.email_exists(record.gmail_id))
        self.assertFalse(reopened.email_exists("missing-fixture"))

    def test_duplicate_email_does_not_replace_original_or_create_another_row(self):
        original = email(body="Original synthetic body")
        self.database.save_email(original, "Credit")
        self.database.save_email(email(body="Different synthetic body"), "UPI")
        self.assertEqual(self.database.get_emails(), [(original, "Credit")])

    def test_transactions_preserve_decimal_precision_and_survive_reopening(self):
        self.database.save_email(email(), "UPI")
        record = transaction(amount=Decimal("123456789.12"))
        self.database.save_transaction(record)
        reopened = Database(str(self.path))
        self.addCleanup(reopened.close)
        row = reopened.connection.execute(
            "SELECT amount, transaction_date, reference FROM transactions"
        ).fetchone()
        self.assertEqual(row, (
            "123456789.12", record.transaction_date.isoformat(), "FIXTURE-REFERENCE",
        ))

    def test_transactions_deduplicate_by_email_without_losing_distinct_emails(self):
        for message_id in ("fixture-one", "fixture-two"):
            self.database.save_email(email(message_id), "UPI")
            record = transaction(message_id)
            self.database.save_transaction(record)
            self.database.save_transaction(record)
        self.assertEqual(self.database.connection.execute(
            "SELECT email_id FROM transactions ORDER BY email_id"
        ).fetchall(), [("fixture-one",), ("fixture-two",)])

    def test_unprocessed_selection_excludes_saved_transactions_and_keeps_order(self):
        stored = email("fixture-stored")
        pending = email("fixture-pending")
        later = email("fixture-later")
        self.database.save_email(stored, "Credit")
        self.database.save_email(pending, "UPI")
        self.database.save_email(later, "Debit card")
        self.database.save_transaction(transaction(stored.gmail_id))
        self.assertEqual(self.database.get_emails(unprocessed_only=True), [
            (pending, "UPI"), (later, "Debit card"),
        ])
        self.assertEqual(self.database.get_emails(), [
            (stored, "Credit"), (pending, "UPI"), (later, "Debit card"),
        ])

    def test_unprocessed_selection_survives_reopening_database(self):
        self.database.save_email(email("fixture-stored"), "Credit")
        pending = email("fixture-pending")
        self.database.save_email(pending, "UPI")
        self.database.save_transaction(transaction("fixture-stored"))
        reopened = Database(str(self.path))
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get_emails(unprocessed_only=True), [(pending, "UPI")])

    def test_deleting_transaction_makes_its_original_email_pending_again(self):
        record = email()
        self.database.save_email(record, "UPI")
        self.database.save_transaction(transaction())
        self.assertEqual(self.database.get_emails(unprocessed_only=True), [])
        self.database.connection.execute(
            "DELETE FROM transactions WHERE email_id = ?", (record.gmail_id,),
        )
        self.database.connection.commit()
        self.assertEqual(self.database.get_emails(unprocessed_only=True), [(record, "UPI")])
