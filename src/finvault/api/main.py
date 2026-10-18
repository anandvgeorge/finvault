from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Annotated, Literal

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, HTTPException, Path as PathParameter, Response
from pydantic import BaseModel, ConfigDict, field_validator

from finvault.api.filters import Months, Period, Years, transaction_date_filter
from finvault.backfill_categories import apply_categories, calendar_month, preview_categories
from finvault.categories import CategoryConfigError, CategoryStore, TomlCategoryStore
from finvault.category_matching import KeywordCategoryMatcher
from finvault.database.database import Database
from finvault.models.category import Category, CategoryId, CategorySettings
from finvault.runtime import RuntimeInfo

app = FastAPI()
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

database = Database()
category_store: CategoryStore = TomlCategoryStore()
runtime_info = RuntimeInfo(Path(__file__).resolve().parents[3])


@app.get("/settings/status")
def get_system_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {**database.runtime_status(), "commit_id": runtime_info.commit_id}


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: CategoryId | None


def category_settings() -> CategorySettings:
    try:
        return category_store.load()
    except CategoryConfigError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/categories", response_model=list[Category])
def get_categories():
    """Public catalog for transaction dropdowns; never includes keyword rules."""
    return list(category_settings().categories)


@app.get("/settings/categories", response_model=CategorySettings)
def get_category_settings():
    return category_settings()


@app.put("/settings/categories", response_model=CategorySettings)
def save_category_settings(settings: CategorySettings):
    try:
        category_store.save(settings)
    except CategoryConfigError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return settings


class BackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str | None = None

    @field_validator("month")
    @classmethod
    def valid_month(cls, value):
        return calendar_month(value) if value is not None else None


@app.post("/settings/categories/backfill/{action}")
def backfill_category_settings(
    action: Literal["preview", "apply"], request: BackfillRequest, response: Response,
):
    """Run saved rules on this host's existing Uncategorized transactions."""
    response.headers["Cache-Control"] = "no-store"
    matcher = KeywordCategoryMatcher(category_settings())
    mode = "ro" if action == "preview" else "rw"
    uri = database.db_path.resolve().as_uri() + f"?mode={mode}"
    try:
        # A separate connection keeps a bulk update's transaction independent
        # of dashboard requests. URI modes also refuse a missing database.
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
            preview = preview_categories(connection, matcher, month=request.month)
            updated = apply_categories(connection, preview.proposals) if action == "apply" else None
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Could not read or update categories. Try again.") from None
    return {
        "month": request.month,
        "considered": preview.considered,
        "matched": len(preview.proposals),
        "unmatched": preview.unmatched,
        "missing_body": preview.missing_body,
        "unsupported_type": preview.unsupported_type,
        "updated": updated,
        "skipped": len(preview.proposals) - updated if updated is not None else 0,
    }


@app.get("/")
def dashboard():
    return FileResponse(
        Path(__file__).parent / "static" / "index.html"
    )


@app.get("/settings")
def settings_page():
    return FileResponse(Path(__file__).parent / "static" / "settings.html")


@app.get("/years")
def get_years() -> list[int]:
    """Return years represented by transactions, newest first."""
    cursor = database.connection.execute(
        """
        SELECT DISTINCT substr(transaction_date, 1, 4) AS year
        FROM transactions
        ORDER BY year DESC
        """
    )
    return [int(row[0]) for row in cursor.fetchall()]


@app.get("/overview")
def get_overview(
    period: Period = "all",
    years: Years = None,
    months: Months = None,
):
    """Return selected totals, or all-time totals when filters are omitted."""
    where, parameters = transaction_date_filter(period, years, months)
    cursor = database.connection.execute(
        f"""
        SELECT
            SUM(CASE WHEN transaction_type = 'debit'
                THEN CAST(amount AS REAL) ELSE 0 END) AS total_debits,

            SUM(CASE WHEN transaction_type = 'credit'
                THEN CAST(amount AS REAL) ELSE 0 END) AS total_credits,

            SUM(CASE
                WHEN transaction_type = 'credit'
                THEN CAST(amount AS REAL)
                WHEN transaction_type = 'debit'
                THEN -CAST(amount AS REAL)
                ELSE 0
            END) AS net_cashflow,

            SUM(CASE
                WHEN transaction_type = 'debit'
                AND source = 'UPI'
                THEN CAST(amount AS REAL) ELSE 0
            END) AS upi_debits,

            SUM(CASE
                WHEN transaction_type = 'debit'
                AND source = 'Credit card'
                THEN CAST(amount AS REAL) ELSE 0
            END) AS credit_card_debits

        FROM transactions

        WHERE {where}
        """,
        parameters,
    )

    row = cursor.fetchone()

    return {
        "total_debits": row[0] or 0,
        "total_credits": row[1] or 0,
        "net_cashflow": row[2] or 0,
        "upi_debits": row[3] or 0,
        "credit_card_debits": row[4] or 0,
    }


@app.get("/transactions")
def get_transactions(
    period: Period = "all",
    years: Years = None,
    months: Months = None,
):
    """Return rows for the same calendar selection accepted by /overview."""
    where, parameters = transaction_date_filter(period, years, months)
    cursor = database.connection.execute(
        f"""
        SELECT
            id,
            transaction_date,
            amount,
            currency,
            transaction_type,
            merchant,
            account,
            source,
            reference,
            category
        FROM transactions
        WHERE {where}
        ORDER BY transaction_date DESC
        """,
        parameters,
    )

    rows = cursor.fetchall()

    return [
        {
            "id": row[0],
            "transaction_date": row[1],
            "amount": row[2],
            "currency": row[3],
            "transaction_type": row[4],
            "merchant": row[5],
            "account": row[6],
            "source": row[7],
            "reference": row[8],
            "category": row[9],
        }
        for row in rows
    ]


@app.patch("/transactions/{transaction_id}/category")
def update_transaction_category(
    transaction_id: Annotated[int, PathParameter(gt=0)],
    update: CategoryUpdate,
):
    """Save a category for one transaction; null clears its category."""
    if update.category is not None and update.category not in {category.id for category in category_settings().categories}:
        raise HTTPException(status_code=422, detail="Unknown category")
    if not database.update_transaction_category(transaction_id, update.category):
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"id": transaction_id, "category": update.category}
