from decimal import Decimal

from dto.OperationModels import RawOperation  # <-- Новый импорт


class ValidatorService:
    def validate_operations(self, operations: list[RawOperation]) -> tuple[list[RawOperation], list[str]]:
        """
        Проверяет список операций.
        Возвращает: (Список валидных операций, Список ошибок)
        """
        valid_ops = []
        errors = []

        for op in operations:
            # 1. Проверка суммы (должна быть больше нуля)
            if op.amount <= Decimal("0.00"):
                errors.append(f"Операция {op.operation_id}: Сумма должна быть > 0")
                continue

            # 2. Проверка направления
            if op.direction not in ["Поступление", "Списание"]:
                errors.append(f"Операция {op.operation_id}: Неизвестное направление '{op.direction}'")
                continue

            # Если все проверки пройдены — добавляем в валидные
            valid_ops.append(op)

        return valid_ops, errors