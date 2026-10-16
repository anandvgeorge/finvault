from pathlib import Path
from fastapi.responses import FileResponse
from fastapi import FastAPI

from finvault.api.filters import Months, Period, Years, transaction_date_filter
from finvault.database.database import Database

app = FastAPI()

database = Database()


@app.get("/")
def dashboard():
    return FileResponse(
        Path(__file__).parent / "static" / "index.html"
    )


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
