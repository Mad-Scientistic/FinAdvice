from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


# Одна операция
class OperationDetail(BaseModel):
    operation_id: str
    operation_date: datetime
    amount: Decimal
    counterparty_name: str
    payment_purpose: str
    category: str
    vat_amount: Decimal | None = None

# Блок "Списание" или "Поступление"
class DirectionBlock(BaseModel):
    direction_name: str             # "Списание" или "Поступление"
    total_amount: Decimal
    operations: list[OperationDetail] # Просто список операций, без категорий

# Итоговый пакет для отправки
class FinalAnalysisPackage(BaseModel):
    company_id: str
    period_from: datetime
    period_to: datetime
    incomes: DirectionBlock         # Блок поступлений
    outcomes: DirectionBlock        # Блок списаний

# разбивка на категории