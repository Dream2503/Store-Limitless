"""Public upload-manager interface for the shared Redis FIFO upload queue.

Every ingestion source must use this interface so only one file is uploaded at a
time across processes. Provider-level chunk parallelism remains configured by
each DataCenter implementation.
"""

from core.utils.ingestion_queue import (
    IngestionUploadTicket,
    cancel_ingestion_upload,
    enqueue_ingestion_upload,
    ingestion_upload_slot,
)

__all__ = [
    "IngestionUploadTicket",
    "cancel_ingestion_upload",
    "enqueue_ingestion_upload",
    "ingestion_upload_slot",
]
