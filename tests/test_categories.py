"""Config-driven categories using fictional rules and temporary files only."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from finvault.categories import CategoryConfigError, TomlCategoryStore
from finvault.models.category import Category, CategoryRule, CategorySettings


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


if __name__ == "__main__":
    unittest.main()
