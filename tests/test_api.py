"""API checks with synthetic, temporary data and no network or real database."""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import HTTPException

import finvault.api
from finvault.categories import TomlCategoryStore
from finvault.database.database import Database
from finvault.models.category import Category, CategoryGroup, CategoryRule, CategorySettings
from tests.fixtures import email, transaction


class ApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(str(Path(directory.name) / "test.db"))
        self.addCleanup(self.database.close)
        self.category_store = TomlCategoryStore(Path(directory.name) / "categories.toml")
        example = Path(__file__).resolve().parents[1] / "categories.example.toml"
        self.category_store.save(TomlCategoryStore(example).load())
        # The module opens a database at import time. Load an isolated instance
        # while substituting our temporary DB, so real data is never opened.
        path = Path(finvault.api.__file__).with_name("main.py")
        spec = importlib.util.spec_from_file_location("finvault_api_fixture", path)
        module = importlib.util.module_from_spec(spec)
        with patch("finvault.database.database.Database", return_value=self.database), \
                patch("finvault.categories.TomlCategoryStore", return_value=self.category_store):
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

    def request(self, path, parameters=(), *, method="GET", body=None, json_response=True):
        """Exercise ASGI query validation and responses without HTTP dependencies.

        Execute sync handlers and file reads inline; worker-thread scheduling is outside these
        tests. All request handling and SQL still use the real application.
        """
        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def inline_thread(function, *args, **thread_options):
            return function(*args)

        async def run():
            messages = []
            if isinstance(body, bytes):
                payload = body
            else:
                payload = json.dumps(body).encode() if body is not None else b""

            async def receive():
                return {"type": "http.request", "body": payload, "more_body": False}

            async def send(message):
                messages.append(message)

            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "scheme": "http", "path": path,
                "raw_path": path.encode(), "root_path": "",
                "headers": [(b"content-type", b"application/json")] if body is not None else [],
                "query_string": urlencode(parameters).encode(),
                "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
            }
            with patch("fastapi.routing.run_in_threadpool", new=inline), \
                    patch("anyio.to_thread.run_sync", new=inline_thread):
                await asyncio.wait_for(self.api.app(scope, receive, send), timeout=3)
            status = next(message["status"] for message in messages
                          if message["type"] == "http.response.start")
            response_body = b"".join(message.get("body", b"") for message in messages
                                     if message["type"] == "http.response.body")
            return status, json.loads(response_body) if json_response else response_body.decode()

        return asyncio.run(run())

    def backfill_fixture(self):
        self.category_store.save(CategorySettings(
            categories=(Category(id="custom", label="Custom"),),
            rules=(CategoryRule(category="custom", keywords=("synthetic",), transaction_type="debit"),),
        ))
        self.save_transaction("fixture-match", transaction_date=datetime(2001, 1, 2))
        self.save_transaction("fixture-next-month", transaction_date=datetime(2001, 2, 2))
        self.save_transaction("fixture-assigned", category="custom")
        self.save_transaction("fixture-no-match", transaction_type="credit")
        self.save_transaction("fixture-empty")
        self.database.connection.execute("UPDATE emails SET body_text = '' WHERE gmail_id = 'fixture-empty'")
        self.database.connection.commit()

    def test_browser_backfill_preview_is_read_only_and_month_is_optional(self):
        self.backfill_fixture()
        before = list(self.database.connection.iterdump())
        status, body = self.request("/settings/categories/backfill/preview", method="POST", body={})
        self.assertEqual(status, 200)
        self.assertEqual(body, {
            "month": None, "considered": 4, "matched": 2, "unmatched": 1,
            "missing_body": 1, "unsupported_type": 0, "updated": None, "skipped": 0,
        })
        status, body = self.request("/settings/categories/backfill/preview", method="POST", body={"month": "2001-01"})
        self.assertEqual(status, 200)
        self.assertEqual((body["considered"], body["matched"]), (3, 1))
        self.assertEqual(list(self.database.connection.iterdump()), before)

    def test_browser_backfill_applies_current_saved_rules_and_preserves_other_fields(self):
        self.backfill_fixture()
        before = self.api.get_transactions()
        # Apply uses the latest saved rules, rather than an earlier preview.
        self.request("/settings/categories/backfill/preview", method="POST", body={})
        self.category_store.save(CategorySettings(
            categories=(Category(id="new", label="New"),),
            rules=(CategoryRule(category="new", keywords=("synthetic",), transaction_type="debit"),),
        ))
        status, body = self.request("/settings/categories/backfill/apply", method="POST", body={"month": "2001-01"})
        self.assertEqual(status, 200)
        self.assertEqual(body["updated"], 1)
        after = self.api.get_transactions()
        self.assertEqual([{k: v for k, v in row.items() if k != "category"} for row in before],
                         [{k: v for k, v in row.items() if k != "category"} for row in after])
        categories = {row["id"]: row["category"] for row in after}
        self.assertEqual(categories, {1: "new", 2: None, 3: "custom", 4: None, 5: None})
        status, body = self.request("/settings/categories/backfill/apply", method="POST", body={})
        self.assertEqual(body["updated"], 1)
        status, body = self.request("/settings/categories/backfill/apply", method="POST", body={})
        self.assertEqual(body["updated"], 0)

    def test_browser_backfill_rejects_bad_input_and_get_cannot_apply(self):
        for body in ({"month": "0000-01"}, {"month": "2001-13"}, {"month": "2001-2"},
                     {"month": "2001-01' OR 1=1 --"}, {"month": ""}, {"database": "other.db"}):
            with self.subTest(body=body):
                self.assertEqual(self.request("/settings/categories/backfill/apply", method="POST", body=body)[0], 422)
        self.assertEqual(self.request("/settings/categories/backfill/unknown", method="POST", body={})[0], 422)
        self.assertEqual(self.request("/settings/categories/backfill/apply")[0], 405)

    def test_browser_backfill_database_failure_rolls_back_and_hides_details(self):
        self.backfill_fixture()
        self.database.connection.execute("""
            CREATE TRIGGER reject_test_update BEFORE UPDATE OF category ON transactions
            WHEN NEW.id = 2 BEGIN SELECT RAISE(ABORT, 'fictional private database detail'); END
        """)
        self.database.connection.commit()
        status, body = self.request("/settings/categories/backfill/apply", method="POST", body={})
        self.assertEqual(status, 503)
        self.assertEqual(body, {"detail": "Could not read or update categories. Try again."})
        self.assertIsNone(self.database.connection.execute("SELECT category FROM transactions WHERE id = 1").fetchone()[0])

    def test_browser_backfill_uses_sanitized_config_errors_and_refuses_missing_database(self):
        self.category_store.path.write_text("not valid TOML")
        status, body = self.request("/settings/categories/backfill/preview", method="POST", body={})
        self.assertEqual(status, 503)
        self.assertNotIn("not valid TOML", body["detail"])
        self.category_store.save(CategorySettings(categories=()))
        missing = self.database.db_path.with_name("missing.db")
        with patch.object(self.database, "db_path", missing):
            for action in ("preview", "apply"):
                self.assertEqual(self.request(f"/settings/categories/backfill/{action}", method="POST", body={})[0], 503)
        self.assertFalse(missing.exists())

    def test_dashboard_points_to_existing_html(self):
        response = self.api.dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "text/html")
        self.assertIn("FinVault", Path(response.path).read_text())

    def test_settings_page_points_to_existing_html(self):
        response = self.api.settings_page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "text/html")
        self.assertIn('id="settings-form"', Path(response.path).read_text())

    def test_analysis_page_and_navigation_are_served(self):
        response = self.api.analysis_page()
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/#analysis-heading")
        status, html = self.request("/", json_response=False)
        self.assertEqual(status, 200)
        self.assertIn('id="analysis-results"', html)
        self.assertIn('id="transactions-table"', html)
        self.assertEqual(html.count('class="period-selector"'), 1)
        self.assertIn('href="#analysis-heading"', html)

    def test_system_status_reports_only_metadata_and_the_running_commit(self):
        with patch.object(self.api.runtime_info, "commit_id", "a" * 40):
            status, body = self.request("/settings/status")
            self.assertEqual(status, 200)
            self.assertEqual(body, {"last_ingestion_at": None, "last_deploy_check_at": None,
                                    "last_deploy_check_status": None, "commit_id": "a" * 40})
            self.database.record_ingestion_completed()
            self.database.connection.executemany("INSERT INTO app_metadata VALUES (?, ?)", [
                ("last_deploy_check_at", "2001-01-01T00:00:00+00:00"),
                ("last_deploy_check_status", "unchanged"), ("unrelated_private_value", "not exposed"),
            ])
            self.database.connection.commit()
            status, body = self.request("/settings/status")
            self.assertEqual(body, {**self.database.runtime_status(), "commit_id": "a" * 40})

    def test_shared_ui_assets_are_served(self):
        for name, expected in (("ui.css", ".page-header"), ("ui.js", "class CategoryMenu"),
                               ("category-drag.js", "class CategoryDrag"),
                               ("category-picker.js", "class GroupedCategoryMenu"),
                               ("summary-controls.js", "class PeriodSelection"),
                               ("summary-controls.css", ".period-selector"),
                               ("analysis.js", "class AnalysisView"), ("analysis.css", ".analysis-card")):
            with self.subTest(name=name):
                status, body = self.request(f"/static/{name}", json_response=False)
                self.assertEqual(status, 200)
                self.assertIn(expected, body)

    def test_public_category_catalog_exposes_groups_and_assignments_without_rules(self):
        settings = self.category_store.load().model_dump(mode="json")
        settings["rules"] = [{"category": "salary", "transaction_type": "credit", "keywords": ["fictional private keyword"]}]
        self.category_store.save(CategorySettings.model_validate(settings))
        expected = {key: settings[key] for key in ("categories", "groups", "category_groups")}
        self.assertEqual(self.request("/categories/catalog"), (200, expected))
        self.assertNotIn("fictional private keyword", json.dumps(expected))
        self.assertEqual(self.request("/categories"), (200, settings["categories"]))
        settings["category_groups"] = {}
        self.category_store.save(CategorySettings.model_validate(settings))
        self.assertEqual(self.request("/categories/catalog")[1]["category_groups"], {})
        self.category_store.path.write_text('invalid = "fictional private keyword"\n')
        status, body = self.request("/categories/catalog")
        self.assertEqual(status, 503)
        self.assertNotIn("fictional private keyword", json.dumps(body))

    def test_empty_database_returns_zero_summary_and_empty_transactions(self):
        self.assertEqual(self.api.get_overview(period="month"), {
            "total_debits": 0, "total_credits": 0, "net_cashflow": 0,
            "upi_debits": 0, "credit_card_debits": 0,
        })
        self.assertEqual(self.api.get_transactions(), [])
        self.assertEqual(self.api.get_years(), [])

    def test_analysis_accounts_for_groups_independent_removed_and_uncategorized_without_writes(self):
        self.category_store.save(CategorySettings(
            categories=tuple(Category(id=key, label=label) for key, label in (
                ("housing", "Housing"), ("goods", "Goods"), ("pay", "Pay"),
                ("lend", "Lend"), ("refunds", "Refunds"), ("unused", "Unused"),
            )),
            groups=(CategoryGroup(id="needs", label="Needs"), CategoryGroup(id="earnings", label="Earnings"),
                    CategoryGroup(id="empty", label="Empty group")),
            category_groups={"housing": "needs", "goods": "needs", "pay": "earnings"},
            rules=(CategoryRule(category="pay", keywords=("fictional private keyword",)),),
        ))
        for index, (category, kind, amount) in enumerate((
            ("housing", "debit", "0.10"), ("goods", "debit", "0.20"), ("goods", "credit", "0.07"),
            ("pay", "credit", "1200.05"), ("pay", "debit", "0.05"), ("lend", "debit", "100.00"),
            ("refunds", "credit", "10.00"), ("removed", "debit", "30.00"), (None, "debit", "5.00"),
        )):
            self.save_transaction(f"fixture-analysis-{index}", category=category,
                                  transaction_type=kind, amount=Decimal(amount))
        before = list(self.database.connection.iterdump())
        config_before = self.category_store.path.read_bytes()
        status, body = self.request("/analysis/summary")
        self.assertEqual(status, 200)
        self.assertEqual(body, {
            "income_group": None, "income_total": None,
            "totals": {"total_debits": "135.35", "total_credits": "1210.12", "transaction_count": 9},
            "groups": [
                {"id": "needs", "label": "Needs", "total_debits": "0.30", "total_credits": "0.07", "transaction_count": 3, "income_percentage": None,
                 "categories": [
                     {"id": "goods", "label": "Goods", "total_debits": "0.20", "total_credits": "0.07", "transaction_count": 2, "income_percentage": None},
                     {"id": "housing", "label": "Housing", "total_debits": "0.10", "total_credits": "0.00", "transaction_count": 1, "income_percentage": None},
                 ]},
                {"id": "earnings", "label": "Earnings", "total_debits": "0.05", "total_credits": "1200.05", "transaction_count": 2, "income_percentage": None,
                 "categories": [{"id": "pay", "label": "Pay", "total_debits": "0.05", "total_credits": "1200.05", "transaction_count": 2, "income_percentage": None}]},
                {"id": "empty", "label": "Empty group", "total_debits": "0.00", "total_credits": "0.00", "transaction_count": 0, "income_percentage": None, "categories": []},
            ],
            "independent": [
                {"id": "lend", "label": "Lend", "total_debits": "100.00", "total_credits": "0.00", "transaction_count": 1},
                {"id": "refunds", "label": "Refunds", "total_debits": "0.00", "total_credits": "10.00", "transaction_count": 1},
                {"id": "unused", "label": "Unused", "total_debits": "0.00", "total_credits": "0.00", "transaction_count": 0},
                {"id": "removed", "label": "removed (unavailable)", "total_debits": "30.00", "total_credits": "0.00", "transaction_count": 1},
            ],
            "uncategorized": {"total_debits": "5.00", "total_credits": "0.00", "transaction_count": 1},
        })
        self.assertEqual(list(self.database.connection.iterdump()), before)
        self.assertEqual(self.category_store.path.read_bytes(), config_before)

    def test_analysis_uses_current_group_assignments_and_legacy_config_without_rewriting_transactions(self):
        settings = CategorySettings(
            categories=(Category(id="custom", label="Custom"),),
            groups=(CategoryGroup(id="first", label="First"), CategoryGroup(id="second", label="Second")),
            category_groups={"custom": "first"},
        )
        self.category_store.save(settings)
        self.save_transaction("fixture-group-move", category="custom")
        before = self.api.get_transactions()
        self.assertEqual(self.request("/analysis/summary")[1]["groups"][0]["total_debits"], "42.50")
        updated = settings.model_dump()
        updated["category_groups"] = {"custom": "second"}
        updated["groups"][1]["label"] = "Renamed"
        self.category_store.save(CategorySettings.model_validate(updated))
        body = self.request("/analysis/summary")[1]
        self.assertEqual([group["total_debits"] for group in body["groups"]], ["0.00", "42.50"])
        self.assertEqual(body["groups"][1]["label"], "Renamed")
        self.category_store.path.write_text('[categories]\ncustom = "Custom"\n')
        body = self.request("/analysis/summary")[1]
        self.assertEqual(body["groups"], [])
        self.assertEqual(body["independent"], [
            {"id": "custom", "label": "Custom", "total_debits": "42.50", "total_credits": "0.00", "transaction_count": 1},
        ])
        self.assertEqual(self.api.get_transactions(), before)

    def test_analysis_period_filters_match_transactions_including_ist_month_boundary(self):
        self.save_calendar_transactions()
        self.database.connection.execute("""
            UPDATE transactions SET category = CASE
                WHEN transaction_type = 'debit' THEN 'necessary' ELSE 'salary' END
        """)
        self.database.connection.commit()
        self.save_transaction("fixture-analysis-ist", amount=Decimal("0.01"),
                              transaction_date=datetime.fromisoformat("2001-01-01T00:15:00+05:30"))
        for parameters in (
            [], [("years", 1999)], [("years", 2001)],
            [("years", 2001), ("months", "2001-01"), ("months", "2001-03")],
            [("years", 2000), ("years", 2002), ("months", "2002-01")],
            [("years", 2001), ("years", 2002), ("months", "2001-01"), ("months", "2002-12")],
            [("years", 2001), ("years", 2001), ("months", "2001-01"), ("months", "2001-01")],
            [("period", "month")], [("period", "year")],
        ):
            with self.subTest(parameters=parameters):
                status, summary = self.request("/analysis/summary", parameters)
                self.assertEqual(status, 200)
                rows = self.request("/transactions", parameters)[1]
                self.assertEqual(summary["totals"]["transaction_count"], len(rows))
                buckets = summary["groups"] + summary["independent"] + [summary["uncategorized"]]
                self.assertEqual(sum(bucket["transaction_count"] for bucket in buckets), len(rows))
                for kind in ("debit", "credit"):
                    expected = sum((Decimal(row["amount"]) for row in rows if row["transaction_type"] == kind), Decimal(0))
                    self.assertEqual(Decimal(summary["totals"][f"total_{kind}s"]), expected)
                    self.assertEqual(sum(Decimal(bucket[f"total_{kind}s"]) for bucket in buckets), expected)

    def test_analysis_empty_database_missing_config_and_invalid_config(self):
        zero = {"total_debits": "0.00", "total_credits": "0.00", "transaction_count": 0}
        body = self.request("/analysis/summary")[1]
        self.assertEqual(body["totals"], zero)
        self.assertEqual(body["uncategorized"], zero)
        self.assertEqual([group["id"] for group in body["groups"]],
                         [group.id for group in self.category_store.load().groups])
        for entry in body["groups"] + body["independent"]:
            self.assertEqual({key: entry[key] for key in zero}, zero)
        self.category_store.path.unlink()
        self.assertEqual(self.request("/analysis/summary"), (200, {
            "totals": zero, "groups": [], "independent": [], "uncategorized": zero,
            "income_group": None, "income_total": None,
        }))
        self.category_store.path.write_text('invalid = "fictional private keyword"\n')
        status, body = self.request("/analysis/summary")
        self.assertEqual(status, 503)
        self.assertNotIn("fictional private keyword", json.dumps(body))

    def test_analysis_percentages_use_configured_income_credits_and_the_selected_period(self):
        self.category_store.save(CategorySettings(
            categories=tuple(Category(id=key, label=key.title()) for key in ("shop", "pay", "other_income", "refund")),
            groups=(CategoryGroup(id="living", label="Living"), CategoryGroup(id="earnings", label="Earnings")),
            category_groups={"shop": "living", "pay": "earnings", "other_income": "earnings"}, income_group="earnings",
        ))
        for index, (category, kind, amount, month) in enumerate((
            ("shop", "debit", "1250.00", 1), ("shop", "credit", "20.00", 1),
            ("pay", "credit", "600.00", 1), ("other_income", "credit", "400.00", 1), ("pay", "debit", "7.00", 1),
            ("refund", "credit", "5000.00", 1), ("shop", "debit", "10.00", 2),
        )):
            self.save_transaction(f"fixture-percentage-{index}", category=category, amount=Decimal(amount),
                                  transaction_type=kind, transaction_date=datetime(2001, month, 1))
        january = self.request("/analysis/summary", [("years", 2001), ("months", "2001-01")])[1]
        self.assertEqual(january["income_total"], "1000.00")
        self.assertEqual([group["income_percentage"] for group in january["groups"]], ["125.0", "100.0"])
        self.assertEqual({category["id"]: category["income_percentage"] for category in january["groups"][1]["categories"]},
                         {"other_income": "40.0", "pay": "60.0"})
        self.assertNotIn("income_percentage", january["independent"][0])
        february = self.request("/analysis/summary", [("years", 2001), ("months", "2001-02")])[1]
        self.assertEqual(february["income_total"], "0.00")
        self.assertTrue(all(group["income_percentage"] is None for group in february["groups"]))
        self.assertEqual(self.request("/analysis/summary")[1]["groups"][0]["income_percentage"], "126.0")
        settings = self.category_store.load().model_dump()
        settings["income_group"] = None
        self.category_store.save(CategorySettings.model_validate(settings))
        self.assertTrue(all(group["income_percentage"] is None
                            for group in self.request("/analysis/summary")[1]["groups"]))
        # An existing Income group works without adding the optional setting.
        settings["groups"][1]["label"] = "Income"
        self.category_store.save(CategorySettings.model_validate(settings))
        automatic = self.request("/analysis/summary", [("years", 2001), ("months", "2001-01")])[1]
        self.assertEqual(automatic["income_group"], "earnings")
        self.assertEqual(automatic["income_total"], "1000.00")
        self.assertEqual([group["income_percentage"] for group in automatic["groups"]], ["125.0", "100.0"])
        self.assertNotIn('income_group =', self.category_store.path.read_text())
        self.assertIsNone(self.category_store.load().income_group)

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

    def test_category_update_accepts_each_category_and_changes_only_the_target_category(self):
        self.save_transaction("fixture-one")
        self.save_transaction("fixture-two", transaction_date=datetime(2001, 1, 1))
        original_rows = self.api.get_transactions()
        original_totals = self.api.get_overview()
        for category in [category.id for category in self.category_store.load().categories]:
            with self.subTest(category=category):
                self.assertEqual(self.request(
                    "/transactions/1/category", method="PATCH", body={"category": category},
                ), (200, {"id": 1, "category": category}))
                expected = [{**row, "category": category if row["id"] == 1 else row["category"]}
                            for row in original_rows]
                self.assertEqual(self.request("/transactions"), (200, expected))
                self.assertEqual(self.api.get_overview(), original_totals)

    def test_category_update_can_clear_a_category_with_null(self):
        self.save_transaction("fixture-one")
        self.request("/transactions/1/category", method="PATCH", body={"category": "investment"})
        self.assertEqual(self.request(
            "/transactions/1/category", method="PATCH", body={"category": None},
        ), (200, {"id": 1, "category": None}))
        self.assertIsNone(self.api.get_transactions()[0]["category"])

    def test_settings_save_persists_custom_categories_and_keeps_keywords_out_of_catalog(self):
        self.save_transaction("fixture-one")
        payload = {
            "categories": [{"id": "travel", "label": "Travel"}, {"id": "custom", "label": "Custom"}],
            "rules": [
                {"category": "custom", "keywords": ["fictional private keyword"], "transaction_type": "credit"},
                {"category": "travel", "keywords": ["example transport"], "transaction_type": None},
            ],
            "groups": [],
            "category_groups": {},
            "income_group": None,
        }
        self.assertEqual(self.request("/settings/categories", method="PUT", body=payload), (200, payload))
        self.assertEqual(self.request("/settings/categories"), (200, payload))
        self.assertEqual(TomlCategoryStore(self.category_store.path).load().model_dump(mode="json"), payload)
        self.assertEqual(self.request("/categories"), (200, payload["categories"]))
        self.assertEqual(self.request("/transactions/1/category", method="PATCH", body={"category": "travel"}),
                         (200, {"id": 1, "category": "travel"}))
        payload["categories"][0]["label"] = "Trips"
        self.assertEqual(self.request("/settings/categories", method="PUT", body=payload)[0], 200)
        self.assertEqual(self.request("/categories")[1][0]["label"], "Trips")
        self.assertEqual(self.api.get_transactions()[0]["category"], "travel")

    def test_group_settings_round_trip_without_rewriting_transactions_or_rules(self):
        self.save_transaction("fixture-grouped", category="custom")
        self.save_transaction("fixture-independent", category="lend")
        before = list(self.database.connection.iterdump())
        payload = {
            "categories": [{"id": "custom", "label": "Custom"}, {"id": "lend", "label": "Lend"}],
            "rules": [{"category": "custom", "keywords": ["example merchant"], "transaction_type": None}],
            "groups": [{"id": "needs", "label": "Example needs"}],
            "category_groups": {"custom": "needs"},
            "income_group": "needs",
        }
        self.assertEqual(self.request("/settings/categories", method="PUT", body=payload), (200, payload))
        self.assertEqual(self.request("/settings/categories"), (200, payload))
        payload["groups"][0]["label"] = "Renamed needs"
        self.assertEqual(self.request("/settings/categories", method="PUT", body=payload), (200, payload))
        payload["category_groups"] = {}
        self.assertEqual(self.request("/settings/categories", method="PUT", body=payload), (200, payload))
        self.assertEqual(list(self.database.connection.iterdump()), before)
        self.assertEqual(self.category_store.load().rules[0].category, "custom")
        self.assertEqual(self.request("/categories"), (200, payload["categories"]))

    def test_group_settings_reject_invalid_links_without_replacing_saved_config(self):
        original = self.category_store.path.read_bytes()
        for extra in (
            {"income_group": "missing"},
            {"category_groups": {"missing": "needs"}},
            {"category_groups": {"custom": "missing"}},
            {"category_groups": {"custom": ["needs", "other"]}},
            {"groups": [{"id": "needs", "label": "One"}, {"id": "needs", "label": "Two"}]},
        ):
            payload = {"categories": [{"id": "custom", "label": "Custom"}],
                       "groups": [{"id": "needs", "label": "Needs"}], **extra}
            with self.subTest(extra=extra):
                self.assertEqual(self.request("/settings/categories", method="PUT", body=payload)[0], 422)
                self.assertEqual(self.category_store.path.read_bytes(), original)

    def test_legacy_settings_request_defaults_to_no_groups_or_assignments(self):
        payload = {"categories": [{"id": "custom", "label": "Custom"}], "rules": []}
        status, body = self.request("/settings/categories", method="PUT", body=payload)
        self.assertEqual(status, 200)
        self.assertEqual(body, {**payload, "groups": [], "category_groups": {}, "income_group": None})

    def test_invalid_settings_are_rejected_without_changing_private_file(self):
        original = self.category_store.path.read_bytes()
        for payload in (
            {},
            {"categories": [{"id": "custom", "label": ""}]},
            {"categories": [{"id": "custom", "label": "One"}, {"id": "custom", "label": "Two"}]},
            {"rules": [{"category": "missing", "keywords": ["example"]}]},
            {"extra": "example"},
            [],
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/settings/categories", method="PUT", body=payload)[0], 422)
                self.assertEqual(self.category_store.path.read_bytes(), original)

    def test_config_edits_are_live_and_missing_file_has_no_builtin_fallback(self):
        self.category_store.path.unlink()
        self.assertEqual(self.request("/categories"), (200, []))
        self.assertEqual(self.request("/settings/categories"), (200, {"categories": [], "rules": [], "groups": [], "category_groups": {}, "income_group": None}))
        self.save_transaction("fixture-one")
        self.assertEqual(self.request("/transactions/1/category", method="PATCH", body={"category": "salary"})[0], 422)
        self.category_store.path.write_text('[categories]\ncustom = "Custom"\n')
        self.assertEqual(self.request("/categories"), (200, [{"id": "custom", "label": "Custom"}]))
        self.assertEqual(self.request("/transactions/1/category", method="PATCH", body={"category": "custom"})[0], 200)
        self.assertEqual(self.request("/settings/categories", method="PUT", body={"categories": [], "rules": []})[0], 200)
        self.assertEqual(self.api.get_transactions()[0]["category"], "custom")
        self.assertEqual(self.request("/transactions/1/category", method="PATCH", body={"category": "custom"})[0], 422)
        self.assertEqual(self.request("/transactions/1/category", method="PATCH", body={"category": None})[0], 200)

    def test_invalid_file_returns_safe_errors_and_can_be_repaired_through_settings_api(self):
        self.category_store.path.write_text('private = "fictional private keyword"\n')
        for path in ("/categories", "/settings/categories"):
            with self.subTest(path=path):
                status, body = self.request(path)
                self.assertEqual(status, 503)
                self.assertNotIn("fictional private keyword", json.dumps(body))
        self.assertEqual(self.request("/settings/categories", method="PUT", body={"categories": [], "rules": []})[0], 200)
        self.assertEqual(self.request("/categories"), (200, []))

    def test_category_update_returns_404_for_a_missing_transaction(self):
        self.save_transaction("fixture-one")
        self.assertEqual(self.request(
            "/transactions/999/category", method="PATCH", body={"category": "salary"},
        ), (404, {"detail": "Transaction not found"}))
        self.assertEqual(len(self.api.get_transactions()), 1)
        self.assertIsNone(self.api.get_transactions()[0]["category"])

    def test_category_update_rejects_invalid_ids_and_bodies_without_changing_data(self):
        self.save_transaction("fixture-one")
        self.database.update_transaction_category(1, "necessary")
        original_rows = self.api.get_transactions()
        for value in ("", "Salary", "salary ", "unknown", 1, True, [], {}, "salary'; DROP TABLE transactions; --"):
            with self.subTest(value=value):
                status, body = self.request(
                    "/transactions/1/category", method="PATCH", body={"category": value},
                )
                self.assertEqual(status, 422)
                self.assertIn("detail", body)
        for payload in ({}, {"category": "salary", "amount": "999"}, [], "salary", b"null", b'{"category":'):
            with self.subTest(payload=payload):
                self.assertEqual(self.request(
                    "/transactions/1/category", method="PATCH", body=payload,
                )[0], 422)
        self.assertEqual(self.request("/transactions/1/category", method="PATCH")[0], 422)
        for transaction_id in ("0", "-1", "not-an-id", "1 OR 1=1"):
            with self.subTest(transaction_id=transaction_id):
                self.assertEqual(self.request(
                    f"/transactions/{transaction_id}/category", method="PATCH", body={"category": "salary"},
                )[0], 422)
        self.assertEqual(self.api.get_transactions(), original_rows)

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
            for path in ("/overview", "/transactions", "/analysis/summary"):
                with self.subTest(path=path, parameters=parameters):
                    status, body = self.request(path, parameters)
                    self.assertEqual(status, 422)
                    self.assertIn("detail", body)
