"""Config-driven categories using fictional rules and temporary files only."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from finvault.categories import CategoryConfigError, TomlCategoryStore
from finvault.models.category import Category, CategoryGroup, CategoryRule, CategorySettings


class CategorySettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private" / "categories.toml"
        self.store = TomlCategoryStore(self.path)

    def settings(self, label="Example \"café\" \\ Travel\nNext line"):
        return CategorySettings(
            categories=(Category(id="custom", label=label),),
            rules=(
                CategoryRule(category="custom", keywords=("example employer", "café \\ \"payroll\""), transaction_type="credit"),
                CategoryRule(category="custom", keywords=("another example",)),
            ),
        )

    def test_missing_file_returns_no_categories_or_rules_and_creates_nothing(self):
        self.assertEqual(self.store.load(), CategorySettings(categories=()))
        self.assertFalse(self.path.parent.exists())

    def test_round_trip_preserves_unicode_quotes_rule_order_and_optional_type(self):
        settings = self.settings()
        self.store.save(settings)
        self.assertEqual(TomlCategoryStore(self.path).load(), settings)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_file_edits_are_read_without_restarting_store(self):
        self.store.save(self.settings("Before"))
        self.path.write_text('[categories]\ntransport = "Travel"\n')
        self.assertEqual(self.store.load().categories, (Category(id="transport", label="Travel"),))

    def test_legacy_file_has_independent_categories_and_is_not_rewritten_on_load(self):
        self.path.parent.mkdir()
        original = '[categories]\ntravel = "Travel"\nlend = "Lend"\n'
        self.path.write_text(original)
        settings = self.store.load()
        self.assertEqual(settings.groups, ())
        self.assertEqual(settings.category_groups, {})
        self.assertEqual(self.path.read_text(), original)

    def test_groups_round_trip_with_optional_assignments_and_stable_category_ids(self):
        settings = CategorySettings(
            categories=(Category(id="travel", label="Travel"), Category(id="lend", label="Lend"),
                        Category(id="refunds", label="Refunds")),
            rules=(CategoryRule(category="travel", keywords=("example trip",)),),
            groups=(CategoryGroup(id="leisure", label="Example leisure"),
                    CategoryGroup(id="empty", label="Empty group")),
            category_groups={"travel": "leisure"},
            income_group="leisure",
        )
        self.store.save(settings)
        loaded = self.store.load()
        self.assertEqual(loaded, settings)
        self.assertNotIn("lend", loaded.category_groups)
        self.assertNotIn("refunds", loaded.category_groups)
        renamed = loaded.model_dump()
        renamed["groups"][0]["label"] = "Renamed group"
        self.store.save(CategorySettings.model_validate(renamed))
        self.assertEqual(self.store.load().category_groups, {"travel": "leisure"})
        self.assertEqual(self.store.load().income_group, "leisure")
        self.assertEqual(self.store.load().categories, settings.categories)
        self.assertEqual(self.store.load().rules, settings.rules)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_group_validation_rejects_ambiguous_or_dangling_assignments(self):
        base = {"categories": [{"id": "travel", "label": "Travel"}],
                "groups": [{"id": "leisure", "label": "Leisure"}]}
        variants = (
            {"income_group": "missing"},
            {"groups": [{"id": "leisure", "label": "One"}, {"id": "leisure", "label": "Two"}]},
            {"groups": [{"id": "one", "label": "Same"}, {"id": "two", "label": "same"}]},
            {"groups": [{"id": "Invalid ID", "label": "Invalid"}]},
            {"groups": [{"id": "leisure", "label": ""}]},
            {"groups": [{"id": "leisure", "label": "Leisure", "parent": "other"}]},
            {"category_groups": {"missing": "leisure"}},
            {"category_groups": {"travel": "missing"}},
            {"category_groups": {"travel": ["leisure", "other"]}},
            {"category_groups": {"travel": None}},
        )
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ValidationError):
                CategorySettings.model_validate({**base, **variant})

    def test_invalid_group_tables_return_sanitized_errors(self):
        self.path.parent.mkdir()
        for extra in (
            'groups = []\n',
            'category_groups = []\n',
            '[groups]\none = "Same"\ntwo = "same"\n',
            '[category_groups]\ncustom = "fictional_private_group"\n',
            '[groups]\ncustom = "Group"\n[category_groups]\nmissing = "custom"\n',
        ):
            with self.subTest(extra=extra):
                self.path.write_text(extra + '\n[categories]\ncustom = "Custom"\n')
                with self.assertRaises(CategoryConfigError) as caught:
                    self.store.load()
                self.assertEqual(str(caught.exception), "Invalid category settings; check categories.toml")

    def test_default_path_is_working_directory_and_environment_can_override_it(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(TomlCategoryStore().path, Path.cwd() / "categories.toml")
        with patch.dict(os.environ, {"FINVAULT_CATEGORIES_CONFIG": str(self.path)}):
            self.assertEqual(TomlCategoryStore().path, self.path)

    def test_invalid_files_never_silently_fall_back_or_disclose_private_data(self):
        secret = "fictional private keyword"
        bodies = (
            '[categories\n',
            '[categories]\ncustom = "One"\ncustom = "Two"\n',
            '[categories]\ncustom = 123\n',
            'categories = []\n',
            '[categories]\nBadID = "Example"\n',
            '[categories]\ncustom = ""\n',
            '[categories]\na = "Same"\nb = "same"\n',
            f'[[rules]]\ncategory = "missing"\nkeywords = ["{secret}"]\n',
            f'unknown = "{secret}"\n',
            '[categories]\ncustom = "Example"\n[[rules]]\ncategory = "custom"\nkeywords = []\n',
            '[categories]\ncustom = "Example"\n[[rules]]\ncategory = "custom"\nkeywords = ["   "]\n',
            '[categories]\ncustom = "Example"\n[[rules]]\ncategory = "custom"\nkeywords = ["example"]\ntransaction_type = "both"\n',
        )
        self.path.parent.mkdir()
        for body in bodies:
            with self.subTest(body=body):
                self.path.write_text(body)
                with self.assertRaises(CategoryConfigError) as caught:
                    self.store.load()
                self.assertNotIn(secret, str(caught.exception))

    def test_models_reject_duplicate_ids_unknown_rule_targets_and_extra_fields(self):
        for payload in (
            {"categories": [{"id": "custom", "label": "One"}, {"id": "custom", "label": "Two"}]},
            {"categories": [{"id": "custom", "label": "One"}], "rules": [{"category": "missing", "keywords": ["example"]}]},
            {"categories": [{"id": "custom", "label": "One", "extra": True}]},
            {"categories": [{"id": 1, "label": "One"}]},
            {"unknown": True},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                CategorySettings.model_validate(payload)
        category = Category(id="custom", label="Custom")
        with self.assertRaises(ValidationError):
            category.label = "Changed"

    def test_failed_atomic_replace_preserves_previous_settings_and_cleans_temporary_file(self):
        original = self.settings("Before")
        self.store.save(original)
        with patch("finvault.categories.os.replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(CategoryConfigError):
                self.store.save(self.settings("After"))
        self.assertEqual(self.store.load(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_concurrent_saves_always_leave_a_complete_valid_document(self):
        choices = [self.settings(f"Example {index}") for index in range(10)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(self.store.save, choices))
        self.assertIn(self.store.load(), choices)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_repository_example_is_valid_and_contains_no_active_private_rules(self):
        example = Path(__file__).resolve().parents[1] / "categories.example.toml"
        settings = TomlCategoryStore(example).load()
        self.assertTrue(settings.categories)
        self.assertEqual(settings.rules, ())
        self.assertEqual({group.label for group in settings.groups},
                         {"Necessary", "Lifestyle", "Investments", "Income"})
        self.assertNotIn("lend", settings.category_groups)
        self.assertNotIn("refunds", settings.category_groups)


if __name__ == "__main__":
    unittest.main()
