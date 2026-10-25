from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal


Direction = Literal["IN", "OUT"]
EventSource = Literal["recurring", "invoice", "tax"]


@dataclass(frozen=True)
class ExternalInvoice:
    invoice_id: str
    direction: Direction
    amount: Decimal
    due_date: date
    counterparty_name: str
    purpose: str = ""
    status: str = "pending"


@dataclass(frozen=True)
class ExternalTaxObligation:
    tax_id: str
    amount: Decimal
    pay_date: date
    tax_name: str
    status: str = "pending"


@dataclass(frozen=True)
class CashflowEvent:
    event_id: str
    event_date: date
    direction: Direction
    amount: Decimal
    source_type: EventSource
    source_id: str
    counterparty_name: str = ""
    purpose: str = ""
    category: str = "other"
    status: str = "planned"
    confidence: float | None = None
    linked_event_id: str | None = None

    def signed_amount(self) -> Decimal:
        value = abs(self.amount)
        return value if self.direction == "IN" else -value
