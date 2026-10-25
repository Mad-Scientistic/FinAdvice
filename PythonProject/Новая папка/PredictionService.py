
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import numpy as np
import pandas as pd
from dto.OperationModels import StoredOperation
from service.PaymentAnalyzer import PaymentAnalyzer
from service.RecurringPaymentService import RecurringPaymentService


@dataclass
class ExternalInvoice:
    direction: Literal["IN", "OUT"]
    amount: Decimal
    due_date: date
    counterparty_name: str
    purpose: str


@dataclass
class ExternalTaxObligation:
    amount: Decimal
    pay_date: date
    tax_name: str


class PredictionService:
    """
    Прогноз остатка на основе исторических поступлений и расходов.

    Основные принципы:
    - ежедневные временные ряды IN и OUT;
    - выбор модели по rolling-origin WAPE;
    - модели naive, seasonal_naive и trend;
    - осторожное обнаружение смены режима;
    - отделение распознанных регулярных операций от нерегулярного потока;
    - отдельное добавление известных счетов, налогов и регулярных платежей;
    - накопительный интервал неопределённости, растущий с горизонтом;
    - сохранение формата результата для существующего интерфейса.

    Важно:
    - точность прогноза зависит от качества исторических остатков;
    - автоматическое обнаружение регулярных платежей может ошибаться;
    - это модель планирования денежных потоков, а не гарантия будущего остатка.
    """

    PERIOD_DAYS = {
        "Неделя": 7,
        "Месяц": 30,
        "Год": 365,
        "Два года": 730,
    }

    DIRECTIONS_IN = {
        "in",
        "income",
        "credit",
        "приход",
        "поступление",
        "поступления",
        "доход",
        "доходы",
    }

    DIRECTIONS_OUT = {
        "out",
        "expense",
        "debit",
        "расход",
        "расходы",
        "списание",
        "списания",
    }

    # ------------------------------------------------------------------
    # Общие вспомогательные методы
    # ------------------------------------------------------------------

    @staticmethod
    def _to_date(value: date | datetime | pd.Timestamp) -> date:
        if isinstance(value, (datetime, pd.Timestamp)):
            return value.date()
        if isinstance(value, date):
            return value
        raise TypeError(f"Неподдерживаемый тип даты: {type(value).__name__}")

    @staticmethod
    def _money(value: Decimal | float | str) -> float:
        """Возвращает абсолютную сумму в формате float."""
        try:
            result = float(abs(Decimal(str(value))))
        except (InvalidOperation, ValueError, TypeError):
            return 0.0

        if not np.isfinite(result):
            return 0.0

        return result

    @staticmethod
    def _direction(value: Any) -> str:
        raw = str(value or "").strip().casefold()

        if raw in PredictionService.DIRECTIONS_IN:
            return "IN"

        if raw in PredictionService.DIRECTIONS_OUT:
            return "OUT"

        return str(value or "").strip().upper()

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default

        return result if np.isfinite(result) else default

    @staticmethod
    def _normalise_text(value: Any) -> str:
        return " ".join(str(value or "").casefold().split())

    # ------------------------------------------------------------------
    # Платёжные задержки и исторический остаток
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_payment_lags(
        operations: list[StoredOperation],
    ) -> pd.DataFrame:
        rows = []

        for op in operations:
            op_date = PredictionService._to_date(op.operation_date)

            try:
                doc_date = PaymentAnalyzer.extract_document_date(
                    op.payment_purpose
                )
            except Exception:
                doc_date = None

            if isinstance(doc_date, (datetime, pd.Timestamp)):
                doc_date = doc_date.date()

            lag = (
                (op_date - doc_date).days
                if isinstance(doc_date, date)
                else np.nan
            )

            rows.append(
                {
                    "counterparty": getattr(op, "counterparty_name", ""),
                    "direction": PredictionService._direction(
                        getattr(op, "direction", "")
                    ),
                    "amount": PredictionService._money(
                        getattr(op, "amount", 0)
                    ),
                    "operation_date": op_date,
                    "document_date": doc_date,
                    "payment_lag_days": lag,
                    "vat_status": None,
                    "doc_type": None,
                    "category": getattr(op, "category", ""),
                    "purpose": getattr(op, "payment_purpose", ""),
                }
            )

            try:
                rows[-1]["vat_status"] = (
                    PaymentAnalyzer.extract_vat_context(op.payment_purpose)
                )
            except Exception:
                pass

            try:
                rows[-1]["doc_type"] = (
                    PaymentAnalyzer.extract_document_type(op.payment_purpose)
                )
            except Exception:
                pass

        return pd.DataFrame(
            rows,
            columns=[
                "counterparty",
                "direction",
                "amount",
                "operation_date",
                "document_date",
                "payment_lag_days",
                "vat_status",
                "doc_type",
                "category",
                "purpose",
            ],
        )

    @staticmethod
    def filter_operations_by_period(
        operations: list[StoredOperation],
        period: str,
    ) -> tuple[list[StoredOperation], bool]:
        """
        Фильтрует операции по периоду.

        Если данных меньше выбранного периода, использует доступную историю,
        а не возвращает пустой результат только потому, что нет полных 2 лет.
        """
        if not operations:
            return [], False

        valid_operations = []

        for op in operations:
            try:
                PredictionService._to_date(op.operation_date)
                valid_operations.append(op)
            except (TypeError, ValueError):
                continue

        if not valid_operations:
            return [], False

        if period in {"Всё время", "Все время", "All time"}:
            return sorted(
                valid_operations,
                key=lambda op: PredictionService._to_date(op.operation_date),
            ), True

        required_days = PredictionService.PERIOD_DAYS.get(period)

        if required_days is None:
            return sorted(
                valid_operations,
                key=lambda op: PredictionService._to_date(op.operation_date),
            ), True

        dates = [
            PredictionService._to_date(op.operation_date)
            for op in valid_operations
        ]

        latest = max(dates)
        earliest = latest - timedelta(days=required_days - 1)

        filtered = [
            op
            for op in valid_operations
            if PredictionService._to_date(op.operation_date) >= earliest
        ]

        filtered.sort(
            key=lambda op: PredictionService._to_date(op.operation_date)
        )

        return filtered, bool(filtered)

    @staticmethod
    def calculate_historical_balance(
        operations: list[StoredOperation],
    ) -> list[tuple[date, Decimal]]:
        """
        Возвращает последний доступный остаток каждого дня.

        Предполагается, что balance_eod содержит остаток после операции.
        """
        if not operations:
            return []

        sorted_ops = sorted(
            operations,
            key=lambda op: (
                PredictionService._to_date(op.operation_date),
                getattr(op, "operation_date", None),
            ),
        )

        daily: dict[date, Decimal] = {}

        for op in sorted_ops:
            try:
                operation_date = PredictionService._to_date(op.operation_date)
                balance = Decimal(str(op.balance_eod))
            except (TypeError, ValueError, InvalidOperation):
                continue

            if not balance.is_finite():
                continue

            daily[operation_date] = balance

        return sorted(daily.items(), key=lambda item: item[0])

    # ------------------------------------------------------------------
    # Поиск похожих обязательств
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_duplicate_obligations(
        invoices: list[ExternalInvoice],
        obligations: list[dict],
    ) -> list[dict]:
        """
        Удаляет регулярное обязательство, если оно похоже на внешний счёт.

        Сравниваются направление, сумма, дата и текстовое описание.
        Если текст не позволяет надёжно подтвердить дубликат, обязательство
        сохраняется, чтобы не удалить реальный платёж ошибочно.
        """
        remaining = []

        for obligation in obligations:
            try:
                ob_date = PredictionService._to_date(obligation["date"])
                ob_amount = PredictionService._money(obligation["amount"])
                ob_direction = PredictionService._direction(
                    obligation.get("direction")
                )
            except (KeyError, TypeError, ValueError):
                remaining.append(obligation)
                continue

            description = PredictionService._normalise_text(
                obligation.get("description", "")
            )

            duplicate = False

            for invoice in invoices:
                if (
                    PredictionService._direction(invoice.direction)
                    != ob_direction
                ):
                    continue

                invoice_amount = PredictionService._money(invoice.amount)

                if invoice_amount <= 0 or ob_amount <= 0:
                    continue

                relative_difference = (
                    abs(invoice_amount - ob_amount) / invoice_amount
                )

                if relative_difference > 0.10:
                    continue

                invoice_date = PredictionService._to_date(invoice.due_date)

                if abs((invoice_date - ob_date).days) > 5:
                    continue

                counterparty = PredictionService._normalise_text(
                    invoice.counterparty_name
                )
                purpose = PredictionService._normalise_text(invoice.purpose)

                text_matches = (
                    bool(counterparty and counterparty in description)
                    or bool(purpose and purpose in description)
                )

                if text_matches:
                    duplicate = True
                    break

            if not duplicate:
                remaining.append(obligation)

        return remaining

    # ------------------------------------------------------------------
    # Подготовка временных рядов
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_outliers(
        series: pd.Series,
        window: int = 15,
        alpha: float = 3.5,
    ) -> pd.Series:
        """
        Мягкая очистка локальных выбросов.

        Для дневных потоков это эвристика: крупный платёж может быть настоящим,
        поэтому исходные данные не изменяются, а очищенный ряд используется
        только для обучения модели.
        """
        values = pd.to_numeric(series, errors="coerce").fillna(0.0).astype(float)

        if len(values) < 14:
            return values.copy()

        window = max(5, int(window))

        rolling_median = values.rolling(
            window=window,
            center=True,
            min_periods=max(3, window // 3),
        ).median()

        rolling_median = rolling_median.fillna(values.median())

        absolute_deviation = (values - rolling_median).abs()

        rolling_mad = absolute_deviation.rolling(
            window=window,
            center=True,
            min_periods=max(3, window // 3),
        ).median()

        robust_sigma = 1.4826 * rolling_mad

        mask = (
            (robust_sigma > 0)
            & ((values - rolling_median).abs() > alpha * robust_sigma)
        )

        cleaned = values.copy()
        cleaned.loc[mask] = rolling_median.loc[mask]

        return cleaned.clip(lower=0.0)

    @staticmethod
    def _find_recent_regime(series: pd.Series) -> pd.Series:
        """
        Ищет возможный сдвиг уровня потока.

        Если после предполагаемой точки смены режима недостаточно данных,
        исходный ряд не сокращается.
        """
        values = pd.to_numeric(series, errors="coerce").fillna(0.0)

        n = len(values)

        if n < 60:
            return values

        array = values.to_numpy(dtype=float)
        window = max(10, min(28, n // 6))

        best_idx = None
        best_score = 0.0

        for idx in range(window, n - window + 1):
            left = array[idx - window:idx]
            right = array[idx:idx + window]

            left_mean = float(np.mean(left))
            right_mean = float(np.mean(right))

            combined = np.concatenate([left, right])
            scale = float(np.std(combined))

            # При почти нулевом разбросе оценка нестабильна.
            if scale < 1e-8:
                continue

            score = abs(right_mean - left_mean) / scale

            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is None or best_score < 2.0:
            return values

        # Не используем слишком короткий новый режим.
        recent = values.iloc[best_idx:]

        if len(recent) < 28:
            return values

        return recent.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Модели и валидация
    # ------------------------------------------------------------------

    @staticmethod
    def _forecast_model(
        train: pd.Series,
        horizon: int,
        model: str,
    ) -> np.ndarray:
        """Строит неотрицательный прогноз дневного денежного потока."""
        horizon = max(0, int(horizon))

        if horizon == 0:
            return np.array([], dtype=float)

        y = (
            pd.to_numeric(train, errors="coerce")
            .fillna(0.0)
            .to_numpy(dtype=float)
        )

        y = np.maximum(y, 0.0)

        if len(y) == 0:
            return np.zeros(horizon, dtype=float)

        if model == "naive" or len(y) < 7:
            # Последний день может быть нулевым после длинной паузы.
            # Медиана последних 7 дней устойчивее единичного значения.
            recent = y[-min(7, len(y)):]
            value = float(np.median(recent))
            return np.full(horizon, max(0.0, value), dtype=float)

        if model == "seasonal_naive":
            pattern = y[-7:]
            repeats = int(np.ceil(horizon / len(pattern)))
            return np.tile(pattern, repeats)[:horizon].astype(float)

        if model == "trend":
            fit_n = min(len(y), 90)
            recent = y[-fit_n:]

            x = np.arange(fit_n, dtype=float)

            try:
                slope, intercept = np.polyfit(x, recent, 1)
            except (ValueError, np.linalg.LinAlgError):
                return np.full(
                    horizon,
                    max(0.0, float(np.median(recent))),
                    dtype=float,
                )

            future_x = np.arange(fit_n, fit_n + horizon, dtype=float)
            prediction = intercept + slope * future_x

            # Длинный линейный тренд способен давать чрезмерные значения.
            # Ограничиваем прогноз разумным диапазоном относительно истории.
            scale = max(
                float(np.quantile(recent, 0.95)),
                float(np.median(recent)) * 3.0,
                1.0,
            )

            prediction = np.clip(prediction, 0.0, scale)

            return prediction.astype(float)

        raise ValueError(f"Неизвестная модель: {model}")

    @staticmethod
    def _wape(actual: np.ndarray, predicted: np.ndarray) -> float:
        if len(actual) == 0:
            return float("inf")

        denominator = float(np.abs(actual).sum())

        if denominator < 1e-9:
            return float(np.abs(actual - predicted).mean())

        return float(np.abs(actual - predicted).sum() / denominator)

    @staticmethod
    def _select_model(
        series: pd.Series,
        horizon: int = 7,
    ) -> tuple[str, list[float]]:
        """
        Выбирает модель по временной валидации.

        Возвращает название модели и остатки actual - predicted,
        рассчитанные только на validation-отрезках.
        """
        candidates = ("naive", "seasonal_naive", "trend")
        n = len(series)

        if n < 28:
            if n >= 7:
                return "seasonal_naive", []
            return "naive", []

        validation_horizon = max(1, min(int(horizon), 14))

        origins = [
            n - 3 * validation_horizon,
            n - 2 * validation_horizon,
            n - validation_horizon,
        ]

        errors: dict[str, list[float]] = {
            name: [] for name in candidates
        }
        residuals_by_model: dict[str, list[float]] = {
            name: [] for name in candidates
        }

        for origin in origins:
            if origin < 14 or origin + validation_horizon > n:
                continue

            train = series.iloc[:origin]
            actual = series.iloc[
                origin:origin + validation_horizon
            ].to_numpy(dtype=float)

            for name in candidates:
                predicted = PredictionService._forecast_model(
                    train,
                    len(actual),
                    name,
                )

                errors[name].append(
                    PredictionService._wape(actual, predicted)
                )

                residuals_by_model[name].extend(
                    (actual - predicted).tolist()
                )

        scores = {
            name: (
                float(np.mean(values))
                if values
                else float("inf")
            )
            for name, values in errors.items()
        }

        selected = min(scores, key=scores.get)

        if not np.isfinite(scores[selected]):
            selected = "seasonal_naive" if n >= 7 else "naive"

        return selected, residuals_by_model[selected]

    @staticmethod
    def _residual_scale(residuals: list[float]) -> float:
        """Робастная оценка дневной ошибки в денежных единицах."""
        if not residuals:
            return 0.0

        arr = np.asarray(residuals, dtype=float)
        arr = arr[np.isfinite(arr)]

        if len(arr) == 0:
            return 0.0

        # RMSE отражает крупные ошибки; квантиль ограничивает влияние
        # единичного экстремального остатка.
        rmse = float(np.sqrt(np.mean(np.square(arr))))
        q90 = float(np.quantile(np.abs(arr), 0.90))

        return max(rmse, q90)

    # ------------------------------------------------------------------
    # Регулярные платежи
    # ------------------------------------------------------------------

    @staticmethod
    def _pattern_key(pattern: dict) -> tuple[str, str, str]:
        return (
            PredictionService._normalise_text(
                pattern.get("counterparty_name", "")
            ),
            PredictionService._direction(pattern.get("direction", "")),
            PredictionService._normalise_text(pattern.get("category", "")),
        )

    @staticmethod
    def _operation_matches_pattern(
        operation_row: Any,
        pattern_key: tuple[str, str, str],
    ) -> bool:
        counterparty, direction, category = pattern_key

        row_counterparty = PredictionService._normalise_text(
            getattr(operation_row, "counterparty", "")
        )
        row_direction = PredictionService._direction(
            getattr(operation_row, "direction", "")
        )
        row_category = PredictionService._normalise_text(
            getattr(operation_row, "category", "")
        )

        if not counterparty or not row_counterparty:
            return False

        if counterparty != row_counterparty:
            return False

        if direction != row_direction:
            return False

        # Пустая категория в паттерне не должна блокировать совпадение.
        if category and row_category and category != row_category:
            return False

        return True

    @staticmethod
    def _build_daily_flows(
        df: pd.DataFrame,
        full_dates: pd.DatetimeIndex,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Строит исходные и очищенные дневные ряды.
        """
        original = pd.DataFrame(index=full_dates)

        for direction in ("IN", "OUT"):
            part = df[df["direction"] == direction]

            values = part.groupby("operation_date")["amount"].sum()

            original[direction] = (
                values.reindex(full_dates, fill_value=0.0)
                .astype(float)
                .clip(lower=0.0)
            )

        cleaned = pd.DataFrame(index=full_dates)

        for direction in ("IN", "OUT"):
            cleaned[direction] = PredictionService._clean_outliers(
                original[direction]
            )

        return original, cleaned

    # ------------------------------------------------------------------
    # Основной прогноз
    # ------------------------------------------------------------------

    @staticmethod
    def predict_future(
        operations: list[StoredOperation],
        period: str = "Всё время",
        days_ahead: int = 30,
        external_invoices: list[ExternalInvoice] | None = None,
        external_taxes: list[ExternalTaxObligation] | None = None,
    ) -> dict:
        external_invoices = external_invoices or []
        external_taxes = external_taxes or []

        if days_ahead < 1:
            raise ValueError("days_ahead должен быть не меньше 1")

        filtered_ops, available = (
            PredictionService.filter_operations_by_period(
                operations,
                period,
            )
        )

        if not available or not filtered_ops:
            return {
                "is_available": False,
                "reason": "Нет операций для выбранного периода.",
            }

        df = PredictionService.calculate_payment_lags(filtered_ops)

        history_balance = PredictionService.calculate_historical_balance(
            filtered_ops
        )

        if not history_balance:
            return {
                "is_available": False,
                "reason": "Не удалось получить исторические остатки.",
            }

        dates, balances = zip(*history_balance)

        last_date = dates[-1]
        last_balance = float(balances[-1])

        if not np.isfinite(last_balance):
            return {
                "is_available": False,
                "reason": "Последний исторический остаток некорректен.",
            }

        start_date = dates[0]

        full_dates = pd.date_range(
            start=start_date,
            end=last_date,
            freq="D",
        )

        df["direction"] = df["direction"].map(
            PredictionService._direction
        )

        df["operation_date"] = pd.to_datetime(
            df["operation_date"],
            errors="coerce",
        ).dt.normalize()

        df["amount"] = pd.to_numeric(
            df["amount"],
            errors="coerce",
        ).fillna(0.0).clip(lower=0.0)

        df = df.dropna(subset=["operation_date"])

        if df.empty:
            return {
                "is_available": False,
                "reason": "Не удалось подготовить дневные денежные потоки.",
            }

        # Полные потоки до выделения регулярных операций.
        original_daily, cleaned_daily = (
            PredictionService._build_daily_flows(df, full_dates)
        )

        # Распознаём регулярные платежи.
        try:
            detected_patterns = (
                RecurringPaymentService.detect_recurring_operations(
                    filtered_ops
                )
                or []
            )
        except Exception:
            detected_patterns = []

        last_dt = datetime.combine(last_date, datetime.min.time())

        try:
            future_obligations = (
                RecurringPaymentService.generate_future_obligations(
                    detected_patterns,
                    last_dt,
                    horizon_days=days_ahead,
                )
                or []
            )
        except Exception:
            future_obligations = []

        # Убираем обязательства, которые совпадают с известными счетами.
        future_obligations = (
            PredictionService._detect_duplicate_obligations(
                external_invoices,
                future_obligations,
            )
        )

        # Исключаем исторические операции, относящиеся к обнаруженным
        # регулярным паттернам. Сопоставление допускает пустую категорию,
        # но требует совпадения контрагента и направления.
        recurring_mask = pd.Series(False, index=df.index)

        if detected_patterns:
            row_keys = [
                PredictionService._normalise_text(row.counterparty)
                for row in df.itertuples()
            ]

            # Сначала быстро формируем возможные совпадения.
            for pattern in detected_patterns:
                key = PredictionService._pattern_key(pattern)

                if not key[0]:
                    continue

                candidate = (
                    df["counterparty"]
                    .map(PredictionService._normalise_text)
                    .eq(key[0])
                    & df["direction"].eq(key[1])
                )

                if key[2]:
                    categories = df["category"].map(
                        PredictionService._normalise_text
                    )

                    candidate &= (
                        categories.eq(key[2]) | categories.eq("")
                    )

                recurring_mask |= candidate

        # Если регулярные операции были распознаны, обучаем модель на
        # оставшихся операциях. Если после исключения ряд пустой или
        # слишком короткий, используем полный ряд, чтобы не получить
        # искусственный нулевой прогноз.
        model_daily = cleaned_daily.copy()

        if detected_patterns and recurring_mask.any():
            non_recurring_df = df.loc[~recurring_mask]

            if not non_recurring_df.empty:
                _, non_recurring_daily = (
                    PredictionService._build_daily_flows(
                        non_recurring_df,
                        full_dates,
                    )
                )

                for direction in ("IN", "OUT"):
                    nonzero_days = int(
                        (non_recurring_daily[direction] > 0).sum()
                    )

                    if nonzero_days >= 3:
                        model_daily[direction] = (
                            non_recurring_daily[direction]
                        )

        forecasts: dict[str, np.ndarray] = {}
        model_info: dict[str, str] = {}
        residuals_by_direction: dict[str, list[float]] = {}
        residual_scale_by_direction: dict[str, float] = {}

        for direction in ("IN", "OUT"):
            series = (
                model_daily[direction]
                .reindex(full_dates, fill_value=0.0)
                .astype(float)
                .reset_index(drop=True)
            )

            series = PredictionService._find_recent_regime(series)

            model_name, residuals = PredictionService._select_model(
                series,
                horizon=min(days_ahead, 14),
            )

            forecasts[direction] = (
                PredictionService._forecast_model(
                    series,
                    days_ahead,
                    model_name,
                )
            )

            model_info[direction] = model_name
            residuals_by_direction[direction] = residuals
            residual_scale_by_direction[direction] = (
                PredictionService._residual_scale(residuals)
            )

        # Карты известных обязательств по датам.
        invoice_map: dict[date, list[ExternalInvoice]] = {}
        tax_map: dict[date, list[ExternalTaxObligation]] = {}
        obligation_map: dict[date, list[dict]] = {}

        horizon_end = last_date + timedelta(days=days_ahead)

        for invoice in external_invoices:
            invoice_date = PredictionService._to_date(invoice.due_date)

            if last_date < invoice_date <= horizon_end:
                invoice_map.setdefault(invoice_date, []).append(invoice)

        for tax in external_taxes:
            tax_date = PredictionService._to_date(tax.pay_date)

            if last_date < tax_date <= horizon_end:
                tax_map.setdefault(tax_date, []).append(tax)

        for obligation in future_obligations:
            try:
                obligation_date = PredictionService._to_date(
                    obligation["date"]
                )
            except (KeyError, TypeError, ValueError):
                continue

            if last_date < obligation_date <= horizon_end:
                obligation_map.setdefault(
                    obligation_date,
                    [],
                ).append(obligation)

        # Остатки на начало каждого будущего дня.
        realistic: list[tuple[date, float]] = []
        pessimistic: list[tuple[date, float]] = []
        optimistic: list[tuple[date, float]] = []

        current_real = last_balance

        # Оценка накопительной ошибки: неопределённость растёт с горизонтом
        # приблизительно как sqrt(h), а не вычитается повторно каждый день.
        # IN и OUT считаются раздельно, чтобы не смешивать масштабы потоков.
        scale_in = residual_scale_by_direction["IN"]
        scale_out = residual_scale_by_direction["OUT"]

        # При отсутствии валидационных остатков не придумываем искусственную
        # фиксированную неопределённость. Сценарии будут совпадать, а качество
        # прогноза следует считать ограниченным.
        risk_date = None

        for i in range(days_ahead):
            forecast_date = last_date + timedelta(days=i + 1)

            inflow = max(0.0, float(forecasts["IN"][i]))
            outflow = max(0.0, float(forecasts["OUT"][i]))

            invoice_in = sum(
                PredictionService._money(invoice.amount)
                for invoice in invoice_map.get(forecast_date, [])
                if PredictionService._direction(invoice.direction) == "IN"
            )

            invoice_out = sum(
                PredictionService._money(invoice.amount)
                for invoice in invoice_map.get(forecast_date, [])
                if PredictionService._direction(invoice.direction) == "OUT"
            )

            tax_out = sum(
                PredictionService._money(tax.amount)
                for tax in tax_map.get(forecast_date, [])
            )

            recurring_in = sum(
                PredictionService._money(obligation.get("amount", 0))
                for obligation in obligation_map.get(forecast_date, [])
                if PredictionService._direction(
                    obligation.get("direction")
                ) == "IN"
            )

            recurring_out = sum(
                PredictionService._money(obligation.get("amount", 0))
                for obligation in obligation_map.get(forecast_date, [])
                if PredictionService._direction(
                    obligation.get("direction")
                ) == "OUT"
            )

            net = (
                inflow
                + invoice_in
                + recurring_in
                - outflow
                - invoice_out
                - recurring_out
                - tax_out
            )

            current_real += net

            cumulative_uncertainty = (
                np.sqrt(i + 1)
                * np.sqrt(scale_in ** 2 + scale_out ** 2)
            )

            current_pess = current_real - cumulative_uncertainty
            current_opt = current_real + cumulative_uncertainty

            realistic.append((forecast_date, float(current_real)))
            pessimistic.append((forecast_date, float(current_pess)))
            optimistic.append((forecast_date, float(current_opt)))

            if risk_date is None and current_pess < 0:
                risk_date = forecast_date

        min_real = min(value for _, value in realistic)
        min_pess = min(value for _, value in pessimistic)

        if min_real < 0:
            risk_score, risk_level = 95, "критический"
            reason_prefix = (
                "Прогнозный остаток становится отрицательным "
                "в базовом сценарии"
            )
        elif min_pess < 0:
            risk_score, risk_level = 65, "высокий"
            reason_prefix = (
                "В пессимистичном сценарии возможен кассовый разрыв"
            )
        else:
            risk_score, risk_level = 15, "низкий"
            reason_prefix = (
                "Отрицательный остаток не выявлен "
                "в построенных сценариях"
            )

        valid_lags = df["payment_lag_days"].dropna()

        avg_lag = (
            float(valid_lags.mean())
            if not valid_lags.empty
            else 0.0
        )

        def summary(column: str) -> str:
            if column not in df or df[column].dropna().empty:
                return ""

            return ", ".join(
                f"{key}: {value}"
                for key, value in df[column]
                .value_counts(dropna=True)
                .items()
            )

        minimum = min(min_real, min_pess)
        deficit = max(0.0, abs(minimum)) if minimum < 0 else 0.0

        reason = (
            f"{reason_prefix}. "
            f"Минимальный остаток по сценариям: {minimum:,.0f} ₽. "
            f"Средний лаг по документам: {avg_lag:.1f} дн."
        ).replace(",", " ")

        # Если период длиннее доступной истории, явно отражаем это в метаданных.
        available_span_days = (dates[-1] - dates[0]).days + 1
        requested_span = PredictionService.PERIOD_DAYS.get(period)

        history_warning = ""

        if (
            requested_span is not None
            and available_span_days < requested_span
        ):
            history_warning = (
                f" Для периода «{period}» доступно только "
                f"{available_span_days} дн. истории."
            )

        if not any(residuals_by_direction.values()):
            history_warning += (
                " Недостаточно validation-ошибок для надёжной оценки "
                "неопределённости сценариев."
            )

        reason += history_warning

        history = [
            (day, float(balance))
            for day, balance in history_balance
        ]

        return {
            "is_available": True,
            "history": history,
            "realistic": realistic,
            "pessimistic": pessimistic,
            "optimistic": optimistic,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "risk_date": risk_date,
            "days_to_risk": (
                (risk_date - last_date).days if risk_date else 0
            ),
            "minimum_forecast_balance": round(minimum, 2),
            "estimated_deficit": round(deficit, 2),
            "reason": reason,
            "meta_vat": summary("vat_status"),
            "meta_categories": summary("category"),
            "meta_documents": summary("doc_type"),
            "detected_recurring_count": len(detected_patterns),
            "selected_models": model_info,
            "validation_method": "rolling-origin WAPE",
            "uncertainty_band": round(
                np.sqrt(scale_in ** 2 + scale_out ** 2),
                2,
            ),
            "history_days": available_span_days,
            "forecast_start_date": last_date,
            "starting_balance": round(last_balance, 2),
            "forecast_horizon": days_ahead,
        }
