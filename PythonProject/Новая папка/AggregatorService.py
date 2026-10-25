from collections import defaultdict
from decimal import Decimal

from dto.OperationModels import StoredOperation


class AggregatorService:
    def __init__(self):
        pass

    def aggregate_by_category(self, operations: list[StoredOperation]) -> dict[str, dict[str, Decimal]]:
        """
        Считает общую сумму поступлений и списаний для каждой категории.
        Возвращает словарь вида: {'salary': {'income': 0, 'outcome': 1500000}, ...}
        """
        stats = defaultdict(lambda: {"income": Decimal("0.00"), "outcome": Decimal("0.00")})

        for op in operations:
            cat = op.category
            amount = op.amount

            if op.direction == "Поступление":
                stats[cat]["income"] += amount
            elif op.direction == "Списание":
                stats[cat]["outcome"] += amount

        return dict(stats)

    def get_monthly_cashflow(self, operations: list[StoredOperation]) -> dict[str, dict[str, Decimal]]:
        """
        Считает общий приход и расход по месяцам (формат YYYY-MM).
        Это база для прогнозирования кассовых разрывов.
        """
        monthly = defaultdict(lambda: {"income": Decimal("0.00"), "outcome": Decimal("0.00")})

        for op in operations:
            # Форматируем дату в строку '2024-10'
            month_key = op.operation_date.strftime("%Y-%m")
            amount = op.amount

            if op.direction == "Поступление":
                monthly[month_key]["income"] += amount
            elif op.direction == "Списание":
                monthly[month_key]["outcome"] += amount

        # Сортируем по месяцам для красоты
        return dict(sorted(monthly.items()))

    def get_average_salary(self, operations: list[StoredOperation]) -> Decimal:
        """Считает среднюю сумму выплаты зарплаты."""
        salaries = [op.amount for op in operations if op.category == "salary"]
        if not salaries:
            return Decimal("0.00")
        return sum(salaries) / len(salaries)