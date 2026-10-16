import re
from datetime import datetime
from decimal import Decimal

from finvault.models.email import Email
from finvault.models.transaction import Transaction


class HDFCParser:

    def parse(self, email: Email, source: str) -> Transaction | None:
        if source == "UPI":
            return self._parse_upi(email)

        if source == "Credit card":
            return self._parse_credit_card(email)

        if source == "Debit card":
            return self._parse_debit_card(email)

        if source == "Credit":
            # Gmail labels can overlap; confirmed spending also appears here.
            return (
                self._parse_credit(email)
                or self._parse_debit_card(email)
                or self._parse_bank_debit(email)
            )

        return None

    def _parse_debit_card(self, email: Email) -> Transaction | None:
        body = " ".join(email.body_text.split())
        amount_pattern = r"(?P<amount>\d[\d,]*(?:\.\d+)?)"
        card_pattern = r"(?P<card>\d{4})"
        date_pattern = r"(?P<date>\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})"

        reversal = re.search(
            r"We note that a transaction for Rs\.?\s*" + amount_pattern
            + r",\s+on your HDFC Bank Debit Card ending\s+" + card_pattern
            + r"\s+at\s+(?P<merchant>.+?)\s+on\s+" + date_pattern
            + r"\s+(?:is|has been) reversed\b",
            body,
        )
        if reversal:
            return Transaction(
                amount=Decimal(reversal.group("amount").replace(",", "")),
                currency="INR",
                transaction_type="credit",
                merchant=reversal.group("merchant").strip(),
                # The date in the body belongs to the original purchase.
                transaction_date=email.received_at,
                account=f"HDFC DC {reversal.group('card')}",
                source="Debit card",
                reference=None,
                email_id=email.gmail_id,
            )

        # Purchase and ATM alerts; short alerts may omit the transaction date.
        match = re.search(
            r"Thank you for using (?:your\s+)?HDFC Bank Debit Card ending\s+(?:with\s+)?"
            + card_pattern
            + r"\s+for\s+(?P<atm>ATM withdrawal\s+for\s+)?Rs\.?\s*"
            + amount_pattern
            + r"\s+(?:in\s+.+?\s+)?at\s+(?P<merchant>.+?)"
            + r"(?:\s+on\s+" + date_pattern + r"(?=\.|\s|$)|$)",
            body,
        )
        if match:
            transaction_date = (
                datetime.strptime(match.group("date"), "%d-%m-%Y %H:%M:%S")
                if match.group("date") else email.received_at
            )
            merchant = match.group("merchant").strip()
            if match.group("atm"):
                merchant = f"ATM withdrawal: {merchant}"
        else:
            # Newer debit alerts use a month name and an explicit timestamp.
            match = re.search(
                r"Rs\.?\s*" + amount_pattern
                + r"\s+(?:is|has been) debited from your HDFC Bank Debit Card ending\s+"
                + card_pattern
                + r"\s+at\s+(?P<merchant>.+?)\s+on\s+"
                + r"(?P<date>\d{2} \w{3}, \d{4} at \d{2}:\d{2}:\d{2})",
                body,
            )
            if match:
                transaction_date = datetime.strptime(
                    match.group("date"), "%d %b, %Y at %H:%M:%S"
                )
                merchant = match.group("merchant").strip()
            else:
                # Only completed recurring payments count as transactions.
                match = re.search(
                    r"Your\s+(?P<merchant>.+?)\s+bill,\s+"
                    r"set up through E-mandate \(Auto payment\),\s+"
                    r"has been successfully paid using your HDFC Bank Debit Card ending\s+"
                    + card_pattern
                    + r"\.\s+Transaction Details:\s+Amount:\s+INR\s*"
                    + amount_pattern
                    + r"\s+Date:\s*(?P<date>\d{2}/\d{2}/\d{4})",
                    body,
                )
                if not match:
                    return None
                transaction_date = datetime.strptime(match.group("date"), "%d/%m/%Y")
                merchant = match.group("merchant").strip()

        return Transaction(
            amount=Decimal(match.group("amount").replace(",", "")),
            currency="INR",
            transaction_type="debit",
            merchant=merchant,
            transaction_date=transaction_date,
            account=f"HDFC DC {match.group('card')}",
            source="Debit card",
            reference=None,
            email_id=email.gmail_id,
        )

    def _parse_bank_debit(self, email: Email) -> Transaction | None:
        body = " ".join(email.body_text.split())
        amount = r"(?P<amount>\d[\d,]*(?:\.\d+)?)"
        account = r"(?P<account>[Xx*\d]+)"
        patterns = (
            (
                r"Rs\.?\s*(?:INR\s*)?" + amount
                + r"\s+is (?:deducted|debited) from your account ending\s+" + account
                + r"\s+and (?:added|credited) to\s+(?P<merchant>.+?)(?:\s+account)?\s+on\s+"
                + r"(?P<date>\d{2}-[A-Za-z]{3}-\d{4})",
                "%d-%b-%Y", "Bank transfer", None,
            ),
            (
                r"Rs\.?\s*" + amount
                + r"\s+has been deducted from your HDFC Bank account ending in\s+"
                + account
                + r"\s+for a transfer to payee\s+(?P<merchant>.+?)\s+"
                + r"via NEFT using HDFC Bank Online Banking\.",
                None, "Bank transfer", None,
            ),
            (
                r"Amount deducted Of Rs\.?\s*" + amount
                + r"\s+From your HDFC Bank A/c\s+" + account
                + r"\s+For NEFT transaction Via HDFC Bank Online Banking\.",
                None, "Bank transfer", "NEFT transfer",
            ),
            (
                r"Thank you for using HDFC Bank NetBanking for payment of Rs\.?\s*"
                + amount + r"\s+from A/c\s+" + account
                + r"\s+to\s+(?P<merchant>.+?)(?=\s+Not you\?|\s+For any queries|$)",
                None, "NetBanking", None,
            ),
            (
                r"Thank you for using HDFC Bank NetBanking\.\s+"
                r"We hope your payment experience was safe and smooth\.\s+"
                r"Payment Details\s+Amount:\s+Rs\.?\s*" + amount
                + r"\s+From Account:\s+" + account
                + r"\s+Paid To:\s+(?P<merchant>.+?)"
                + r"(?=\s+Important Note|\s+Not You\?|$)",
                None, "NetBanking", None,
            ),
        )
        for pattern, date_format, source, default_merchant in patterns:
            match = re.search(pattern, body)
            if not match:
                continue
            return Transaction(
                amount=Decimal(match.group("amount").replace(",", "")),
                currency="INR",
                transaction_type="debit",
                merchant=default_merchant or match.group("merchant").strip(),
                transaction_date=(
                    datetime.strptime(match.group("date"), date_format)
                    if date_format else email.received_at
                ),
                account=f"HDFC {match.group('account')}",
                source=source,
                reference=None,
                email_id=email.gmail_id,
            )
        return None

    def get_skip_reason(self, email: Email) -> str | None:
        body = " ".join(email.body_text.split())
        notices = (
            (r"\bOTP is\s+\d+\s+for txn\b", "payment OTP"),
            (
                r"(?:^|[!.]\s+)(?:Your )?Available balance in (?:your )?account\b",
                "balance update",
            ),
            (r"\bThere is an upcoming E-mandate\b", "upcoming payment"),
            (r"\bYour payment for .+? is registered for E-mandate\b", "mandate registration"),
            (r"\ba recent transaction on your HDFC Bank Debit Card .+? was not completed\b", "failed payment"),
            (r"\byour transaction on Debit Card .+? has failed\b", "failed payment"),
            (r"\byou have modified Card usage settings for (?:Debit|Credit) Card\b", "card settings"),
            (r"\bYou have successfully changed the PIN for Debit Card\b", "PIN change"),
            (r"\bYou have successfully reset your NetBanking password\b", "password reset"),
            (r"\bsuccessfully read secure usage tips and accepted\b", "banking terms accepted"),
            (r"\byour Biometric Login has been successfully set\b", "biometric login"),
            (r"\bsuccessfully registered your device and set your\b", "device registration"),
            (r"\bstarted the process to verify your mobile number\b", "mobile verification"),
            (r"\bThere has been no activity in your Account since\b.*?Account status as 'Dormant'", "dormant account"),
        )
        for pattern, reason in notices:
            if re.search(pattern, body, re.IGNORECASE):
                return reason
        return None

    def _parse_upi(self, email: Email) -> Transaction | None:
        body = email.body_text

        amount_match = re.search(
            r"Rs\.(\d+(?:\.\d+)?)\s+is debited",
            body,
        )

        transaction_match = re.search(
            r"towards VPA (.+?) \((.+?)\) on (\d{2}-\d{2}-\d{2})",
            body,
        )

        reference_match = re.search(
            r"UPI transaction reference no\.:(\S+)",
            body,
        )

        if not amount_match or not transaction_match:
            return None

        amount = Decimal(amount_match.group(1))
        vpa = transaction_match.group(1)
        merchant = transaction_match.group(2)

        date = datetime.strptime(
            transaction_match.group(3),
            "%d-%m-%y",
        ).date()

        transaction_date = datetime.combine(
            date,
            email.received_at.time(),
        )

        reference = reference_match.group(1) if reference_match else None

        return Transaction(
            amount=amount,
            currency="INR",
            transaction_type="debit",
            merchant=merchant,
            transaction_date=transaction_date,
            account="HDFC",
            source="UPI",
            reference=reference,
            email_id=email.gmail_id,
        )

    def _parse_credit_card(self, email: Email) -> Transaction | None:
        body = email.body_text

        # Format 1 & 2:
        # Rs. 434.00 is/has been debited from your HDFC Bank Credit Card...
        transaction_match = re.search(
            r"Rs\.\s*(\d+(?:\.\d+)?)\s+"
            r"(?:is|has been)\s+debited\s+"
            r"from your HDFC Bank Credit Card ending\s+"
            r"(\d+)\s+"
            r"towards\s+"
            r"(.+?)\s+"
            r"on\s+"
            r"(\d{2} \w{3}, \d{4} at \d{2}:\d{2}:\d{2})",
            body,
            re.DOTALL,
        )

        if transaction_match:
            amount = Decimal(transaction_match.group(1))
            card = transaction_match.group(2)
            merchant = transaction_match.group(3).strip()
            transaction_date = datetime.strptime(
                transaction_match.group(4),
                "%d %b, %Y at %H:%M:%S",
            )

            return Transaction(
                amount=amount,
                currency="INR",
                transaction_type="debit",
                merchant=merchant,
                transaction_date=transaction_date,
                account=f"HDFC CC {card}",
                source="Credit card",
                reference=None,
                email_id=email.gmail_id,
            )

        # Format 3:
        # You made a transaction of Rs. 322.00 at RAZ*Swiggy
        transaction_match = re.search(
            r"Credit Card ending in\s+"
            r"(\d+)\s*"
            r".*?"
            r"transaction of\s+"
            r"Rs\.\s*(\d+(?:\.\d+)?)\s+"
            r"at\s+"
            r"(.+?)\s+"
            r"on\s+"
            r"(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})",
            body,
            re.DOTALL,
        )

        if transaction_match:
            card = transaction_match.group(1)
            amount = Decimal(transaction_match.group(2))
            merchant = transaction_match.group(3).strip()
            transaction_date = datetime.strptime(
                transaction_match.group(4),
                "%d-%m-%Y %H:%M:%S",
            )

            return Transaction(
                amount=amount,
                currency="INR",
                transaction_type="debit",
                merchant=merchant,
                transaction_date=transaction_date,
                account=f"HDFC CC {card}",
                source="Credit card",
                reference=None,
                email_id=email.gmail_id,
            )

        return None


    def _parse_credit(self, email: Email) -> Transaction | None:
        body = email.body_text

        # UPI credit
        match = re.search(
            r"Rs\.(?P<amount>\d+(?:\.\d+)?)\s+"
            r"has been successfully credited to your HDFC Bank account "
            r"ending in\s+(?P<account>\S+).*?"
            r"Date:\s*(?P<date>\d{2}-\d{2}-\d{2}).*?"
            r"Sender:\s*(?P<sender>.+?).*?"
            r"UPI Reference No\.:\s*(?P<reference>\S+)",
            body,
            re.DOTALL,
        )

        if match:
            return Transaction(
                amount=Decimal(match.group("amount")),
                currency="INR",
                transaction_type="credit",
                merchant=match.group("sender").strip(),
                transaction_date=datetime.strptime(
                    match.group("date"),
                    "%d-%m-%y",
                ),
                account=f"HDFC {match.group('account')}",
                source="Credit",
                reference=match.group("reference"),
                email_id=email.gmail_id,
            )

        # NEFT credit
        match = re.search(
            r"Amount received:\s*INR\s*(?P<amount>[\d,]+(?:\.\d+)?).*?"
            r"Account:\s*(?P<account>\S+).*?"
            r"Date:\s*(?P<date>\d{2}-[A-Z]{3}-\d{4}).*?"
            r"Reference Details:\s*(?P<reference>\S+)",
            body,
            re.DOTALL,
        )

        if match:
            return Transaction(
                amount=Decimal(match.group("amount").replace(",", "")),
                currency="INR",
                transaction_type="credit",
                merchant="NEFT",
                transaction_date=datetime.strptime(
                    match.group("date"),
                    "%d-%b-%Y",
                ),
                account=f"HDFC {match.group('account')}",
                source="Credit",
                reference=match.group("reference"),
                email_id=email.gmail_id,
            )

        # UPI credit - VPA format
        match = re.search(
            r"Rs\.\s*(?P<amount>[\d,]+(?:\.\d+)?)\s+"
            r"is successfully credited to your account\s+"
            r"\*\*(?P<account>\S+)\s+"
            r"by VPA\s+(?P<vpa>\S+)\s+"
            r"(?:(?P<sender>.+?)\s+)?on\s+"
            r"(?P<date>\d{2}-\d{2}-\d{2})\.\s+"
            r"Your UPI transaction reference number is\s+"
            r"(?P<reference>\S+)",
            body,
            re.DOTALL,
        )

        if match:
            return Transaction(
                amount=Decimal(match.group("amount").replace(",", "")),
                currency="INR",
                transaction_type="credit",
                merchant=(match.group("sender") or match.group("vpa")).strip(),
                transaction_date=datetime.strptime(
                    match.group("date"),
                    "%d-%m-%y",
                ),
                account=f"HDFC {match.group('account')}",
                source="Credit",
                reference=match.group("reference"),
                email_id=email.gmail_id,
            )

        # Account alerts use INR and a transfer narration instead of VPA fields.
        match = re.search(
            r"Rs\.?\s*(?:INR\s*)?(?P<amount>\d[\d,]*(?:\.\d+)?)\s+"
            r"has been successfully (?:added|credited) to your account ending\s+"
            r"(?P<account>[Xx*\d]+)\s+(?:from|by)\s+"
            r"(?P<merchant>.+?)\s+on\s+(?P<date>\d{2}-[A-Za-z]{3}-\d{4})",
            " ".join(body.split()),
        )
        if match:
            return Transaction(
                amount=Decimal(match.group("amount").replace(",", "")),
                currency="INR",
                transaction_type="credit",
                merchant=match.group("merchant").strip(),
                transaction_date=datetime.strptime(match.group("date"), "%d-%b-%Y"),
                account=f"HDFC {match.group('account')}",
                source="Credit",
                reference=None,
                email_id=email.gmail_id,
            )

        return None
