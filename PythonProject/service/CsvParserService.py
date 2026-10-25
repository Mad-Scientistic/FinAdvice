import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from dto.OperationModels import RawOperation  # <-- Новый импорт


class CsvParserService:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)

    def _parse_decimal(self, value: str) -> Decimal:
        """Конвертирует строку в Decimal."""
        if not value:
            return Decimal("0.00")
        clean_value = value.strip().replace(" ", "").replace(",", ".")
        return Decimal(clean_value)

    def _parse_date(self, value: str) -> datetime:
        """Парсит дату из формата YYYY-MM-DD."""
        return datetime.strptime(value.strip(), "%Y-%m-%d")

    def parse_company_statement(self, company_id: str) -> list[RawOperation]:
        """
        Читает файл выписки и возвращает список объектов RawOperation.
        """
        file_path = self.base_dir / "statements" / f"{company_id}.csv"

        if not file_path.exists():
            raise FileNotFoundError(f"Файл выписки не найден: {file_path}")

        operations = []

        with open(file_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")

            for row in reader:
                try:
                    if not row.get("operation_id"):
                        continue

                    # Создаем объект Pydantic. Он сам проверит, что типы совпадают.
                    op = RawOperation(
                        company_id=row["company_id"].strip(),
                        operation_id=row["operation_id"].strip(),
                        operation_date=self._parse_date(row["operation_date"]),
                        amount=self._parse_decimal(row["amount"]),
                        direction=row["direction"].strip(),
                        counterparty_inn=row["counterparty_inn"].strip(),
                        counterparty_name=row["counterparty_name"].strip(),
                        payment_purpose=row["payment_purpose"].strip(),
                        balance_eod=self._parse_decimal(row["balance_eod"])
                    )
                    operations.append(op)

                except Exception as e:
                    # Логируем и пропускаем битую строку
                    print(f"Ошибка при парсинге строки {row.get('operation_id')}: {e}")
                    continue

        return operations