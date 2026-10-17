"""Synthetic banking fixtures only; never copy personal email records here."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from finvault.database.database import Database
from finvault.category_matching import KeywordCategoryMatcher
from finvault.models.category import CategorySettings
from finvault.models.email import Email
from finvault.parsers.hdfc import HDFCParser
from finvault.processing.transaction_processor import TransactionProcessor

IST = timezone(timedelta(hours=5, minutes=30), "IST")


class HDFCParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = HDFCParser()
        self.received_at = datetime(2001, 10, 14, 17, 30, tzinfo=timezone.utc)

    def email(self, body, gmail_id="sample"):
        return Email(gmail_id, "alerts@example.com", "Account update", self.received_at, "", body)

    def parse(self, body, source="Credit"):
        transaction = self.parser.parse(self.email(body), source)
        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.currency, "INR")
        self.assertEqual(transaction.email_id, "sample")
        self.assertEqual(transaction.transaction_date.utcoffset(), timedelta(hours=5, minutes=30))
        return transaction

    def test_debit_card_purchase_under_overlapping_credit_label(self):
        body = (
            "Thank you for using your HDFC Bank Debit Card ending 0000 "
            "for Rs 42.50 at Example Merchant"
        )
        for label in ("Debit card", "Credit"):
            with self.subTest(label=label):
                transaction = self.parse(body, label)
                self.assertEqual(transaction.amount, Decimal("42.50"))
                self.assertEqual(transaction.merchant, "Example Merchant")
                self.assertEqual(transaction.source, "Debit card")
                self.assertEqual(transaction.account, "HDFC DC 0000")
                self.assertEqual(transaction.transaction_type, "debit")
                self.assertEqual(transaction.transaction_date, self.received_at)

    def test_dated_debit_card_purchase_variants(self):
        for prefix in ("your HDFC Bank Debit Card ending", "HDFC Bank Debit Card ending with"):
            with self.subTest(prefix=prefix):
                transaction = self.parse(
                    f"Thank you for using {prefix} 0000 for Rs. 1,042.50 at\n"
                    "EXAMPLE MERCHANT on 13-10-2001 09:15:30. After the above transaction, "
                    "the available balance on your card is Rs 99,000.00.",
                    "Debit card",
                )
                self.assertEqual(transaction.amount, Decimal("1042.50"))
                self.assertEqual(transaction.merchant, "EXAMPLE MERCHANT")
                self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 9, 15, 30, tzinfo=IST))

    def test_atm_withdrawal(self):
        transaction = self.parse(
            "Thank you for using your HDFC Bank Debit Card ending 0000 "
            "for ATM withdrawal for Rs 10,000.00 in EXAMPLE CITY at EXAMPLE ROAD "
            "on 13-10-2001 09:15:30. After the above transaction, the total "
            "available balance on your card is Rs 99,000.00.", "Debit card",
        )
        self.assertEqual(transaction.amount, Decimal("10000.00"))
        self.assertEqual(transaction.merchant, "ATM withdrawal: EXAMPLE ROAD")
        self.assertEqual(transaction.transaction_type, "debit")

    def test_debit_card_debited_alert(self):
        transaction = self.parse(
            "Rs.42.50 is debited from your HDFC Bank Debit Card ending 0000 "
            "at EXAMPLE SHOP on 13 Oct, 2001 at 09:15:30.", "Debit card",
        )
        self.assertEqual(transaction.amount, Decimal("42.50"))
        self.assertEqual(transaction.merchant, "EXAMPLE SHOP")
        self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 9, 15, 30, tzinfo=IST))

    def test_completed_recurring_payment(self):
        transaction = self.parse(
            "Your Example Services bill, set up through E-mandate (Auto payment), "
            "has been successfully paid using your HDFC Bank Debit Card ending 0000. "
            "Transaction Details: Amount: INR 1,042.50 Date: 13/10/2001 SI Hub ID: example",
            "Debit card",
        )
        self.assertEqual(transaction.amount, Decimal("1042.50"))
        self.assertEqual(transaction.merchant, "Example Services")
        self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 23, tzinfo=IST))

    def test_reversals_are_credits_at_notification_time(self):
        for wording in ("is", "has been"):
            with self.subTest(wording=wording):
                transaction = self.parse(
                    "We note that a transaction for Rs 42.50, on your HDFC Bank "
                    "Debit Card ending 0000 at Example Merchant on 10-10-2001 09:15:30 "
                    f"{wording} reversed. Contact Bank if refund is not received in 9 days.",
                    "Debit card",
                )
                self.assertEqual(transaction.amount, Decimal("42.50"))
                self.assertEqual(transaction.merchant, "Example Merchant")
                self.assertEqual(transaction.transaction_type, "credit")
                self.assertEqual(transaction.source, "Debit card")
                self.assertEqual(transaction.transaction_date, self.received_at)

    def test_upi_credit_with_or_without_sender_name(self):
        for sender in ("", "Example Sender "):
            with self.subTest(sender=sender):
                transaction = self.parse(
                    "Rs. 10,000.00 is successfully credited to your account **0000 "
                    f"by VPA payer@example.invalid {sender}on 13-10-01. "
                    "Your UPI transaction reference number is 000000000000."
                )
                self.assertEqual(transaction.amount, Decimal("10000.00"))
                self.assertEqual(transaction.merchant, sender.strip() or "payer@example.invalid")
                self.assertEqual(transaction.reference, "000000000000.")
                self.assertEqual(transaction.transaction_type, "credit")
                self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 23, tzinfo=IST))

    def test_account_credit_variants_do_not_use_available_balance(self):
        for verb, preposition in (("added", "from"), ("credited", "by")):
            with self.subTest(verb=verb):
                transaction = self.parse(
                    "HDFC BANK Dear Customer, Greetings from HDFC Bank! "
                    f"Rs.INR 1,00,000.00 has been successfully {verb} to your account "
                    f"ending XX0000 {preposition} NEFT Cr-EXAMPLE-REFERENCE on 13-OCT-2001. "
                    "The available balance in your account is Rs. INR 9,99,999.00."
                )
                self.assertEqual(transaction.amount, Decimal("100000.00"))
                self.assertEqual(transaction.merchant, "NEFT Cr-EXAMPLE-REFERENCE")
                self.assertEqual(transaction.account, "HDFC XX0000")
                self.assertEqual(transaction.transaction_type, "credit")
                self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 23, tzinfo=IST))

    def test_deposit_alert_uses_credited_amount_and_complete_neft_sender(self):
        reference = "NEFT Cr-TEST0000000-Example Sender-Example Recipient-FIXTURE000000"
        body = (
            "HDFC BANK Dear Customer, You have received a credit in your account. "
            "The amount credited/received is INR 1,042.50 in your account XX0000 "
            f"on 13-OCT-2001 on account of {reference} "
            "Your A/c is at HDFC Bank Example Branch. "
            "Your Available balance is INR 99,999.00."
        )
        for separator in (" ", "\n", "\t", "\u00a0"):
            with self.subTest(separator=repr(separator)):
                transaction = self.parse(body.replace(" ", separator))
                self.assertEqual(transaction.amount, Decimal("1042.50"))
                self.assertEqual(transaction.merchant, "Example Sender")
                self.assertEqual(transaction.reference, reference)
                self.assertEqual(transaction.account, "HDFC XX0000")
                self.assertEqual(transaction.transaction_type, "credit")
                self.assertEqual(transaction.source, "Credit")
                self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 23, tzinfo=IST))

    def test_deposit_alert_keeps_other_narrations_without_guessing_sender(self):
        for reference in (
            "UPI/000000000000/Example Sender/payer@example.invalid",
            "TEST0000-000000000-Example Sender",
            "NEFT Cr-Unknown reference format",
        ):
            with self.subTest(reference=reference):
                transaction = self.parse(
                    "The amount credited/received is INR 42.50 in your account **0000 "
                    f"on 13-Oct-2001 on account of {reference} "
                    "Your A/c is at HDFC Bank Example Branch. "
                    "Your Available balance is INR 9,999.00."
                )
                self.assertEqual(transaction.merchant, reference)
                self.assertEqual(transaction.reference, reference)
                self.assertEqual(transaction.amount, Decimal("42.50"))

    def test_deposit_alert_requires_transaction_details_not_just_a_subject_or_balance(self):
        bodies = (
            "Your Available balance is INR 9,999.00.",
            "The amount will be credited/received is INR 42.50 in your account XX0000 "
            "on 13-OCT-2001 on account of Example Sender Your A/c is at Example Branch.",
            "The amount credited/received is INR 42.50 in your account XX0000 "
            "on account of Example Sender Your A/c is at Example Branch.",
            "The amount credited/received is INR 42.50 in your account "
            "on 13-OCT-2001 on account of Example Sender Your A/c is at Example Branch.",
            "The amount credited/received is INR 42.50 in your account XX0000 "
            "on 13-OCT-2001 on account of Your A/c is at Example Branch.",
            "The amount credited/received is INR 42.50 in your account XX0000 "
            "on 13-OCT-2001 on account of Incomplete narration",
        )
        for body in bodies:
            with self.subTest(body=body):
                email = self.email(body)
                email.subject = "New Deposit Alert: Check your A/c balance now!"
                self.assertIsNone(self.parser.parse(email, "Credit"))

    def test_pending_deposit_alert_is_processed_once_and_not_duplicated_on_rerun(self):
        database = Database(":memory:")
        self.addCleanup(database.close)
        email = self.email(
            "The amount credited/received is INR 42.50 in your account XX0000 "
            "on 13-OCT-2001 on account of "
            "NEFT Cr-TEST0000000-Example Sender-Example Recipient-FIXTURE000000 "
            "Your A/c is at HDFC Bank Example Branch. Your Available balance is INR 9,999.00.",
            gmail_id="fixture-deposit-alert",
        )
        database.save_email(email, "Credit")
        self.assertEqual(len(database.get_emails(unprocessed_only=True)), 1)
        processor = TransactionProcessor(database, KeywordCategoryMatcher(CategorySettings(categories=())))
        processor.process()
        self.assertEqual(database.get_emails(unprocessed_only=True), [])
        processor.process()
        rows = database.connection.execute("SELECT amount, merchant FROM transactions").fetchall()
        self.assertEqual(rows, [("42.50", "Example Sender")])

    def test_account_debit_variants(self):
        for verb, credit_verb, suffix in (("deducted", "added", " account"), ("debited", "credited", "")):
            with self.subTest(verb=verb):
                transaction = self.parse(
                    f"Rs. INR 1,000.00 is {verb} from your account ending XX0000 "
                    f"and {credit_verb} to ACH D-EXAMPLE{suffix} on 13-OCT-2001. "
                    "The available balance in your account is Rs. INR 99,999.00."
                )
                self.assertEqual(transaction.amount, Decimal("1000.00"))
                self.assertEqual(transaction.merchant, "ACH D-EXAMPLE")
                self.assertEqual(transaction.transaction_type, "debit")
                self.assertEqual(transaction.source, "Bank transfer")
                self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 23, tzinfo=IST))

    def test_forex_debit_keeps_inr_amount(self):
        transaction = self.parse(
            "Rs.INR 9,000.00 is debited from your account ending XX0000 "
            "and credited to RFX 000000BTT00000 USD100.00@90.000 on 13-OCT-2001 "
            "Your available balance is Rs. INR 99,999.00"
        )
        self.assertEqual(transaction.amount, Decimal("9000.00"))
        self.assertEqual(transaction.transaction_type, "debit")
        self.assertEqual(transaction.merchant, "RFX 000000BTT00000 USD100.00@90.000")

    def test_neft_outgoing_without_explicit_date(self):
        cases = (
            ("Rs. 100000 has been deducted from your HDFC Bank account ending in XX0000 "
             "for a transfer to payee Example Recipient via NEFT using HDFC Bank Online Banking.",
             "Example Recipient"),
            ("Amount deducted Of Rs. 100000 From your HDFC Bank A/c XX0000 "
             "For NEFT transaction Via HDFC Bank Online Banking.", "NEFT transfer"),
        )
        for body, merchant in cases:
            with self.subTest(merchant=merchant):
                transaction = self.parse(body)
                self.assertEqual(transaction.amount, Decimal("100000"))
                self.assertEqual(transaction.merchant, merchant)
                self.assertEqual(transaction.transaction_type, "debit")
                self.assertEqual(transaction.transaction_date, self.received_at)

    def test_netbanking_payment_formats(self):
        cases = (
            "Thank you for using HDFC Bank NetBanking for payment of Rs. 1,000.00 "
            "from A/c XX0000 to EXAMPLE MERCHANT Not you?Call 00000000000 For any queries contact us.",
            "Thank you for using HDFC Bank NetBanking. We hope your payment experience "
            "was safe and smooth. Payment Details Amount: Rs. 1,000.00 "
            "From Account: **********0000 Paid To: EXAMPLE MERCHANT Important Note "
            "If you made this payment, no action is needed.",
        )
        for body in cases:
            with self.subTest(body=body):
                transaction = self.parse(body)
                self.assertEqual(transaction.amount, Decimal("1000.00"))
                self.assertEqual(transaction.merchant, "EXAMPLE MERCHANT")
                self.assertEqual(transaction.source, "NetBanking")
                self.assertEqual(transaction.transaction_type, "debit")
                self.assertEqual(transaction.transaction_date, self.received_at)

    def test_notices_are_skipped_without_creating_transactions(self):
        cases = (
            "HDFC BANK OTP is 000000 for txn of INR 100.00 at EXAMPLE on HDFC Bank card ending 0000.",
            "HDFC BANK Dear Customer, Greetings from HDFC Bank! Available balance in your account ending XX0000 is Rs. INR 1,000.00 as on 13-OCT-01.",
            "HDFC BANK Dear Customer, Greetings from HDFC Bank! Your available balance in account ending XX0000 is Rs. INR 1,000.00 as on 13-OCT-01.",
            "There is an upcoming E-mandate (Auto payment) of INR 42.50 for Example. Amount will be debited from your HDFC Bank Debit Card ending 0000 on 15/10/2001.",
            "Your payment for Example is registered for E-mandate (Auto payment) using your HDFC Bank Debit Card ending 0000.",
            "A recent transaction on your HDFC Bank Debit Card ending in 0000 was not completed.",
            "Your transaction on Debit Card ending with xx0000 has failed. Merchant name: Example Merchant Amount: Rs. 42.50 Date: 13-10-2001 09:15:30",
            "This is to inform you have modified Card usage settings for Credit Card ending with XX0000.",
            "You have successfully changed the PIN for Debit Card ending 0000.",
            "You have successfully reset your NetBanking password via HDFC Bank NetBanking.",
            "You have successfully read secure usage tips and accepted the terms and conditions of using NetBanking.",
            "Your Biometric Login has been successfully set using HDFC Bank Mobile App.",
            "You've successfully registered your device and set your 4 Digit PIN.",
            "We have started the process to verify your mobile number for HDFC Bank MobileBanking App.",
            "There has been no activity in your Account since 13-10-2000. As per guidelines, presently your Account status as 'Dormant'",
        )
        for body in cases:
            with self.subTest(body=body):
                self.assertIsNone(self.parser.parse(self.email(body), "Credit"))
                self.assertIsNotNone(self.parser.get_skip_reason(self.email(body)))

    def test_unknown_format_is_not_silently_skipped(self):
        email = self.email("A new bank transaction format we do not recognize.")
        self.assertIsNone(self.parser.parse(email, "Credit"))
        self.assertIsNone(self.parser.get_skip_reason(email))

    def test_incomplete_payment_is_not_parsed(self):
        email = self.email("Thank you for using your HDFC Bank Debit Card ending 0000 for Rs 42.50")
        self.assertIsNone(self.parser.parse(email, "Credit"))
        self.assertIsNone(self.parser.get_skip_reason(email))

    def test_existing_upi_and_credit_card_routes(self):
        upi = self.parse(
            "Rs.42.50 is debited towards VPA payer@example.invalid (Example Merchant) on 13-10-01. "
            "UPI transaction reference no.:000000000000", "UPI",
        )
        self.assertEqual(upi.amount, Decimal("42.50"))
        self.assertEqual(upi.source, "UPI")
        card = self.parse(
            "Rs. 42.50 is debited from your HDFC Bank Credit Card ending 0000 "
            "towards Example Merchant on 13 Oct, 2001 at 09:15:30", "Credit card",
        )
        self.assertEqual(card.amount, Decimal("42.50"))
        self.assertEqual(card.source, "Credit card")

    def test_credit_card_alternative_format_and_multiline_merchant(self):
        transaction = self.parse(
            "Credit Card ending in 0000. You made a transaction of Rs. 42.50 "
            "at Example\nMerchant on 13-10-2001 09:15:30", "Credit card",
        )
        self.assertEqual(transaction.amount, Decimal("42.50"))
        self.assertEqual(transaction.account, "HDFC CC 0000")
        self.assertEqual(transaction.merchant, "Example\nMerchant")
        self.assertEqual(transaction.transaction_type, "debit")
        self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 9, 15, 30, tzinfo=IST))

    def test_original_upi_credit_format(self):
        transaction = self.parse(
            "Rs.42.50 has been successfully credited to your HDFC Bank account "
            "ending in XX0000. Date: 13-10-01 Sender: Example Sender\n"
            "UPI Reference No.: FIXTURE-REFERENCE"
        )
        self.assertEqual(transaction.amount, Decimal("42.50"))
        self.assertEqual(transaction.merchant, "Example Sender")
        self.assertEqual(transaction.account, "HDFC XX0000")
        self.assertEqual(transaction.reference, "FIXTURE-REFERENCE")
        self.assertEqual(transaction.transaction_type, "credit")
        self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 23, tzinfo=IST))

    def test_original_upi_credit_preserves_sender_across_whitespace_variants(self):
        senders = (
            ("Example Sender", "Example Sender"),
            ("Example   Sender", "Example Sender"),
            ("Example\nSender", "Example Sender"),
            ("Example\r\nSender", "Example Sender"),
            ("Éxample O'Name-Sender", "Éxample O'Name-Sender"),
            ("X", "X"),
        )
        for sender, expected in senders:
            for separator in (" ", "\n", "\r\n\t"):
                with self.subTest(sender=sender, separator=separator):
                    transaction = self.parse(
                        "Rs.42.50 has been successfully credited to your HDFC Bank "
                        "account ending in XX0000. Date: 13-10-01 "
                        f"Sender: {sender}{separator}"
                        "UPI Reference No.: FIXTURE-REFERENCE\nSynthetic footer"
                    )
                    self.assertEqual(transaction.merchant, expected)
                    self.assertEqual(transaction.reference, "FIXTURE-REFERENCE")

    def test_original_upi_credit_missing_sender_is_not_parsed(self):
        for sender in ("", "   ", "\n\t"):
            with self.subTest(sender=sender):
                record = self.email(
                    "Rs.42.50 has been successfully credited to your HDFC Bank "
                    "account ending in XX0000. Date: 13-10-01 "
                    f"Sender: {sender}\nUPI Reference No.: FIXTURE-REFERENCE"
                )
                self.assertIsNone(self.parser.parse(record, "Credit"))

    def test_processor_stores_complete_upi_credit_sender(self):
        database = Database(":memory:")
        self.addCleanup(database.close)
        database.save_email(self.email(
            "Rs.42.50 has been successfully credited to your HDFC Bank "
            "account ending in XX0000. Date: 13-10-01 Sender: Example\nSender\n"
            "UPI Reference No.: FIXTURE-REFERENCE"
        ), "Credit")
        processor = TransactionProcessor(database, KeywordCategoryMatcher(CategorySettings(categories=())))
        processor.process()
        processor.process()
        self.assertEqual(database.connection.execute(
            "SELECT merchant, amount, reference FROM transactions"
        ).fetchall(), [("Example Sender", "42.50", "FIXTURE-REFERENCE")])

    def test_neft_credit_format(self):
        transaction = self.parse(
            "Amount received: INR 1,042.50\nAccount: XX0000\n"
            "Date: 13-OCT-2001\nReference Details: FIXTURE-REFERENCE"
        )
        self.assertEqual(transaction.amount, Decimal("1042.50"))
        self.assertEqual(transaction.merchant, "NEFT")
        self.assertEqual(transaction.reference, "FIXTURE-REFERENCE")
        self.assertEqual(transaction.transaction_type, "credit")
        self.assertEqual(transaction.transaction_date, datetime(2001, 10, 13, 23, tzinfo=IST))

    def test_neft_credit_extracts_sender_and_preserves_complete_reference(self):
        for sender in ("Example Sender", "Example   Sender", "Example\tSender"):
            with self.subTest(sender=sender):
                reference = (
                    f"NEFT Cr-TEST0000000-{sender}-Example Recipient-FIXTURE000000"
                )
                transaction = self.parse(
                    "Amount received: INR 1,042.50\nAccount: XX0000\n"
                    f"Date: 13-OCT-2001\nReference Details:\n{reference}\n"
                    "Synthetic footer"
                )
                self.assertEqual(transaction.amount, Decimal("1042.50"))
                self.assertEqual(transaction.merchant, "Example Sender")
                self.assertEqual(transaction.reference, reference)
                self.assertEqual(transaction.account, "HDFC XX0000")
                self.assertEqual(transaction.transaction_type, "credit")

    def test_neft_unknown_reference_formats_do_not_guess_sender(self):
        references = (
            "FIXTURE-REFERENCE",
            "NEFT Cr-Unknown reference format",
            "NEFT Cr-INVALID-Example Sender-Example Recipient-FIXTURE000000",
            "NEFT Cr-TEST0000000-Example-Hyphen-Sender-Example Recipient-FIXTURE000000",
            "NEFT Cr-TEST0000000- -Example Recipient-FIXTURE000000",
        )
        for reference in references:
            with self.subTest(reference=reference):
                transaction = self.parse(
                    "Amount received: INR 42.50\nAccount: XX0000\n"
                    f"Date: 13-OCT-2001\nReference Details: {reference}\nSynthetic footer"
                )
                self.assertEqual(transaction.merchant, "NEFT")
                self.assertEqual(transaction.reference, reference)

    def test_upi_debit_without_optional_reference(self):
        transaction = self.parse(
            "Rs.42.50 is debited towards VPA payer@example.invalid "
            "(Example Merchant) on 13-10-01.", "UPI",
        )
        self.assertEqual(transaction.amount, Decimal("42.50"))
        self.assertEqual(transaction.merchant, "Example Merchant")
        self.assertIsNone(transaction.reference)

    def test_empty_body_and_unsupported_source_produce_no_transaction(self):
        for source in ("UPI", "Credit card", "Debit card", "Credit", "Unknown"):
            with self.subTest(source=source):
                self.assertIsNone(self.parser.parse(self.email(""), source))
        self.assertIsNone(self.parser.parse(self.email(
            "Thank you for using your HDFC Bank Debit Card ending 0000 "
            "for Rs 42.50 at Example Merchant"
        ), "Unknown"))

    def test_processor_distinguishes_skips_failures_and_parsed_transactions(self):
        database = Database(":memory:")
        self.addCleanup(database.close)
        database.save_email(self.email(
            "Thank you for using your HDFC Bank Debit Card ending 0000 for Rs 42.50 at Example Merchant",
            "purchase",
        ), "Credit")
        database.save_email(self.email(
            "HDFC BANK OTP is 000000 for txn of INR 100.00 at EXAMPLE on HDFC Bank card ending 0000.",
            "otp",
        ), "Credit")
        database.save_email(self.email("Unrecognized transaction format", "unknown"), "Credit")
        processor = TransactionProcessor(database, KeywordCategoryMatcher(CategorySettings(categories=())))
        with self.assertLogs("finvault.processing.transaction_processor", level="DEBUG") as logs:
            processor.process()
        warnings = [record for record in logs.records if record.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("unknown", warnings[0].getMessage())
        self.assertIn("1 parsed, 1 skipped, 1 failed", logs.output[-1])
        with self.assertLogs("finvault.processing.transaction_processor", level="INFO"):
            processor.process()
        rows = database.connection.execute(
            "SELECT amount, merchant, source, transaction_type FROM transactions"
        ).fetchall()
        self.assertEqual(rows, [("42.50", "Example Merchant", "Debit card", "debit")])


class HDFCTimestampTests(unittest.TestCase):
    """Timestamp rules across all supported HDFC transaction formats."""

    def setUp(self):
        self.parser = HDFCParser()

    def timestamp(self, body, source, header):
        record = Email("fixture-time", "alerts@example.invalid", "Synthetic alert", header, "", body)
        result = self.parser.parse(record, source)
        self.assertIsNotNone(result)
        self.assertEqual(result.transaction_date.utcoffset(), timedelta(hours=5, minutes=30))
        return result.transaction_date.isoformat()

    def test_all_date_only_formats_use_ist_header_clock_and_keep_body_date(self):
        cases = (
            ("UPI", "Rs.42.50 is debited towards VPA payer@example.invalid "
             "(Example Merchant) on 13-10-01."),
            ("Credit", "Rs.42.50 has been successfully credited to your HDFC Bank account "
             "ending in XX0000. Date: 13-10-01 Sender: Example Sender "
             "UPI Reference No.: FIXTURE-REFERENCE"),
            ("Credit", "Rs. 42.50 is successfully credited to your account **0000 "
             "by VPA payer@example.invalid Example Sender on 13-10-01. "
             "Your UPI transaction reference number is FIXTURE-REFERENCE"),
            ("Credit", "Amount received: INR 42.50\nAccount: XX0000\n"
             "Date: 13-OCT-2001\nReference Details: FIXTURE-REFERENCE"),
            ("Credit", "The amount credited/received is INR 42.50 in your account XX0000 "
             "on 13-OCT-2001 on account of Example Sender Your A/c is at Example Branch."),
            ("Credit", "Rs.INR 42.50 has been successfully added to your account ending "
             "XX0000 from Example Sender on 13-OCT-2001."),
            ("Credit", "Rs. INR 42.50 is deducted from your account ending XX0000 "
             "and added to Example Recipient account on 13-OCT-2001."),
            ("Debit card", "Your Example Services bill, set up through E-mandate (Auto payment), "
             "has been successfully paid using your HDFC Bank Debit Card ending 0000. "
             "Transaction Details: Amount: INR 42.50 Date: 13/10/2001"),
        )
        # The two headers are the same instant; conversion crosses midnight.
        headers = (
            datetime(2001, 10, 14, 23, 50, tzinfo=timezone.utc),
            datetime(2001, 10, 15, 5, 20, tzinfo=IST),
        )
        for source, body in cases:
            for header in headers:
                with self.subTest(source=source, body=body, header=header):
                    self.assertEqual(self.timestamp(body, source, header), "2001-10-13T05:20:00+05:30")

    def test_all_header_only_formats_convert_the_full_timestamp(self):
        cases = (
            ("Debit card", "Thank you for using your HDFC Bank Debit Card ending 0000 "
             "for Rs 42.50 at Example Merchant"),
            ("Debit card", "We note that a transaction for Rs 42.50, on your HDFC Bank "
             "Debit Card ending 0000 at Example Merchant on 10-10-2001 09:15:30 is reversed."),
            ("Credit", "Rs. 42.50 has been deducted from your HDFC Bank account ending in "
             "XX0000 for a transfer to payee Example Recipient via NEFT using HDFC Bank Online Banking."),
            ("Credit", "Amount deducted Of Rs. 42.50 From your HDFC Bank A/c XX0000 "
             "For NEFT transaction Via HDFC Bank Online Banking."),
            ("Credit", "Thank you for using HDFC Bank NetBanking for payment of Rs. 42.50 "
             "from A/c XX0000 to EXAMPLE MERCHANT"),
            ("Credit", "Thank you for using HDFC Bank NetBanking. We hope your payment experience "
             "was safe and smooth. Payment Details Amount: Rs. 42.50 From Account: XX0000 "
             "Paid To: EXAMPLE MERCHANT"),
        )
        headers = (
            datetime(2001, 10, 14, 23, 50, tzinfo=timezone.utc),
            datetime(2001, 10, 15, 5, 20, tzinfo=IST),
        )
        for source, body in cases:
            for header in headers:
                with self.subTest(source=source, body=body, header=header):
                    self.assertEqual(self.timestamp(body, source, header), "2001-10-15T05:20:00+05:30")

    def test_explicit_body_timestamps_convert_offsets_and_preserve_ist(self):
        cases = (
            ("Debit card", "Thank you for using your HDFC Bank Debit Card ending 0000 "
             "for Rs 42.50 at Example Merchant on 13-10-2001 {clock}{zone}."),
            ("Debit card", "Thank you for using your HDFC Bank Debit Card ending 0000 "
             "for ATM withdrawal for Rs 42.50 at Example ATM on 13-10-2001 {clock}{zone}."),
            ("Debit card", "Rs.42.50 is debited from your HDFC Bank Debit Card ending 0000 "
             "at Example Merchant on 13 Oct, 2001 at {clock}{zone}."),
            ("Credit card", "Rs. 42.50 is debited from your HDFC Bank Credit Card ending 0000 "
             "towards Example Merchant on 13 Oct, 2001 at {clock}{zone}"),
            ("Credit card", "Credit Card ending in 0000. You made a transaction of Rs. 42.50 "
             "at Example Merchant on 13-10-2001 {clock}{zone}"),
        )
        variations = (
            ("23:50:00", " UTC", "2001-10-14T05:20:00+05:30"),
            ("23:50:00", " GMT", "2001-10-14T05:20:00+05:30"),
            ("23:50:00", "Z", "2001-10-14T05:20:00+05:30"),
            ("23:50:00", " +00:00", "2001-10-14T05:20:00+05:30"),
            ("23:50:00", " +0000", "2001-10-14T05:20:00+05:30"),
            ("23:50:00", " -04:00", "2001-10-14T09:20:00+05:30"),
            ("23:50:00", " +0800", "2001-10-13T21:20:00+05:30"),
            ("23:50:00", " IST", "2001-10-13T23:50:00+05:30"),
            ("23:50:00", " +05:30", "2001-10-13T23:50:00+05:30"),
            ("23:50:00", " +0530", "2001-10-13T23:50:00+05:30"),
            ("23:50:00", "", "2001-10-13T23:50:00+05:30"),
            ("23:50:00", " Zero liability protection", "2001-10-13T23:50:00+05:30"),
            # A known midnight must not be mistaken for a missing time.
            ("00:00:00", "", "2001-10-13T00:00:00+05:30"),
        )
        header = datetime(2001, 10, 15, 12, 0, tzinfo=timezone.utc)
        for source, template in cases:
            for clock, zone, expected in variations:
                with self.subTest(source=source, template=template, clock=clock, zone=zone):
                    self.assertEqual(self.timestamp(template.format(clock=clock, zone=zone), source, header), expected)

    def test_header_without_offset_is_treated_as_ist(self):
        header = datetime(2001, 10, 15, 5, 20)
        body = "Thank you for using your HDFC Bank Debit Card ending 0000 for Rs 42.50 at Example Merchant"
        self.assertEqual(self.timestamp(body, "Debit card", header), "2001-10-15T05:20:00+05:30")
        body = "Rs.42.50 is debited towards VPA payer@example.invalid (Example Merchant) on 13-10-01."
        self.assertEqual(self.timestamp(body, "UPI", header), "2001-10-13T05:20:00+05:30")

    def test_processing_persists_ist_and_keeps_email_header_unchanged(self):
        database = Database(":memory:")
        self.addCleanup(database.close)
        header = datetime(2001, 10, 14, 23, 50, tzinfo=timezone.utc)
        record = Email(
            "fixture-time", "alerts@example.invalid", "Synthetic alert", header, "",
            "Rs.42.50 is debited towards VPA payer@example.invalid (Example Merchant) on 13-10-01.",
        )
        database.save_email(record, "UPI")
        processor = TransactionProcessor(database, KeywordCategoryMatcher(CategorySettings(categories=())))
        processor.process()
        processor.process()
        self.assertEqual(database.connection.execute("SELECT transaction_date FROM transactions").fetchall(),
                         [("2001-10-13T05:20:00+05:30",)])
        self.assertEqual(database.connection.execute("SELECT received_at FROM emails").fetchone()[0],
                         "2001-10-14T23:50:00+00:00")


if __name__ == "__main__":
    unittest.main()
