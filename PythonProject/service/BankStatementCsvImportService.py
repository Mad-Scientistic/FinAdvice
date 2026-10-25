
import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dto.OperationModels import RawOperation


class BankStatementCsvImportService:
    ALIASES = {
        "date": {
            "дата", "дата операции", "дата платежа",
            "дата проводки", "operation_date", "date",
            "transaction date", "booking date",
        },
        "operation_id": {
            "id", "id операции", "номер операции",
            "номер документа", "operation_id",
            "transaction_id", "transaction id",
        },
        "amount": {
            "сумма", "сумма операции", "сумма платежа",
            "amount", "transaction amount",
        },
        "income": {
            "приход", "поступление", "зачисление",
            "кредит", "credit", "income",
        },
        "expense": {
            "расход", "списание", "дебет",
            "debit", "expense",
        },
        "direction": {
            "направление", "тип операции", "вид операции",
            "direction", "type",
        },
        "counterparty_inn": {
            "инн контрагента", "инн", "inn",
            "counterparty_inn", "tax id",
        },
        "counterparty_name": {
            "контрагент", "наименование контрагента",
            "получатель", "плательщик", "название контрагента",
            "counterparty", "counterparty_name",
            "counterparty name", "recipient", "payer",
        },
        "purpose": {
            "назначение платежа", "назначение",
            "описание", "комментарий", "purpose",
            "payment_purpose", "payment purpose",
            "description", "details",
        },
        "balance": {
            "остаток", "остаток на конец дня",
            "остаток после операции", "баланс",
            "balance", "balance_eod", "closing balance",
        },
    }

    @staticmethod
    def _normalize_header(value):
        value = str(value or "").strip().lower().replace("ё", "е")
        return re.sub(r"[\s_\-]+", " ", value)

    @staticmethod
    def _decimal(value, default=None):
        text = str(value or "").strip()
        if not text:
            return default

        text = (
            text.replace("\u00a0", "")
            .replace(" ", "")
            .replace("₽", "")
            .replace("руб.", "")
            .replace("руб", "")
        )

        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")

        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        else:
            text = text.replace(",", ".")

        try:
            result = Decimal(text)
            return -abs(result) if negative else result
        except InvalidOperation:
            raise ValueError(f"Некорректная сумма: {value!r}")

    @staticmethod
    def _parse_date(value):
        text = str(value or "").strip()
        if not text:
            raise ValueError("Не указана дата операции")

        formats = (
            "%d.%m.%Y",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%Y-%m-%dT%H:%M:%S",
        )

        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass

        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Не удалось распознать дату: {value!r}")

    @classmethod
    def _find_columns(cls, headers):
        normalized = {
            cls._normalize_header(header): header
            for header in headers
        }

        result = {}
        for field, aliases in cls.ALIASES.items():
            for alias in aliases:
                normalized_alias = cls._normalize_header(alias)
                if normalized_alias in normalized:
                    result[field] = normalized[normalized_alias]
                    break

        return result

    @staticmethod
    def _get(row, columns, field, default=""):
        column = columns.get(field)
        return row.get(column, default) if column else default

    @staticmethod
    def _parse_direction(value, amount):
        text = str(value or "").strip().lower().replace("ё", "е")

        if any(word in text for word in (
            "поступ", "приход", "доход", "зачисл",
            "income", "credit", "входящ",
        )):
            return "Поступление"

        if any(word in text for word in (
            "спис", "расход", "платеж", "перевод",
            "expense", "debit", "исходящ",
        )):
            return "Списание"

        if amount > 0:
            return "Поступление"
        if amount < 0:
            return "Списание"

        raise ValueError(
            "Не удалось определить направление операции: "
            "нет направления и знак суммы равен нулю"
        )

    @classmethod
    def parse_file(cls, file_path, company_id):
        path = Path(file_path)

        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Файл не найден: {path}")

        if path.suffix.lower() != ".csv":
            raise ValueError("Поддерживаются только CSV-файлы")

        last_error = None
        rows = None
        headers = None

        for encoding in ("utf-8-sig", "cp1251", "utf-8"):
            try:
                with path.open(
                    "r",
                    encoding=encoding,
                    newline="",
                ) as file:
                    sample = file.read(8192)
                    file.seek(0)

                    try:
                        dialect = csv.Sniffer().sniff(
                            sample,
                            delimiters=";,\t|",
                        )
                    except csv.Error:
                        dialect = csv.excel
                        dialect.delimiter = ";"

                    reader = csv.DictReader(file, dialect=dialect)
                    headers = reader.fieldnames

                    if not headers:
                        raise ValueError("В CSV отсутствует строка заголовков")

                    rows = list(reader)
                break
            except UnicodeDecodeError as exc:
                last_error = exc

        if rows is None:
            raise ValueError(
                f"Не удалось прочитать CSV в поддерживаемых кодировках: "
                f"{last_error}"
            )

        columns = cls._find_columns(headers)

        if "date" not in columns:
            raise ValueError(
                "Не найден столбец даты. Поддерживаются, например, "
                "'Дата операции' и 'operation_date'."
            )

        if not any(key in columns for key in ("amount", "income", "expense")):
            raise ValueError(
                "Не найдены столбцы суммы, прихода или расхода."
            )

        operations = []
        errors = []

        for line_number, row in enumerate(rows, start=2):
            if not any(str(value or "").strip() for value in row.values()):
                continue

            try:
                operation_date = cls._parse_date(
                    cls._get(row, columns, "date")
                )

                income_raw = cls._get(row, columns, "income")
                expense_raw = cls._get(row, columns, "expense")
                amount_raw = cls._get(row, columns, "amount")

                if income_raw.strip() or expense_raw.strip():
                    income = abs(cls._decimal(income_raw, Decimal(0)))
                    expense = abs(cls._decimal(expense_raw, Decimal(0)))

                    if income > 0 and expense > 0:
                        raise ValueError(
                            "В одной строке одновременно указаны приход и расход"
                        )

                    signed_amount = income if income > 0 else -expense
                    if signed_amount == 0 and amount_raw.strip():
                        signed_amount = cls._decimal(amount_raw)
                else:
                    signed_amount = cls._decimal(amount_raw)

                if signed_amount is None:
                    raise ValueError("Не указана сумма операции")

                direction = cls._parse_direction(
                    cls._get(row, columns, "direction"),
                    signed_amount,
                )

                amount = abs(signed_amount)

                balance = cls._decimal(
                    cls._get(row, columns, "balance"),
                    Decimal(0),
                )

                operation_id = str(
                    cls._get(row, columns, "operation_id")
                    or f"{path.stem}-{line_number}"
                ).strip()

                operations.append(
                    RawOperation(
                        company_id=str(company_id),
                        operation_id=operation_id,
                        operation_date=operation_date,
                        amount=amount,
                        direction=direction,
                        counterparty_inn=str(
                            cls._get(row, columns, "counterparty_inn") or ""
                        ).strip(),
                        counterparty_name=str(
                            cls._get(row, columns, "counterparty_name")
                            or "Без контрагента"
                        ).strip(),
                        payment_purpose=str(
                            cls._get(row, columns, "purpose") or ""
                        ).strip(),
                        balance_eod=balance or Decimal(0),
                    )
                )

            except (ValueError, InvalidOperation, TypeError) as exc:
                errors.append(f"Строка {line_number}: {exc}")

        if not operations:
            details = "\n".join(errors[:10])
            raise ValueError(
                "Не удалось импортировать ни одной операции.\n" + details
            )

        return operations, errors
