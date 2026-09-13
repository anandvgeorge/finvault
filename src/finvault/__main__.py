from finvault.gmail.client import GmailClient
from finvault.ingestion.email_ingestor import EmailIngestor


def main():
    gmail = GmailClient()
    ingestor = EmailIngestor(gmail)

    ingestor.ingest_label("UPI")
    ingestor.ingest_label("Credit card")


if __name__ == "__main__":
    main()