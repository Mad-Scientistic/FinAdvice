from decimal import Decimal

from dto.AnalysisPackage import FinalAnalysisPackage
from dto.OperationModels import OperationFilter, SortOrder, StoredOperation
from service.BankStatementCsvImportService import (
    BankStatementCsvImportService,
)
from service.CsvParserService import CsvParserService
from service.PackageBuilderService import PackageBuilderService
from service.PaymentAnalyzer import PaymentAnalyzer
from service.ValidatorService import ValidatorService


class AnalysisRepository:
    def __init__(self, data_dir: str):
        self.parser = CsvParserService(base_dir=data_dir)
        self.validator = ValidatorService()
        self.analyzer = PaymentAnalyzer()
        self.package_builder = PackageBuilderService()

        self._storage: list[StoredOperation] = []
        self.last_import_errors: list[str] = []
        self.current_company_id: str | None = None

    def _save_operations(
        self,
        company_id: str,
        raw_ops: list,
        parse_errors: list[str] | None = None,
    ) -> int:
        """
        Валидирует и сохраняет операции выбранной компании.
        Обновляет ID активной компании только после успешной загрузки.
        """
        company_id = company_id.strip()

        if not company_id:
            raise ValueError("ID компании не может быть пустым.")

        valid_ops, validation_errors = (
            self.validator.validate_operations(raw_ops)
        )

        errors = list(parse_errors or [])
        errors.extend(str(error) for error in validation_errors)

        if not valid_ops:
            self.last_import_errors = errors
            raise ValueError(
                "Не удалось загрузить корректные операции. "
                f"Ошибок: {len(errors)}."
            )

        enriched_ops = [
            self.analyzer.analyze(op)
            for op in valid_ops
        ]

        # Меняем состояние только после успешной обработки.
        self._storage = enriched_ops
        self.current_company_id = company_id
        self.last_import_errors = errors

        return len(self._storage)

    def load_and_process(self, company_id: str) -> int:
        """
        Загружает выписку компании из источника данных,
        валидирует операции, выполняет анализ и сохраняет результат.
        """
        company_id = company_id.strip()
        print(f"Загрузка данных для {company_id}...")

        raw_ops = self.parser.parse_company_statement(company_id)
        count = self._save_operations(company_id, raw_ops)

        print(
            f"Успешно обработано и сохранено {count} операций "
            f"для {self.current_company_id}."
        )

        if self.last_import_errors:
            print(
                "Предупреждения при обработке: "
                f"{len(self.last_import_errors)}"
            )

        return count

    def load_csv_file(
        self,
        file_path: str,
        company_id: str,
    ) -> int:
        """
        Импортирует CSV-выписку для указанной компании.
        """
        company_id = company_id.strip()

        raw_ops, parse_errors = (
            BankStatementCsvImportService.parse_file(
                file_path=file_path,
                company_id=company_id,
            )
        )

        count = self._save_operations(
            company_id=company_id,
            raw_ops=raw_ops,
            parse_errors=parse_errors,
        )

        print(
            f"Импортировано операций: {count}. "
            f"Компания: {self.current_company_id}."
        )

        if self.last_import_errors:
            print(
                "Ошибок и предупреждений при импорте: "
                f"{len(self.last_import_errors)}"
            )

        return count

    def get_all_operations(self) -> list[StoredOperation]:
        """Возвращает копию списка всех загруженных операций."""
        return self._storage.copy()

    def get_latest_operation(self) -> StoredOperation | None:
        """Возвращает последнюю операцию по дате."""
        if not self._storage:
            return None

        return max(
            self._storage,
            key=lambda op: op.operation_date,
        )

    def get_current_balance(self) -> Decimal | None:
        """
        Возвращает balance_eod последней операции.
        Не пересчитывает банковский остаток самостоятельно.
        """
        latest = self.get_latest_operation()

        if latest is None:
            return None

        return latest.balance_eod

    def get_current_company_id(self) -> str | None:
        """Возвращает ID компании, чьи операции сейчас загружены."""
        return self.current_company_id

    def get_package(
        self,
        company_id: str | None,
        filters: OperationFilter,
    ) -> FinalAnalysisPackage:
        """
        Формирует аналитический пакет для активной компании.
        """
        if not self._storage:
            raise ValueError(
                "Данные не загружены. "
                "Сначала вызовите load_and_process() или load_csv_file()."
            )

        active_company_id = (
            self.current_company_id
            or (company_id.strip() if company_id else None)
        )

        if not active_company_id:
            raise ValueError("Не выбрана компания.")

        return self.package_builder.build(
            company_id=active_company_id,
            operations=self._storage,
            filters=filters,
        )

    def get_operations_for_ui(
        self,
        filters: OperationFilter,
    ) -> list[StoredOperation]:
        """
        Возвращает отфильтрованные и отсортированные операции для UI.
        """
        filtered_ops = self._storage.copy()

        if filters.date_from:
            filtered_ops = [
                op
                for op in filtered_ops
                if op.operation_date >= filters.date_from
            ]

        if filters.date_to:
            filtered_ops = [
                op
                for op in filtered_ops
                if op.operation_date <= filters.date_to
            ]

        if filters.counterparty_name:
            needle = filters.counterparty_name.casefold()
            filtered_ops = [
                op
                for op in filtered_ops
                if needle in op.counterparty_name.casefold()
            ]

        if filters.purpose_contains:
            needle = filters.purpose_contains.casefold()
            filtered_ops = [
                op
                for op in filtered_ops
                if needle in op.payment_purpose.casefold()
            ]

        reverse = filters.sort_by != SortOrder.DATE_ASC

        filtered_ops.sort(
            key=lambda op: op.operation_date,
            reverse=reverse,
        )

        return filtered_ops