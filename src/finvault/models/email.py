from dataclasses import dataclass
from datetime import datetime


@dataclass
class Email:
    gmail_id: str
    sender: str
    subject: str
    received_at: datetime
    body_html: str
    body_text: str