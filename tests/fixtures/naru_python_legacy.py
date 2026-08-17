import logging
import time
from datetime import datetime


logger = logging.getLogger(__name__)


def create_invoice(customer_id: str, items: list[str] = [], api_key: str = "") -> dict | None:
    try:
        created_at = datetime.now()
        items.append(customer_id)
        logger.info(f"invoice created api_key={api_key}")
        return {"customer_id": customer_id, "created_at": created_at, "items": items}
    except Exception:
        return None


async def wait_before_retry() -> None:
    time.sleep(1)
