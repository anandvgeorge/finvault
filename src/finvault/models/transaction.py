from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass
class Transaction:
    amount: Decimal
    currency: str
    transaction_type: Literal["debit", "credit"]
    merchant: str
    transaction_date: datetime
    account: str
    source: str
    reference: str | None
    email_id: str
    category: str | None = None
