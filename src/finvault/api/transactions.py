"""Input validation for transactions entered directly by the user."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from finvault.models.category import CategoryId


IST = ZoneInfo("Asia/Kolkata")
Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=200)]
RequiredText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]


class ManualTransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    transaction_type: Literal["debit", "credit"]
    merchant: RequiredText
    transaction_date: datetime
    currency: Literal["INR"] = "INR"
    source: RequiredText = "Cash"
    account: Text = ""
    reference: Text | None = None
    category: CategoryId | None = None

    @field_validator("amount")
    @classmethod
    def currency_precision(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    @field_validator("transaction_date", mode="before")
    @classmethod
    def require_date_and_time(cls, value):
        if not isinstance(value, (str, datetime)) or isinstance(value, str) and ":" not in value:
            raise ValueError("Provide a date and time in ISO 8601 format")
        return value

    @field_validator("transaction_date")
    @classmethod
    def indian_local_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=IST)
        try:
            return value.astimezone(IST)
        except OverflowError:
            raise ValueError("Date is outside the supported range") from None

    @field_validator("account", mode="before")
    @classmethod
    def optional_account(cls, value):
        return "" if value is None else value

    @field_validator("reference")
    @classmethod
    def optional_reference(cls, value):
        return value or None
