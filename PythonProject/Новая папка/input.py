from datetime import datetime
from decimal import Decimal

from dto.OperationModels import OperationFilter
from repository.AnalysisRepository import AnalysisRepository


def main():
    # 1. Путь к данным
    data_dir = "cashflow_dataset_participants/statements"

    # 2. Инициализация репозитория
    repo = AnalysisRepository(data_dir=data_dir)

    # 3. Загрузка (один раз)
    repo.load_and_process("C001")

    # 4. Формируем фильтры для ядра
    # Например: ядру нужны все операции за Октябрь 2024,
    # сумма больше 10 000, чтобы не засорять модель мелочью.
    filters = OperationFilter(
        date_from=datetime(2024, 10, 1),
        date_to=datetime(2024, 10, 31),
        min_amount=Decimal("10000.00")
    )

    # 5. Получаем финальный объект
    package = repo.get_package("C001", filters)

    # ==========================================
    # ВЫВОД ПОЛНОГО ОБЪЕКТА В КОНСОЛЬ (JSON)
    # ==========================================
    print("\n" + "=" * 60)
    print("ФИНАЛЬНЫЙ ОБЪЕКТ ДЛЯ ВЫЧИСЛИТЕЛЬНОГО ЯДРА (JSON):")
    print("=" * 60 + "\n")

    # model_dump_json() превращает объект Pydantic в красивую JSON-строку
    json_output = package.model_dump_json(indent=2)

    # Выводим в консоль
    print(json_output)

    # ==========================================
    # (Опционально) Сохраняем в файл
    # ==========================================
    with open("final_package_for_core.json", "w", encoding="utf-8") as f:
        f.write(json_output)

    print("\n" + "=" * 60)
    print("✅ Файл 'final_package_for_core.json' успешно создан.")
    print("=" * 60)


if __name__ == "__main__":
    main()