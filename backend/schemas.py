from pydantic import BaseModel
from typing import Optional


class RecoveryItem(BaseModel):
    email_id: str
    subject: str
    sender: str
    date: str
    category: str
    priority: str
    summary: str
    action_required: bool
    action: Optional[str] = None
    deadline: Optional[str] = None
    confidence: str = "medium"


class RecoveryReport(BaseModel):
    job_id: str
    filename: str
    emails_processed: int
    threads_found: int
    action_count: int
    high_priority_count: int
    items: list[RecoveryItem]
    warnings: list[str] = []
