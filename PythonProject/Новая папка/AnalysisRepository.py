
from dto.AnalysisPackage import FinalAnalysisPackage
from dto.OperationModels import OperationFilter, SortOrder, StoredOperation
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

    def load_and_process(self, company_id: str) -> int:
        """
        Загружает выписку, проверяет операции, обогащает их
        и сохраняет во внутреннем хранилище.
        """
        print(f"Загрузка данных для {company_id}...")

        raw_ops = self.parser.parse_company_statement(company_id)
        valid_ops, errors = self.validator.validate_operations(raw_ops)

        if errors:
            print(f"⚠️ Найдено ошибок при валидации: {len(errors)}")

        enriched_ops = [
            self.analyzer.analyze(op)
            for op in valid_ops
        ]

        self._storage = enriched_ops

        print(
            f"✅ Успешно обработано и сохранено "
            f"{len(self._storage)} операций."
        )

        return len(self._storage)

    def get_all_operations(self) -> list[StoredOperation]:
        """
        Возвращает копию всех загруженных операций.

        Копия списка позволяет вызывающему коду изменять список,
        не меняя внутреннее хранилище репозитория.
        """
        return self._storage.copy()

    def get_package(
        self,
        company_id: str,
        filters: OperationFilter
    ) -> FinalAnalysisPackage:
        """
        Возвращает аналитический пакет с учётом фильтров.
        """
        if not self._storage:
            raise ValueError(
                "Данные не загружены. Сначала вызовите load_and_process()"
            )

        return self.package_builder.build(
            company_id=company_id,
            operations=self._storage,
            filters=filters
        )

    def get_operations_for_ui(
        self,
        filters: OperationFilter
    ) -> list[StoredOperation]:
        """
        Возвращает отфильтрованный и отсортированный список операций для UI.
        """
        filtered_ops = self._storage.copy()

        if filters.date_from:
            filtered_ops = [
                op for op in filtered_ops
                if op.operation_date >= filters.date_from
            ]

        if filters.date_to:
            filtered_ops = [
                op for op in filtered_ops
                if op.operation_date <= filters.date_to
            ]

        if filters.counterparty_name:
            needle = filters.counterparty_name.casefold()
            filtered_ops = [
                op for op in filtered_ops
                if needle in op.counterparty_name.casefold()
            ]

        if filters.purpose_contains:
            needle = filters.purpose_contains.casefold()
            filtered_ops = [
                op for op in filtered_ops
                if needle in op.payment_purpose.casefold()
            ]

        reverse = filters.sort_by != SortOrder.DATE_ASC

        filtered_ops.sort(
            key=lambda op: op.operation_date,
            reverse=reverse
        )

        return filtered_ops
