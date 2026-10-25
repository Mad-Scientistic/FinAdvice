from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from dto.FutureCashflowModels import ExternalInvoice, ExternalTaxObligation


class ExternalObligationService:
    """
    Загружает неоплаченные счета и налоговые обязательства из CSV/XLS/XLSX.

    Счета: invoice_id, direction, amount, due_date, counterparty_name,
           purpose (необязательно), status (необязательно).
    Налоги: tax_id, amount, pay_date, tax_name, status (необязательно).

    Направление счёта: IN или OUT; также принимаются «Поступление» и «Списание».
    Неизвестные статусы отклоняются, чтобы не учитывать обязательства неверно.
    """

    PENDING_STATUSES = {
        "pending", "planned", "issued", "unpaid",
        "частично оплачен", "выставлен", "ожидает оплаты", "не оплачен",
    }
    IGNORED_STATUSES = {
        "paid", "cancelled", "canceled", "void",
        "оплачен", "оплачено", "оплачена", "отменён", "отменен",
        "отменена", "аннулирован",
    }

    @staticmethod
    def _read_file(path: str | Path) -> pd.DataFrame:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Файл не найден: {file_path}")

        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            errors: list[Exception] = []
            for encoding in ("utf-8-sig", "utf-8", "cp1251"):
                try:
                    return pd.read_csv(
                        file_path,
                        sep=None,
                        engine="python",
                        dtype=str,
                        encoding=encoding,
                        keep_default_na=False,
                    )
                except UnicodeDecodeError as exc:
                    errors.append(exc)
            raise ValueError(
                f"Не удалось определить кодировку CSV-файла: {file_path}"
            ) from errors[-1]

        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(file_path, dtype=str).fillna("")

        raise ValueError("Поддерживаются только CSV, XLSX и XLS.")

    @staticmethod
    def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        result.columns = [
            str(column).strip().lstrip("\ufeff").lower()
            for column in result.columns
        ]
        if len(set(result.columns)) != len(result.columns):
            raise ValueError("После нормализации имён обнаружены повторяющиеся столбцы.")
        return result

    @staticmethod
    def _required_columns(
        df: pd.DataFrame,
        columns: set[str],
        source: str,
    ) -> None:
        missing = columns - set(df.columns)
        if missing:
            raise ValueError(
                f"{source}: отсутствуют обязательные столбцы: "
                f"{', '.join(sorted(missing))}"
            )

    @staticmethod
    def _parse_date(value: Any, field_name: str) -> date:
        raw = str(value or "").strip()
        parsed = pd.to_datetime(raw, dayfirst=True, errors="coerce")
        if not raw or pd.isna(parsed):
            raise ValueError(f"Некорректная дата в поле {field_name}: {value!r}")
        return parsed.date()

    @staticmethod
    def _parse_amount(value: Any, field_name: str) -> Decimal:
        normalized = (
            str(value or "")
            .strip()
            .replace("\u00a0", "")
            .replace("\u202f", "")
            .replace(" ", "")
            .replace(",", ".")
            .replace("₽", "")
            .replace("руб.", "")
            .replace("руб", "")
        )
        try:
            amount = Decimal(normalized)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(
                f"Некорректная сумма в поле {field_name}: {value!r}"
            ) from exc

        if not amount.is_finite() or amount <= 0:
            raise ValueError(
                f"Сумма в поле {field_name} должна быть конечным числом больше нуля."
            )
        return amount.quantize(Decimal("0.01"))

    @classmethod
    def _is_pending(cls, status: Any) -> bool:
        value = str(status if status is not None else "pending").strip().casefold()
        if not value:
            return True
        if value in cls.IGNORED_STATUSES:
            return False
        if value in cls.PENDING_STATUSES:
            return True
        raise ValueError(
            f"Неизвестный статус {status!r}. Укажите pending/issued/unpaid "
            "либо paid/cancelled."
        )

    @staticmethod
    def _normalize_direction(value: Any, row_number: int) -> str:
        normalized = str(value or "").strip().casefold()
        mapping = {
            "in": "IN",
            "поступление": "IN",
            "поступления": "IN",
            "out": "OUT",
            "списание": "OUT",
            "списания": "OUT",
        }
        if normalized not in mapping:
            raise ValueError(
                f"Счета: направление должно быть IN/OUT или "
                f"Поступление/Списание, строка {row_number}."
            )
        return mapping[normalized]

    @classmethod
    def load_invoices(cls, path: str | Path) -> list[ExternalInvoice]:
        df = cls._normalize_columns(cls._read_file(path))
        cls._required_columns(
            df,
            {"invoice_id", "direction", "amount", "due_date", "counterparty_name"},
            "Реестр счетов",
        )

        invoices: list[ExternalInvoice] = []
        seen_ids: set[str] = set()

        for index, row in df.iterrows():
            row_number = index + 2
            invoice_id = str(row.get("invoice_id", "")).strip()
            if not invoice_id:
                raise ValueError(f"Счета: пустой invoice_id в строке {row_number}.")
            if invoice_id in seen_ids:
                raise ValueError(
                    f"Счета: повторяется invoice_id={invoice_id!r} "
                    f"(строка {row_number})."
                )
            seen_ids.add(invoice_id)

            if not cls._is_pending(row.get("status", "pending")):
                continue

            direction = cls._normalize_direction(row.get("direction"), row_number)
            counterparty = str(row.get("counterparty_name", "")).strip()
            if not counterparty:
                raise ValueError(
                    f"Счета: пустой counterparty_name в строке {row_number}."
                )

            invoices.append(ExternalInvoice(
                invoice_id=invoice_id,
                direction=direction,
                amount=cls._parse_amount(row.get("amount"), "amount"),
                due_date=cls._parse_date(row.get("due_date"), "due_date"),
                counterparty_name=counterparty,
                purpose=str(row.get("purpose", "")).strip(),
                status=str(row.get("status", "pending")).strip() or "pending",
            ))

        return invoices

    @classmethod
    def load_taxes(cls, path: str | Path) -> list[ExternalTaxObligation]:
        df = cls._normalize_columns(cls._read_file(path))
        cls._required_columns(
            df,
            {"tax_id", "amount", "pay_date", "tax_name"},
            "Реестр налогов",
        )

        taxes: list[ExternalTaxObligation] = []
        seen_ids: set[str] = set()

        for index, row in df.iterrows():
            row_number = index + 2
            tax_id = str(row.get("tax_id", "")).strip()
            if not tax_id:
                raise ValueError(f"Налоги: пустой tax_id в строке {row_number}.")
            if tax_id in seen_ids:
                raise ValueError(
                    f"Налоги: повторяется tax_id={tax_id!r} (строка {row_number})."
                )
            seen_ids.add(tax_id)

            if not cls._is_pending(row.get("status", "pending")):
                continue

            tax_name = str(row.get("tax_name", "")).strip()
            if not tax_name:
                raise ValueError(
                    f"Налоги: пустой tax_name в строке {row_number}."
                )

            taxes.append(ExternalTaxObligation(
                tax_id=tax_id,
                amount=cls._parse_amount(row.get("amount"), "amount"),
                pay_date=cls._parse_date(row.get("pay_date"), "pay_date"),
                tax_name=tax_name,
                status=str(row.get("status", "pending")).strip() or "pending",
            ))

        return taxes
