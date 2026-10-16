"""API handler unit tests using an in-memory database and no HTTP client."""

import importlib.util
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import finvault.api
from finvault.database.database import Database
from tests.fixtures import email, transaction


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.database = Database(":memory:")
        self.addCleanup(self.database.close)
        # The module opens a database at import time. Load an isolated instance
        # while substituting our in-memory DB, so real data is never opened.
        path = Path(finvault.api.__file__).with_name("main.py")
        spec = importlib.util.spec_from_file_location("finvault_api_fixture", path)
        module = importlib.util.module_from_spec(spec)
        with patch("finvault.database.database.Database", return_value=self.database):
            spec.loader.exec_module(module)
        self.api = module

    def save_transaction(self, message_id, **values):
        self.database.save_email(email(message_id), "UPI")
        self.database.save_transaction(transaction(message_id, **values))

    def test_dashboard_points_to_existing_html(self):
        response = self.api.dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "text/html")
        self.assertIn("FinVault", Path(response.path).read_text())

    def test_empty_database_returns_zero_summary_and_empty_transactions(self):
        self.assertEqual(self.api.get_overview(period="month"), {
            "total_debits": 0, "total_credits": 0, "net_cashflow": 0,
            "upi_debits": 0, "credit_card_debits": 0,
        })
        self.assertEqual(self.api.get_transactions(), [])

    def test_overview_totals_and_period_filters(self):
        # Match SQLite's current UTC date without using any real transaction data.
        today = datetime.fromisoformat(self.database.connection.execute("SELECT date('now')").fetchone()[0])
        for message_id, amount, kind, source in (
            ("fixture-upi", "10.25", "debit", "UPI"),
            ("fixture-card", "2.50", "debit", "Credit card"),
            ("fixture-credit", "20.00", "credit", "Credit"),
        ):
            self.save_transaction(message_id, amount=Decimal(amount), transaction_type=kind,
                                  source=source, transaction_date=today)
        self.save_transaction(
            "fixture-old", amount=Decimal("5.00"), transaction_type="credit",
            transaction_date=today.replace(year=today.year - 2, month=1, day=1),
        )
        for period in ("month", "year", "all"):
            with self.subTest(period=period):
                self.assertEqual(self.api.get_overview(period=period), {
                    "total_debits": 12.75,
                    "total_credits": 25.0 if period == "all" else 20.0,
                    "net_cashflow": 12.25 if period == "all" else 7.25,
                    "upi_debits": 10.25, "credit_card_debits": 2.5,
                })

    def test_transactions_are_newest_first_and_preserve_fields(self):
        older = datetime(2001, 1, 1)
        newer = older + timedelta(days=1)
        self.save_transaction("fixture-old", transaction_date=older)
        self.save_transaction("fixture-new", transaction_date=newer)
        rows = self.api.get_transactions()
        self.assertEqual([row["transaction_date"] for row in rows], [newer.isoformat(), older.isoformat()])
        self.assertEqual(rows[0], {
            "id": 2, "transaction_date": newer.isoformat(), "amount": "42.50",
            "currency": "INR", "transaction_type": "debit", "merchant": "Example Merchant",
            "account": "HDFC XX0000", "source": "UPI", "reference": "FIXTURE-REFERENCE",
            "category": None,
        })
