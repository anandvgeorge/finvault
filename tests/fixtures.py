"""Made-up records for isolated tests, with reserved email domains and dummy IDs."""

from datetime import datetime, timezone
from decimal import Decimal

from finvault.models.email import Email
from finvault.models.transaction import Transaction


def email(message_id="fixture-message", body="Synthetic transaction email"):
    return Email(
        gmail_id=message_id,
        sender="alerts@example.invalid",
        subject="Synthetic account update",
        received_at=datetime(2001, 1, 2, 12, 0, tzinfo=timezone.utc),
        body_html="",
        body_text=body,
    )


def transaction(message_id="fixture-message", **overrides):
    values = dict(
        amount=Decimal("42.50"), currency="INR", transaction_type="debit",
        merchant="Example Merchant", transaction_date=email().received_at,
        account="HDFC XX0000", source="UPI", reference="FIXTURE-REFERENCE",
        email_id=message_id,
    )
    values.update(overrides)
    return Transaction(**values)
