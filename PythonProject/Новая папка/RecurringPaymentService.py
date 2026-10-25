from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any, Iterable

from dto.OperationModels import StoredOperation


def normalize_direction(value: Any) -> str:
    """Convert supported direction labels to the canonical IN/OUT values."""
    normalized = str(value or "").strip().casefold()
    mapping = {
        "in": "IN",
        "income": "IN",
        "поступление": "IN",
        "поступления": "IN",
        "out": "OUT",
        "expense": "OUT",
        "списание": "OUT",
        "списания": "OUT",
    }
    try:
        return mapping[normalized]
    except KeyError as exc:
        raise ValueError(f"Неизвестное направление операции: {value!r}") from exc


class RecurringPaymentService:
    """
    Эвристически выявляет повторяющиеся операции и строит прогноз их будущих дат.

    Обнаруженный шаблон — прогноз, а не подтверждение договорного обязательства.
    """

    @staticmethod
    def _as_date(value: date | datetime) -> date:
        return value.date() if isinstance(value, datetime) else value

    @staticmethod
    def _month_add(value: date, months: int, anchor_day: int | None = None) -> date:
        """Добавляет календарные месяцы, сохраняя исходный день-якорь."""
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
        if tolerance_days < 0:
            raise ValueError("tolerance_days должен быть >= 0")
        if amount_tolerance_pct < 0:
            raise ValueError("amount_tolerance_pct должен быть >= 0")
        if min_occurrences < 3:
            raise ValueError("min_occurrences должен быть >= 3")
        if not 0 <= min_interval_consistency <= 1:
            raise ValueError("min_interval_consistency должен быть в диапазоне [0, 1]")

        groups: dict[tuple[str, str, str], list[StoredOperation]] = {}
        for op in operations:
            try:
                direction = normalize_direction(op.direction)
            except ValueError:
                # Неизвестные направления не должны незаметно попадать в расчёты.
                continue

            counterparty = str(op.counterparty_name or "").strip()
            category = str(op.category or "other").strip()
            key = (counterparty.casefold(), direction, category.casefold())
            groups.setdefault(key, []).append(op)

        patterns: list[dict[str, Any]] = []

        for (_, direction, _), group in groups.items():
            group = sorted(group, key=lambda op: cls._as_date(op.operation_date))
            if len(group) < min_occurrences:
                continue

            # Объединяем операции одной группы за один день в одну дневную сумму.
            daily: dict[date, dict[str, Any]] = {}
            for op in group:
                op_date = cls._as_date(op.operation_date)
                bucket = daily.setdefault(
                    op_date,
                    {"amount": Decimal("0"), "operations": []},
                )
                bucket["amount"] += cls._operation_amount(op)
                bucket["operations"].append(op)

            dated = sorted(daily.items(), key=lambda item: item[0])
            if len(dated) < min_occurrences:
                continue

            dates = [item[0] for item in dated]
            amounts = [item[1]["amount"] for item in dated]
            intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            if not intervals or any(days <= 0 for days in intervals):
                continue

            median_interval = float(median(intervals))
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

            consistent = sum(
                abs(interval - median_interval) <= tolerance_days
                for interval in intervals
            ) / len(intervals)
            if consistent < min_interval_consistency:
                continue

            sorted_amounts = sorted(amounts)
            n = len(sorted_amounts)
            if n % 2:
                median_amount = sorted_amounts[n // 2]
            else:
                median_amount = (
                    sorted_amounts[n // 2 - 1] + sorted_amounts[n // 2]
                ) / Decimal("2")

            if median_amount <= 0:
                continue

            deviations = [
                float(abs(amount - median_amount) / median_amount)
                for amount in amounts
            ]
            max_deviation = max(deviations)
            if max_deviation > amount_tolerance_pct:
                continue

            avg_amount = sum(amounts, Decimal("0")) / Decimal(len(amounts))
            latest_day_ops: list[StoredOperation] = dated[-1][1]["operations"]
            latest_op = latest_day_ops[-1]
            confidence = min(
                0.99,
                0.50
                + 0.25 * consistent
                + 0.15 * max(0.0, 1.0 - max_deviation)
                + min(0.09, 0.02 * (len(dated) - 3)),
            )
            counterparty_name = str(latest_op.counterparty_name or "").strip()
            category_name = str(latest_op.category or "other").strip()
            purpose = str(latest_op.payment_purpose or "").strip()

            patterns.append({
                "pattern_id": (
                    f"{direction}:{category_name.casefold()}:"
                    f"{counterparty_name.casefold()}"
                ),
                "direction": direction,
                "category": category_name,
                "counterparty_name": counterparty_name,
                "payment_purpose": purpose,
                "average_amount": avg_amount.quantize(Decimal("0.01")),
                "median_amount": median_amount.quantize(Decimal("0.01")),
                "estimated_interval_days": expected_interval,
                "median_interval_days": round(median_interval, 2),
                "cadence": cadence,
                "last_occurrence": dates[-1],
                "occurrences": len(dated),
                "interval_consistency": round(consistent, 3),
                "amount_deviation_pct": round(max_deviation * 100, 2),
                "confidence": round(confidence, 3),
            })

        return sorted(
            patterns,
            key=lambda p: (
                p["direction"],
                p["counterparty_name"].casefold(),
                p["category"].casefold(),
            ),
        )

    @classmethod
    def generate_future_obligations(
        cls,
        recurring_patterns: list[dict[str, Any]],
        last_date: datetime | date,
        horizon_days: int = 30,
    ) -> list[dict[str, Any]]:
        if horizon_days < 0:
            raise ValueError("horizon_days должен быть >= 0")

        start = cls._as_date(last_date)
        end = start + timedelta(days=horizon_days)
        events: list[dict[str, Any]] = []

        for pattern in recurring_patterns:
            last_occurrence = cls._as_date(pattern["last_occurrence"])
            cadence = str(pattern.get("cadence", "interval"))
            interval = int(pattern.get("estimated_interval_days", 0))
            if interval <= 0:
                continue

            anchor_day = last_occurrence.day
            if cadence == "monthly":
                next_date = cls._month_add(last_occurrence, 1, anchor_day)
            elif cadence == "quarterly":
                next_date = cls._month_add(last_occurrence, 3, anchor_day)
            else:
                next_date = last_occurrence + timedelta(days=interval)

            # Пропускаем даты до начала прогноза, сохраняя календарный ритм.
            while next_date <= start:
                if cadence == "monthly":
                    next_date = cls._month_add(next_date, 1, anchor_day)
                elif cadence == "quarterly":
                    next_date = cls._month_add(next_date, 3, anchor_day)
                else:
                    next_date += timedelta(days=interval)

            while next_date <= end:
                events.append({
                    "event_id": (
                        f"recurring:{pattern['pattern_id']}:{next_date.isoformat()}"
                    ),
                    "date": next_date,
                    "amount": abs(Decimal(str(pattern["average_amount"]))).quantize(
                        Decimal("0.01")
                    ),
                    "direction": normalize_direction(pattern["direction"]),
                    "category": pattern.get("category", "other"),
                    "counterparty_name": pattern.get("counterparty_name", ""),
                    "description": (
                        f"Прогноз регулярного платежа: "
                        f"{pattern.get('counterparty_name', '')} "
                        f"({pattern.get('category', 'other')})"
                    ),
                    "confidence": float(pattern.get("confidence", 0.5)),
                    "pattern_id": pattern["pattern_id"],
                    "status": "forecast",
                })

                if cadence == "monthly":
                    next_date = cls._month_add(next_date, 1, anchor_day)
                elif cadence == "quarterly":
                    next_date = cls._month_add(next_date, 3, anchor_day)
                else:
                    next_date += timedelta(days=interval)

        return sorted(events, key=lambda event: (event["date"], event["direction"]))
