"""API checks with synthetic, in-memory data and no network or real database."""

import asyncio
import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import HTTPException

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

    def save_calendar_transactions(self):
        for message_id, date, amount, kind, source in (
            ("fixture-2000-dec", "2000-12-31T23:59:59", "5", "debit", "Credit card"),
            ("fixture-2001-jan", "2001-01-01T00:00:00", "10", "debit", "UPI"),
            ("fixture-2001-feb", "2001-02-28T12:00:00", "20", "credit", "Credit"),
            ("fixture-2001-mar-debit", "2001-03-01T00:00:00", "30", "debit", "Credit card"),
            ("fixture-2001-mar-credit", "2001-03-31T23:59:59", "40", "credit", "Credit"),
            ("fixture-2001-apr", "2001-04-01T00:00:00", "50", "credit", "Credit"),
            ("fixture-2002-jan", "2002-01-01T00:00:00", "60", "debit", "UPI"),
            ("fixture-2002-dec", "2002-12-31T23:59:59", "70", "credit", "Credit"),
            ("fixture-2003-jan", "2003-01-01T00:00:00", "80", "credit", "Credit"),
        ):
            self.save_transaction(message_id, transaction_date=datetime.fromisoformat(date),
                                  amount=Decimal(amount), transaction_type=kind, source=source)

    def request(self, path, parameters=()):
        """Exercise ASGI query validation and responses without HTTP dependencies.

        Execute sync handlers inline; worker-thread scheduling is outside these
        tests. All request handling and SQL still use the real application.
        """
        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def run():
            messages = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "http", "path": path,
                "raw_path": path.encode(), "root_path": "", "headers": [],
                "query_string": urlencode(parameters).encode(),
                "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
            }
            with patch("fastapi.routing.run_in_threadpool", new=inline):
                await asyncio.wait_for(self.api.app(scope, receive, send), timeout=3)
            status = next(message["status"] for message in messages
                          if message["type"] == "http.response.start")
            body = b"".join(message.get("body", b"") for message in messages
                            if message["type"] == "http.response.body")
            return status, json.loads(body)

        return asyncio.run(run())

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
        self.assertEqual(self.api.get_years(), [])

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

    def test_available_years_are_distinct_newest_first_and_ignore_unprocessed_emails(self):
        self.save_calendar_transactions()
        self.database.save_email(email("fixture-email-only"), "Credit")
        self.assertEqual(self.api.get_years(), [2003, 2002, 2001, 2000])
        self.assertEqual(self.request("/years"), (200, [2003, 2002, 2001, 2000]))

    def test_no_selection_and_explicit_empty_selection_return_all_time(self):
        self.save_calendar_transactions()
        for selection in ({}, {"years": [], "months": []}, {"period": "all"}):
            with self.subTest(selection=selection):
                self.assertEqual(len(self.api.get_transactions(**selection)), 9)
                self.assertEqual(self.api.get_overview(**selection), {
                    "total_debits": 105, "total_credits": 260, "net_cashflow": 155,
                    "upi_debits": 70, "credit_card_debits": 35,
                })

    def test_year_and_month_selections_filter_rows_and_every_card_consistently(self):
        self.save_calendar_transactions()
        cases = (
            ({"years": [2001]}, [6, 5, 4, 3, 2], (40, 110, 70, 10, 30)),
            ({"years": [2001], "months": ["2001-01", "2001-03"]},
             [5, 4, 2], (40, 40, 0, 10, 30)),
            ({"years": [2000, 2002], "months": ["2002-01"]},
             [7, 1], (65, 0, -65, 60, 5)),
            ({"years": [2001, 2002], "months": ["2001-01", "2002-12"]},
             [8, 2], (10, 70, 60, 10, 0)),
            ({"years": [2001, 2002]}, [8, 7, 6, 5, 4, 3, 2], (100, 180, 80, 70, 30)),
            ({"years": [2001, 2001], "months": ["2001-01", "2001-01"]},
             [2], (10, 0, -10, 10, 0)),
            ({"years": [1999]}, [], (0, 0, 0, 0, 0)),
            ({"years": [2001], "period": "month"},
             [6, 5, 4, 3, 2], (40, 110, 70, 10, 30)),
        )
        keys = ("total_debits", "total_credits", "net_cashflow", "upi_debits", "credit_card_debits")
        for selection, ids, totals in cases:
            with self.subTest(selection=selection):
                rows = self.api.get_transactions(**selection)
                self.assertEqual([row["id"] for row in rows], ids)
                overview = self.api.get_overview(**selection)
                self.assertEqual(overview, dict(zip(keys, totals)))
                # Verify the summary independently against the selected rows.
                net = sum(Decimal(row["amount"]) * (1 if row["transaction_type"] == "credit" else -1)
                          for row in rows)
                self.assertEqual(Decimal(str(overview["net_cashflow"])), net)

    def test_removing_last_month_returns_full_year_and_clearing_years_returns_all(self):
        self.save_calendar_transactions()
        self.assertEqual(len(self.api.get_transactions(years=[2001], months=["2001-01"])), 1)
        self.assertEqual(len(self.api.get_transactions(years=[2001], months=[])), 5)
        self.assertEqual(len(self.api.get_transactions(years=[], months=[])), 9)

    def test_leap_day_and_timestamp_offsets_use_the_displayed_calendar_date(self):
        self.save_transaction("fixture-leap", transaction_date=datetime(2000, 2, 29, 23, 59, 59))
        self.save_transaction("fixture-after-leap", transaction_date=datetime(2000, 3, 1))
        self.save_transaction("fixture-offset", transaction_date=datetime(
            2001, 1, 1, 0, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        self.assertEqual(self.api.get_years(), [2001, 2000])
        rows = self.api.get_transactions(years=[2000], months=["2000-02"])
        self.assertEqual([row["id"] for row in rows], [1])
        rows = self.api.get_transactions(years=[2001], months=["2001-01"])
        self.assertEqual([row["id"] for row in rows], [3])

    def test_legacy_periods_exclude_dates_beyond_the_current_month_or_year(self):
        today, next_month, next_year = self.database.connection.execute("""
            SELECT date('now'), date('now', 'start of month', '+1 month'),
                   date('now', 'start of year', '+1 year')
        """).fetchone()
        for message_id, date, amount in (
            ("fixture-today", today, "10"), ("fixture-next-month", next_month, "20"),
            ("fixture-next-year", next_year, "30"),
        ):
            self.save_transaction(message_id, transaction_date=datetime.fromisoformat(date),
                                  amount=Decimal(amount), transaction_type="credit")
        self.assertEqual(self.api.get_overview(period="month")["total_credits"], 10)
        self.assertEqual([row["id"] for row in self.api.get_transactions(period="month")], [1])
        expected = 30 if next_month[:4] == today[:4] else 10
        self.assertEqual(self.api.get_overview(period="year")["total_credits"], expected)
        self.assertNotIn(3, [row["id"] for row in self.api.get_transactions(period="year")])

    def test_invalid_calendar_selections_are_rejected_by_both_handlers(self):
        selections = (
            {"years": [0]}, {"years": [10000]}, {"months": ["2001-01"]},
            {"years": [2001], "months": ["2002-01"]}, {"period": "week"},
        ) + tuple({"years": [2001], "months": [month]} for month in (
            "2001-00", "2001-13", "2001-1", "2001-01-01", "", "not-a-month",
            "2001-01' OR 1=1 --",
        ))
        for selection in selections:
            for handler in (self.api.get_overview, self.api.get_transactions):
                with self.subTest(selection=selection, handler=handler.__name__):
                    with self.assertRaises(HTTPException) as raised:
                        handler(**selection)
                    self.assertEqual(raised.exception.status_code, 422)

    def test_requests_parse_repeated_year_and_month_query_parameters(self):
        self.save_calendar_transactions()
        parameters = [("years", 2000), ("years", 2002), ("months", "2002-01")]
        status, overview = self.request("/overview", parameters)
        self.assertEqual(status, 200)
        self.assertEqual(overview["total_debits"], 65)
        status, rows = self.request("/transactions", parameters)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in rows], [7, 1])
        self.assertEqual(len(self.request("/transactions")[1]), 9)
        self.assertEqual(self.request("/overview")[1]["total_credits"], 260)

    def test_invalid_query_parameters_return_422_responses(self):
        for parameters in (
            [("years", "not-a-year")], [("years", 0)], [("years", 10000)],
            [("months", "2001-01")], [("years", 2001), ("months", "2002-01")],
            [("years", 2001), ("months", "2001-13")], [("period", "week")],
        ):
            for path in ("/overview", "/transactions"):
                with self.subTest(path=path, parameters=parameters):
                    status, body = self.request(path, parameters)
                    self.assertEqual(status, 422)
                    self.assertIn("detail", body)
