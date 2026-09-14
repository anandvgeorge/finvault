import sqlite3
from pathlib import Path

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

    def close(self):
        self.connection.close()
