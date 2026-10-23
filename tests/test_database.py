"""Persistence checks using temporary SQLite files, never the real database."""

import tempfile
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from finvault.database.database import Database
from finvault.models.transaction import Transaction
from tests.fixtures import email, transaction


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "nested" / "fixture.db"
        self.database = Database(str(self.path))
        self.addCleanup(self.database.close)

    def test_ingestion_completion_is_unknown_until_recorded_and_survives_reopening(self):
        self.assertIsNone(self.database.runtime_status()["last_ingestion_at"])
        before = datetime.now(timezone.utc)
        self.database.record_ingestion_completed()
        first = self.database.runtime_status()["last_ingestion_at"]
        self.database.record_ingestion_completed()
        reopened = Database(str(self.path))
        self.addCleanup(reopened.close)
        timestamp = datetime.fromisoformat(reopened.runtime_status()["last_ingestion_at"])
        self.assertGreaterEqual(timestamp, datetime.fromisoformat(first))
        self.assertGreaterEqual(timestamp, before)
        self.assertLessEqual(timestamp, datetime.now(timezone.utc))
        self.assertEqual(reopened.get_emails(), [])

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

    def test_initial_category_persists_and_duplicate_insert_preserves_manual_override(self):
        self.database.save_email(email(), "UPI")
        record = transaction(category="custom_initial")
        self.database.save_transaction(record)
        reopened = Database(str(self.path))
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.connection.execute("SELECT category FROM transactions").fetchall(), [("custom_initial",)])
        reopened.update_transaction_category(1, "custom_manual")
        self.database.save_transaction(replace(record, category="custom_new_match"))
        self.assertEqual(reopened.connection.execute("SELECT category FROM transactions").fetchall(), [("custom_manual",)])

    def test_transactions_deduplicate_by_email_without_losing_distinct_emails(self):
        for message_id in ("fixture-one", "fixture-two"):
            self.database.save_email(email(message_id), "UPI")
            record = transaction(message_id)
            self.database.save_transaction(record)
            self.database.save_transaction(record)
        self.assertEqual(self.database.connection.execute(
            "SELECT email_id FROM transactions ORDER BY email_id"
        ).fetchall(), [("fixture-one",), ("fixture-two",)])

    def test_transactions_without_email_are_independent_and_survive_reopening(self):
        fields = vars(transaction()).copy()
        del fields["email_id"]
        first = Transaction(**fields)
        self.assertIsNone(first.email_id)
        self.database.save_transaction(first)
        self.database.save_transaction(replace(first, amount=Decimal("10.25"), category="custom"))
        self.database.save_email(email("fixture-pending"), "UPI")
        reopened = Database(str(self.path))
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.connection.execute(
            "SELECT amount, email_id, category FROM transactions ORDER BY id"
        ).fetchall(), [("42.50", None, None), ("10.25", None, "custom")])
        self.assertEqual([record.gmail_id for record, _ in reopened.get_emails(unprocessed_only=True)], ["fixture-pending"])

    def test_manual_creation_returns_distinct_ids_and_leaves_emails_and_metadata_unchanged(self):
        self.database.save_email(email(), "UPI")
        self.database.save_transaction(transaction())
        self.database.record_ingestion_completed()
        before_emails = self.database.connection.execute("SELECT * FROM emails").fetchall()
        before_status = self.database.runtime_status()
        manual = transaction(email_id=None, source="Cash", account="", reference=None)
        first = self.database.create_manual_transaction(manual)
        second = self.database.create_manual_transaction(manual)
        self.assertNotEqual(first, second)
        self.assertEqual(self.database.connection.execute(
            "SELECT email_id, source, account FROM transactions WHERE id IN (?, ?) ORDER BY id", (first, second),
        ).fetchall(), [(None, "Cash", ""), (None, "Cash", "")])
        self.assertEqual(self.database.connection.execute("SELECT * FROM emails").fetchall(), before_emails)
        self.assertEqual(self.database.runtime_status(), before_status)
        self.assertEqual(self.database.get_emails(unprocessed_only=True), [])
        with self.assertRaises(ValueError):
            self.database.create_manual_transaction(transaction())

    def test_failed_manual_insert_rolls_back_without_creating_a_partial_record(self):
        self.database.connection.execute("""
            CREATE TRIGGER reject_manual BEFORE INSERT ON transactions
            WHEN NEW.email_id IS NULL BEGIN SELECT RAISE(ABORT, 'Synthetic insert failure'); END
        """)
        self.database.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.create_manual_transaction(transaction(email_id=None))
        self.assertEqual(self.database.connection.execute("SELECT COUNT(*) FROM transactions").fetchone(), (0,))
        self.assertEqual(self.database.get_emails(), [])

    def test_concurrent_manual_saves_return_each_transactions_own_id(self):
        def create(number):
            merchant = f"Example purchase {number}"
            identifier = self.database.create_manual_transaction(transaction(email_id=None, merchant=merchant))
            return identifier, merchant
        with ThreadPoolExecutor(max_workers=4) as executor:
            saved = list(executor.map(create, range(12)))
        self.assertEqual(len({identifier for identifier, _ in saved}), 12)
        self.assertEqual(dict(self.database.connection.execute("SELECT id, merchant FROM transactions")), dict(saved))

    def test_category_updates_and_clearing_survive_reopening_the_database(self):
        self.database.save_email(email(), "UPI")
        self.database.save_transaction(transaction())
        self.assertTrue(self.database.update_transaction_category(1, "investment"))
        reopened = Database(str(self.path))
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.connection.execute(
            "SELECT category FROM transactions WHERE id = 1"
        ).fetchone(), ("investment",))
        self.assertFalse(self.database.update_transaction_category(999, "other"))
        self.assertEqual(reopened.connection.execute(
            "SELECT category FROM transactions"
        ).fetchall(), [("investment",)])
        self.assertTrue(self.database.update_transaction_category(1, None))
        self.assertEqual(reopened.connection.execute(
            "SELECT category FROM transactions WHERE id = 1"
        ).fetchone(), (None,))

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
