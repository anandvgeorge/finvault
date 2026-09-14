import sqlite3
from pathlib import Path
from datetime import datetime

from finvault.models.email import Email


class Database:
    def __init__(self, db_path: str = "data/finvault.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(self.db_path)
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
                FOREIGN KEY (email_id) REFERENCES emails(gmail_id)
            )
            """
        )

        self.connection.commit()

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
    
    def get_emails(self):
        cursor = self.connection.execute(
            """
            SELECT gmail_id, sender, subject, received_at,
                body_html, body_text, label
            FROM emails
            ORDER BY id
            """
        )

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
                email_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        self.connection.commit()

    def close(self):
        self.connection.close()
