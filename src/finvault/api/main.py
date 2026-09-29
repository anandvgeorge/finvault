from pathlib import Path
from fastapi.responses import FileResponse
from fastapi import FastAPI, Query

from finvault.database.database import Database

app = FastAPI()

database = Database()

@app.get("/")
def dashboard():
    return FileResponse(
        Path(__file__).parent / "static" / "index.html"
    )

@app.get("/overview")
def get_overview(
    period: str = Query("month")
):
    cursor = database.connection.execute(
        """
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

        WHERE
            CASE
                WHEN ? = 'month'
                    THEN transaction_date >= date('now', 'start of month')
                WHEN ? = 'year'
                    THEN transaction_date >= date('now', 'start of year')
                WHEN ? = 'all'
                    THEN 1
            END
        """,
        (period, period, period),
    )

    row = cursor.fetchone()

    return {
        "total_debits": row[0] or 0,
        "total_credits": row[1] or 0,
        "net_cashflow": row[2] or 0,
        "upi_debits": row[3] or 0,
        "credit_card_debits": row[4] or 0,
    }
