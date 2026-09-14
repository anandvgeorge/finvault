from finvault.database.database import Database
from finvault.gmail.client import GmailClient
from finvault.ingestion.email_ingestor import EmailIngestor


def main():
    gmail = GmailClient()
    database = Database()

    ingestor = EmailIngestor(gmail, database)

    ingestor.ingest_label("UPI")
    ingestor.ingest_label("Credit card")

    database.close()


if __name__ == "__main__":
    main()
