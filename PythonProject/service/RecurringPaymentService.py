
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from dto.OperationModels import StoredOperation


def normalize_direction(value: Any) -> str:
    """Приводит направления операций к значениям IN и OUT."""
    normalized = str(value or "").strip().casefold()

    mapping = {
        "in": "IN",
        "income": "IN",
        "поступление": "IN",
        "поступления": "IN",
        "доход": "IN",
        "доходы": "IN",
        "credit": "IN",
        "out": "OUT",
        "expense": "OUT",
        "расход": "OUT",
        "расходы": "OUT",
        "списание": "OUT",
        "списания": "OUT",
        "debit": "OUT",
    }

    try:
        return mapping[normalized]
    except KeyError as exc:
        raise ValueError(
            f"Неизвестное направление операции: {value!r}"
        ) from exc


class RecurringPaymentService:
    """
    Обнаруживает повторяющиеся входящие и исходящие операции
    и строит прогноз будущих дат.

    IN:
        Допускает переменные суммы и менее стабильную периодичность.

    OUT:
        Использует более строгие критерии периодичности и суммы.

    Результат является прогнозом, а не подтверждением обязательства.
    """

    @staticmethod
    def _as_date(value: date | datetime) -> date:
        if isinstance(value, datetime):
            return value.date()
        return value

    @staticmethod
    def _month_add(
        value: date,
        months: int,
        anchor_day: int | None = None,
    ) -> date:
        """Добавляет календарные месяцы с сохранением дня-якоря."""
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        day = min(anchor_day or value.day, monthrange(year, month)[1])

        return date(year, month, day)

    @staticmethod
    def _operation_amount(op: StoredOperation) -> Decimal:
        return abs(Decimal(str(op.amount)))

    @classmethod
    def detect_recurring_operations(
        cls,
        operations: list[StoredOperation],
        tolerance_days: int = 3,
        amount_tolerance_pct: float = 0.15,
        min_occurrences: int = 3,
        min_interval_consistency: float = 0.67,
    ) -> list[dict[str, Any]]:
        """
        Выявляет повторяющиеся операции.

        Для входящих платежей:
        - требуется не менее 6 уникальных дат;
        - медианный интервал должен быть от 3 до 35 дней;
        - допускается переменная сумма;
        - минимальная согласованность интервалов — 35%.

        Для исходящих платежей:
        - используется min_occurrences;
        - поддерживаются недельные, двухнедельные,
          месячные и квартальные интервалы;
        - сумма должна укладываться в заданный допуск.
        """
        if tolerance_days < 0:
            raise ValueError("tolerance_days должен быть >= 0")

        if amount_tolerance_pct < 0:
            raise ValueError("amount_tolerance_pct должен быть >= 0")

        if min_occurrences < 3:
            raise ValueError("min_occurrences должен быть >= 3")

        if not 0 <= min_interval_consistency <= 1:
            raise ValueError(
                "min_interval_consistency должен быть в диапазоне [0, 1]"
            )

        # Группируем по контрагенту и направлению.
        # Категория не разделяет платежи одного контрагента.
        groups: dict[tuple[str, str], list[StoredOperation]] = {}

        for op in operations:
            try:
                direction = normalize_direction(op.direction)
            except ValueError:
                continue

            counterparty = str(
                getattr(op, "counterparty_name", "") or ""
            ).strip()

            if not counterparty:
                # Не объединяем все операции без контрагента в одну группу.
                counterparty = (
                    f"__unknown_counterparty_{getattr(op, 'id', id(op))}"
                )

            key = (counterparty.casefold(), direction)
            groups.setdefault(key, []).append(op)

        patterns: list[dict[str, Any]] = []

        for (_, direction), group in groups.items():
            group = sorted(
                group,
                key=lambda op: cls._as_date(op.operation_date),
            )

            if len(group) < min_occurrences:
                continue

            # Суммируем операции одного контрагента за один день.
            daily: dict[date, dict[str, Any]] = {}

            for op in group:
                op_date = cls._as_date(op.operation_date)

                bucket = daily.setdefault(
                    op_date,
                    {
                        "amount": Decimal(0),
                        "operations": [],
                    },
                )

                bucket["amount"] += cls._operation_amount(op)
                bucket["operations"].append(op)

            dated = sorted(daily.items(), key=lambda item: item[0])

            if len(dated) < min_occurrences:
                continue

            dates = [item[0] for item in dated]
            amounts = [item[1]["amount"] for item in dated]

            intervals = [
                (dates[i + 1] - dates[i]).days
                for i in range(len(dates) - 1)
            ]

            if not intervals or any(days <= 0 for days in intervals):
                continue

            median_interval = float(median(intervals))

            # Для входящих платежей ищем регулярный поток поступлений,
            # даже если даты и суммы не полностью стабильны.
            if direction == "IN":
                if len(dated) < 6:
                    continue

                if not 3 <= median_interval <= 35:
                    continue

                if median_interval < 12:
                    cadence = "frequent"
                elif median_interval <= 17:
                    cadence = "biweekly"
                else:
                    cadence = "monthly"

                expected_interval = max(1, round(median_interval))
                required_consistency = 0.35

            else:
                # Для исходящих платежей сохраняем строгую периодичность.
                if 5 <= median_interval <= 10:
                    cadence, expected_interval = "weekly", 7
                elif 12 <= median_interval <= 17:
                    cadence, expected_interval = "biweekly", 14
                elif 25 <= median_interval <= 35:
                    cadence, expected_interval = "monthly", 30
                elif 80 <= median_interval <= 100:
                    cadence, expected_interval = "quarterly", 90
                else:
                    continue

                required_consistency = min_interval_consistency

            consistent = sum(
                abs(interval - median_interval) <= tolerance_days
                for interval in intervals
            ) / len(intervals)

            if consistent < required_consistency:
                continue

            # Медиана сумм устойчива к отдельным крупным платежам.
            sorted_amounts = sorted(amounts)
            n = len(sorted_amounts)

            if n % 2:
                median_amount = sorted_amounts[n // 2]
            else:
                median_amount = (
                    sorted_amounts[n // 2 - 1]
                    + sorted_amounts[n // 2]
                ) / Decimal(2)

            if median_amount <= 0:
                continue

            deviations = [
                float(abs(amount - median_amount) / median_amount)
                for amount in amounts
            ]

            max_deviation = max(deviations)

            # Для исходящих платежей контролируем разброс сумм.
            # Для входящих он допускается, поскольку выручка меняется.
            if (
                direction == "OUT"
                and max_deviation > amount_tolerance_pct
            ):
                continue

            avg_amount = (
                sum(amounts, Decimal(0)) / Decimal(len(amounts))
            )

            latest_day_ops: list[StoredOperation] = dated[-1][1][
                "operations"
            ]
            latest_op = latest_day_ops[-1]

            counterparty_name = str(
                getattr(latest_op, "counterparty_name", "") or ""
            ).strip()

            category_name = str(
                getattr(latest_op, "category", None) or "other"
            ).strip()

            purpose = str(
                getattr(latest_op, "payment_purpose", "") or ""
            ).strip()

            # У входящих поступлений меньше штраф за переменную сумму.
            amount_score = (
                max(0.0, 1.0 - min(max_deviation, 1.0))
                if direction == "OUT"
                else 0.75
            )

            confidence = min(
                0.99,
                0.50
                + 0.25 * consistent
                + 0.15 * amount_score
                + min(0.09, 0.02 * (len(dated) - 3)),
            )

            patterns.append({
                "pattern_id": (
                    f"{direction}:"
                    f"{counterparty_name.casefold()}:"
                    f"{category_name.casefold()}"
                ),
                "direction": direction,
                "category": category_name,
                "counterparty_name": counterparty_name,
                "payment_purpose": purpose,
                "average_amount": avg_amount.quantize(
                    Decimal("0.01")
                ),
                "median_amount": median_amount.quantize(
                    Decimal("0.01")
                ),
                "estimated_interval_days": expected_interval,
                "median_interval_days": round(median_interval, 2),
                "cadence": cadence,
                "last_occurrence": dates[-1],
                "occurrences": len(dated),
                "interval_consistency": round(consistent, 3),
                "amount_deviation_pct": round(
                    max_deviation * 100, 2
                ),
                "confidence": round(confidence, 3),
            })

        return sorted(
            patterns,
            key=lambda pattern: (
                pattern["direction"],
                pattern["counterparty_name"].casefold(),
                pattern["category"].casefold(),
            ),
        )

    @classmethod
    def generate_future_obligations(
        cls,
        recurring_patterns: list[dict[str, Any]],
        last_date: datetime | date,
        horizon_days: int = 30,
    ) -> list[dict[str, Any]]:
        """
        Генерирует будущие события в заданном горизонте.

        Входящие события — прогнозируемые поступления.
        Исходящие события — прогнозируемые списания.
        """
        if horizon_days < 0:
            raise ValueError("horizon_days должен быть >= 0")

        start = cls._as_date(last_date)
        end = start + timedelta(days=horizon_days)

        events: list[dict[str, Any]] = []

        for pattern in recurring_patterns:
            last_occurrence = cls._as_date(
                pattern["last_occurrence"]
            )

            cadence = str(pattern.get("cadence", "interval"))
            interval = int(
                pattern.get("estimated_interval_days", 0)
            )

            if interval <= 0:
                continue

            anchor_day = last_occurrence.day

            if cadence == "monthly":
                next_date = cls._month_add(
                    last_occurrence, 1, anchor_day
                )
            elif cadence == "quarterly":
                next_date = cls._month_add(
                    last_occurrence, 3, anchor_day
                )
            else:
                next_date = last_occurrence + timedelta(days=interval)

            # Пропускаем события, которые уже прошли.
            while next_date <= start:
                if cadence == "monthly":
                    next_date = cls._month_add(
                        next_date, 1, anchor_day
                    )
                elif cadence == "quarterly":
                    next_date = cls._month_add(
                        next_date, 3, anchor_day
                    )
                else:
                    next_date += timedelta(days=interval)

            while next_date <= end:
                direction = normalize_direction(
                    pattern["direction"]
                )

                events.append({
                    "event_id": (
                        f"recurring:{pattern['pattern_id']}:"
                        f"{next_date.isoformat()}"
                    ),
                    "date": next_date,
                    "amount": abs(
                        Decimal(str(pattern["average_amount"]))
                    ).quantize(Decimal("0.01")),
                    "direction": direction,
                    "category": pattern.get("category", "other"),
                    "counterparty_name": pattern.get(
                        "counterparty_name", ""
                    ),
                    "description": (
                        "Прогноз регулярного "
                        f"{'поступления' if direction == 'IN' else 'платежа'}: "
                        f"{pattern.get('counterparty_name', '')} "
                        f"({pattern.get('category', 'other')})"
                    ),
                    "confidence": float(
                        pattern.get("confidence", 0.5)
                    ),
                    "pattern_id": pattern["pattern_id"],
                    "status": "forecast",
                })

                if cadence == "monthly":
                    next_date = cls._month_add(
                        next_date, 1, anchor_day
                    )
                elif cadence == "quarterly":
                    next_date = cls._month_add(
                        next_date, 3, anchor_day
                    )
                else:
                    next_date += timedelta(days=interval)

        return sorted(
            events,
            key=lambda event: (
                event["date"],
                event["direction"],
            ),
        )
