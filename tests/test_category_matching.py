"""Standalone category matching with fictional bodies and temporary config."""

from pathlib import Path
import tempfile
import unittest

from finvault.categories import TomlCategoryStore
from finvault.category_matching import CategoryMatcher, KeywordCategoryMatcher
from finvault.models.category import Category, CategoryRule, CategorySettings


class KeywordCategoryMatcherTests(unittest.TestCase):
    def settings(self, *rules):
        return CategorySettings(
            categories=(
                Category(id="custom_incoming", label="Example income"),
                Category(id="custom_outgoing", label="Example spending"),
            ),
            rules=rules,
        )

    def matcher(self, *rules) -> CategoryMatcher:
        return KeywordCategoryMatcher(self.settings(*rules))

    def test_returns_configured_id_instead_of_label_ignoring_case(self):
        matcher = self.matcher(CategoryRule(category="custom_incoming", keywords=("example payroll",), transaction_type="credit"))
        self.assertEqual(matcher.match("Credit received: EXAMPLE PAYROLL.", "credit"), "custom_incoming")

    def test_any_keyword_can_match_without_requiring_the_others(self):
        matcher = self.matcher(CategoryRule(category="custom_outgoing", keywords=("fictional cafe", "example market")))
        for body in ("Paid Fictional Cafe", "Paid Example Market"):
            with self.subTest(body=body):
                self.assertEqual(matcher.match(body, "debit"), "custom_outgoing")

    def test_first_matching_rule_wins_and_configured_order_changes_priority(self):
        first = CategoryRule(category="custom_incoming", keywords=("example",))
        second = CategoryRule(category="custom_outgoing", keywords=("example market",))
        self.assertEqual(self.matcher(first, second).match("Example Market", "debit"), "custom_incoming")
        self.assertEqual(self.matcher(second, first).match("Example Market", "debit"), "custom_outgoing")

    def test_mismatched_transaction_type_skips_to_the_next_eligible_rule(self):
        credit = CategoryRule(category="custom_incoming", keywords=("example",), transaction_type="credit")
        debit = CategoryRule(category="custom_outgoing", keywords=("example",), transaction_type="debit")
        matcher = self.matcher(credit, debit)
        self.assertEqual(matcher.match("Example payment", "debit"), "custom_outgoing")
        self.assertEqual(matcher.match("Example refund", "credit"), "custom_incoming")
        self.assertIsNone(self.matcher(credit).match("Example payment", "debit"))
        self.assertIsNone(self.matcher(debit).match("Example refund", "credit"))

    def test_rule_without_type_restriction_accepts_both_debit_and_credit(self):
        matcher = self.matcher(CategoryRule(category="custom_outgoing", keywords=("example shop",)))
        for kind in ("debit", "credit"):
            with self.subTest(kind=kind):
                self.assertEqual(matcher.match("Example Shop transaction", kind), "custom_outgoing")

    def test_empty_body_unmatched_body_and_empty_rules_remain_uncategorised(self):
        matcher = self.matcher(CategoryRule(category="custom_incoming", keywords=("example payroll",)))
        for body in ("", " \t\n", "Unrelated transaction"):
            with self.subTest(body=body):
                self.assertIsNone(matcher.match(body, "credit"))
        self.assertIsNone(self.matcher().match("Example payroll", "credit"))
        self.assertIsNone(KeywordCategoryMatcher(CategorySettings(categories=())).match("Anything", "debit"))

    def test_literal_substring_matching_does_not_interpret_regex_characters(self):
        matcher = self.matcher(CategoryRule(category="custom_outgoing", keywords=("example.market+",)))
        self.assertEqual(matcher.match("Paid via ref-example.market+-001", "debit"), "custom_outgoing")
        self.assertIsNone(matcher.match("Paid exampleXmarket", "debit"))
        self.assertIsNone(matcher.match("Paid example.market", "debit"))

    def test_phrases_match_across_line_breaks_repeated_spaces_and_nonbreaking_spaces(self):
        matcher = self.matcher(CategoryRule(category="custom_incoming", keywords=("Example   payroll",)))
        for body in ("Example\npayroll", "Example\r\n\tpayroll", "Example\u00a0payroll"):
            with self.subTest(body=body):
                self.assertEqual(matcher.match(body, "credit"), "custom_incoming")

    def test_unicode_casefolding_works_in_both_body_and_keywords(self):
        for keyword, body in (("CAFÉ", "Paid café"), ("Straße", "Paid STRASSE"), ("STRASSE", "Paid Straße")):
            with self.subTest(keyword=keyword):
                matcher = self.matcher(CategoryRule(category="custom_outgoing", keywords=(keyword,)))
                self.assertEqual(matcher.match(body, "debit"), "custom_outgoing")

    def test_validated_private_config_can_build_matcher_without_modifying_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "categories.toml"
            store = TomlCategoryStore(path)
            store.save(self.settings(CategoryRule(
                category="custom_incoming", keywords=("fictional employer",), transaction_type="credit",
            )))
            original = path.read_bytes()
            matcher = KeywordCategoryMatcher(store.load())
            self.assertEqual(matcher.match("Payment from Fictional Employer", "credit"), "custom_incoming")
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_transaction_type_is_rejected(self):
        matcher = self.matcher(CategoryRule(category="custom_incoming", keywords=("example",)))
        for kind in ("", "refund", "Credit"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "Transaction type"):
                matcher.match("Example payment", kind)
