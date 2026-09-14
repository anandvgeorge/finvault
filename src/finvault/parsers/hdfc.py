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
        transaction_date = datetime.strptime(
            transaction_match.group(3),
            "%d-%m-%y",
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
