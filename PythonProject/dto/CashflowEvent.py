
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class CashflowDirection(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"


class CashflowSource(str, Enum):
    HISTORY = "history"
    RECURRING = "recurring"
    INVOICE = "invoice"
    TAX = "tax"


class CashflowStatus(str, Enum):
    PLANNED = "planned"
    CONFIRMED = "confirmed"
    PAID = "paid"
    CANCELLED = "cancelled"


@dataclass
class CashflowEvent:
    event_id: str
    direction: str
    amount: Decimal
    currency: str
    due_date: datetime
    source: str
    status: str
    counterparty: str = ""
    description: str = ""
    external_id: str | None = None
    confidence: float | None = None
    linked_operation_id: str | None = None
    company_id: str | None = None