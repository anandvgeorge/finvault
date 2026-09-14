import logging

from finvault.database.database import Database
from finvault.gmail.client import GmailClient
from finvault.ingestion.email_ingestor import EmailIngestor
from finvault.logging_config import setup_logging


def main():
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("Starting FinVault ingestion")

    gmail = GmailClient()
    database = Database()

    ingestor = EmailIngestor(gmail, database)

    ingestor.ingest_label("UPI")
    ingestor.ingest_label("Credit card")

    database.close()

    logger.info("FinVault ingestion completed")
    logger.info("-------------------------------")


if __name__ == "__main__":
    main()