from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class Transaction:
    amount: Decimal
    currency: str
    transaction_type: str
    merchant: str
    transaction_date: datetime
    account: str
    source: str
    reference: str | None
    email_id: str
