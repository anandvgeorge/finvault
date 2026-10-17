import logging

from finvault.categories import CategoryConfigError, TomlCategoryStore
from finvault.category_matching import KeywordCategoryMatcher
from finvault.database.database import Database
from finvault.gmail.client import GmailClient
from finvault.ingestion.email_ingestor import EmailIngestor
from finvault.logging_config import setup_logging
from finvault.processing.transaction_processor import TransactionProcessor


def main():
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("Starting FinVault ingestion")

    try:
        matcher = KeywordCategoryMatcher(TomlCategoryStore().load())
    except CategoryConfigError as error:
        logger.error("%s", error)
        raise SystemExit(1) from None

    gmail = GmailClient()
    database = Database()

    try:
        ingestor = EmailIngestor(gmail, database)

        ingestor.ingest_label("UPI")
        ingestor.ingest_label("Credit card")
        ingestor.ingest_label("Credit")
        ingestor.ingest_label("Debit card")

        processor = TransactionProcessor(database, matcher)
        processor.process()
        database.record_ingestion_completed()
    finally:
        database.close()

    logger.info("FinVault ingestion completed")
    logger.info("-------------------------------")


if __name__ == "__main__":
    main()
