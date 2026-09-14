import logging
from pathlib import Path


def setup_logging():
    log_path = Path("data/finvault.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )
    
    logging.getLogger("googleapiclient.discovery_cache").setLevel(
        logging.WARNING
    )

