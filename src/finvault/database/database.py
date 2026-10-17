import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from finvault.models.email import Email


class Database:
    def __init__(self, db_path: str = "data/finvault.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
        )
        self._create_tables()

    def _create_tables(self):
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmail_id TEXT NOT NULL UNIQUE,
                sender TEXT NOT NULL,
                subject TEXT NOT NULL,
                received_at TEXT NOT NULL,
                body_html TEXT,
                body_text TEXT,
                label TEXT NOT NULL
            )
            """
        )
        
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                merchant TEXT NOT NULL,
                transaction_date TEXT NOT NULL,
                account TEXT NOT NULL,
                source TEXT NOT NULL,
                reference TEXT,
                email_id TEXT NOT NULL UNIQUE,
                category TEXT,
                FOREIGN KEY (email_id) REFERENCES emails(gmail_id)
            )
            """
        )

        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.connection.commit()

    def record_ingestion_completed(self):
        """Record completed runs, including those that find no new emails."""
        self.connection.execute(
            "INSERT INTO app_metadata (key, value) VALUES ('last_ingestion_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (datetime.now(timezone.utc).isoformat(),),
        )
        self.connection.commit()

    def runtime_status(self) -> dict[str, str | None]:
        """Read the three status values together, including absent values."""
        keys = ("last_ingestion_at", "last_deploy_check_at", "last_deploy_check_status")
        rows = dict(self.connection.execute(
            "SELECT key, value FROM app_metadata WHERE key IN (?, ?, ?)", keys,
        ).fetchall())
        return {key: rows.get(key) for key in keys}

    def save_email(self, email: Email, label: str):
        self.connection.execute(
            """
            INSERT OR IGNORE INTO emails (
                gmail_id,
                sender,
                subject,
                received_at,
                body_html,
                body_text,
                label
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email.gmail_id,
                email.sender,
                email.subject,
                email.received_at.isoformat(),
                email.body_html,
                email.body_text,
                label,
            ),
        )

        self.connection.commit()
        
    def email_exists(self, gmail_id: str) -> bool:
        cursor = self.connection.execute(
            "SELECT 1 FROM emails WHERE gmail_id = ? LIMIT 1",
            (gmail_id,),
        )
        return cursor.fetchone() is not None
    
    def get_emails(self, *, unprocessed_only: bool = False):
        """Optionally select only emails without a saved transaction."""
        query = """
            SELECT e.gmail_id, e.sender, e.subject, e.received_at,
                e.body_html, e.body_text, e.label
            FROM emails e
        """
        if unprocessed_only:
            query += """
                WHERE NOT EXISTS (
                    SELECT 1 FROM transactions t WHERE t.email_id = e.gmail_id
                )
            """
        query += " ORDER BY e.id"
        cursor = self.connection.execute(query)

        return [
            (
                Email(
                    gmail_id=row[0],
                    sender=row[1],
                    subject=row[2],
                    received_at=datetime.fromisoformat(row[3]),
                    body_html=row[4] or "",
                    body_text=row[5] or "",
                ),
                row[6],
            )
            for row in cursor.fetchall()
        ]
        
    def save_transaction(self, transaction):
        self.connection.execute(
            """
            INSERT OR IGNORE INTO transactions (
                amount,
                currency,
                transaction_type,
                merchant,
                transaction_date,
                account,
                source,
                reference,
                email_id,
                category
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(transaction.amount),
                transaction.currency,
                transaction.transaction_type,
                transaction.merchant,
                transaction.transaction_date.isoformat(),
                transaction.account,
                transaction.source,
                transaction.reference,
                transaction.email_id,
                transaction.category,
            ),
        )
        self.connection.commit()

    def update_transaction_category(self, transaction_id: int, category: str | None) -> bool:
        """Persist a category, returning whether the transaction exists."""
        cursor = self.connection.execute(
            "UPDATE transactions SET category = ? WHERE id = ?",
            (category, transaction_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def close(self):
        self.connection.close()
