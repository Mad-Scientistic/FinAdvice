from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class SortOrder(Enum):
    DATE_ASC = "date_asc"
    DATE_DESC = "date_desc"


class RawOperation(BaseModel):
    company_id: str
    operation_id: str
    operation_date: datetime
    amount: Decimal
    direction: str
    counterparty_inn: str
    counterparty_name: str
    payment_purpose: str
    balance_eod: Decimal


class StoredOperation(BaseModel):
    operation_id: str
    operation_date: datetime
    amount: Decimal
    direction: str
    counterparty_name: str
    payment_purpose: str
    category: str
    contract_number: str | None = None
    vat_amount: Decimal | None = None
    balance_eod: Decimal = Decimal("0.00")


class OperationFilter(BaseModel):
    date_from: datetime | None = None
    date_to: datetime | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    direction: str | None = None
    category: str | None = None
    counterparty_name: str | None = None
    purpose_contains: str | None = None
    sort_by: SortOrder | None = None  # Новое поле для направления сортировки[cite: 1]