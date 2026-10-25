from datetime import datetime
from decimal import Decimal

from dto.AnalysisPackage import DirectionBlock, FinalAnalysisPackage, OperationDetail
from dto.OperationModels import OperationFilter, StoredOperation


class PackageBuilderService:
    def build(self, company_id: str, operations: list[StoredOperation],
              filters: OperationFilter) -> FinalAnalysisPackage:

        # 1. Фильтруем
        filtered_ops = self._apply_filters(operations, filters)

        # 2. Делим на два главных потока
        incomes_ops = [op for op in filtered_ops if op.direction == "Поступление"]
        outcomes_ops = [op for op in filtered_ops if op.direction == "Списание"]

        # 3. Собираем блоки
        incomes_block = self._build_block("Поступление", incomes_ops)
        outcomes_block = self._build_block("Списание", outcomes_ops)

        # 4. Период
        dates = [op.operation_date for op in filtered_ops]
        p_from = min(dates) if dates else datetime.now()
        p_to = max(dates) if dates else datetime.now()

        return FinalAnalysisPackage(
            company_id=company_id,
            period_from=p_from,
            period_to=p_to,
            incomes=incomes_block,
            outcomes=outcomes_block
        )

    def _apply_filters(self, operations: list[StoredOperation], filters: OperationFilter) -> list[StoredOperation]:
        """Фильтрует по дате, сумме, направлению, контрагенту и тексту назначения."""
        result = []
        for op in operations:
            # 1. Фильтр по направлению (если задан)
            if filters.direction and op.direction != filters.direction: continue

            # 2. Фильтр по дате
            if filters.date_from and op.operation_date < filters.date_from: continue
            if filters.date_to and op.operation_date > filters.date_to: continue

            # 3. Фильтр по сумме
            if filters.min_amount and op.amount < filters.min_amount: continue
            if filters.max_amount and op.amount > filters.max_amount: continue

            # 4. Фильтр по контрагенту (поиск подстроки, регистронезависимый)
            if filters.counterparty_name:
                if filters.counterparty_name.lower() not in op.counterparty_name.lower():
                    continue

            # 5. Фильтр по тексту назначения (поиск подстроки, регистронезависимый)
            if filters.purpose_contains:
                if filters.purpose_contains.lower() not in op.payment_purpose.lower():
                    continue

            result.append(op)
        return result

    def _build_block(self, name: str, operations: list[StoredOperation]) -> DirectionBlock:
        """Просто считает общую сумму и складывает операции в список."""
        total = Decimal("0.00")
        details = []

        for op in operations:
            total += op.amount
            details.append(
                OperationDetail(
                    operation_id=op.operation_id,
                    operation_date=op.operation_date,
                    amount=op.amount,
                    counterparty_name=op.counterparty_name,
                    payment_purpose=op.payment_purpose,
                    category=op.category,
                    vat_amount=op.vat_amount
                )
            )

        return DirectionBlock(
            direction_name=name,
            total_amount=total,
            operations=details
        )