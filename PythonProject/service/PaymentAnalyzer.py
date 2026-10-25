import re
from datetime import date  # ИСПРАВЛЕНО: Добавлен обязательный импорт date
from decimal import Decimal

from dto.OperationModels import RawOperation, StoredOperation


class PaymentAnalyzer:
    def __init__(self):
        # Расширенный словарь шаблонов с учетом отраслевой специфики (Пункт 4)
        self.patterns = {
            "bank_fee": [
                r"комиссия\s+за\s+(ведение|обслуживание|расчетно)",
                r"абонентская\s+плата\s+за\s+рко",
                r"комиссия\s+банка\s+за",
                r"комиссия\s+за\s+перевод",
            ],
            "salary": [
                r"заработн[а-я]+\s+плат[а-я]+",
                r"оплата\s+труда",
                r"выплат[а-я]+\s+зарплат[а-я]+",
                r"аванс\s+по\s+зарплате",
                r"перечисление\s+заработной",
            ],
            "tax": [
                r"единый\s+налоговый\s+платеж",
                r"\bенп\b",
                r"налог\s+на\s+",
                r"страховые\s+взносы",
                r"взносы\s+в\s+пфр",
                r"взносы\s+в\s+фсс",
                r"\bndfl\b",
                r"\bндфл\b",
                r"пошлин[а-я]*",
                r"\bфнс\b",
            ],
            "fuel": [
                r"топливн[а-я]+\s+карт[а-я]+",
                r"\bазс\b",
                r"бензин",
                r"дизельное\s+топливо",
                r"нефтепродукт",
            ],
            "telecom": [
                r"услуг[а-я]+\s+связи",
                r"доступ\s+в\s+интернет",
                r"мобильная\s+связь",
                r"телекоммуникац",
            ],
            "accounting": [
                r"бухгалтерск[а-я]+\s+услуг[а-я]+",
                r"аутсорсинг\s+бухгалтерии",
            ],
            "loan": [
                r"погашение\s+кредит",
                r"оплата\s+процентов\s+по\s+кредит",
                r"основной\s+долг\s+по\s+договор",
                r"возврат\s+займа",
            ],
            # [НОВОЕ] Отраслевая детализация категорий (Пункт 4)
            "logistics": [
                r"транспортн[а-я]+", r"логистик[а-я]+", r"экспедитор[а-я]*", 
                r"доставк[а-я]+", r"фрахт", r"перевозк[а-я]+"
            ],
            "elevator": [
                r"элеватор[а-я]*", r"хранен[иея]+", r"складск[а-я]+", r"терминал[а-я]*"
            ],
            "raw_materials": [
                r"зерно[а-я]*", r"сырье", r"сырьё", r"пшениц[а-я]+", 
                r"закупк[а-я]+", r"семен[а-я]*", r"удобрен[ияа-я]+"
            ],
            "ready_products": [
                r"продукц[ияиеа-я]+", r"реализац[ияиеа-я]+", r"продаж[а-я]+", 
                r"отгрузк[а-я]+", r"дистрибьютор[а-я]*"
            ]
        }

        combined_patterns = []
        for cat_name, regex_list in self.patterns.items():
            combined_expr = f"(?P<{cat_name}>" + "|".join(regex_list) + ")"
            combined_patterns.append(combined_expr)
        self.master_regex = re.compile("|".join(combined_patterns), re.IGNORECASE)

    def _clean_decimal(self, value: str) -> Decimal:
        if not value:
            return Decimal("0.00")
        clean_str = re.sub(r"[^\d.,]", "", value).replace(",", ".")
        try:
            return Decimal(clean_str)
        except Exception:
            return Decimal("0.00")

    @staticmethod
    def extract_document_date(payment_purpose: str) -> date | None:
        """[1. ДАТЫ ПЕРВИЧНЫХ ДОКУМЕНТОВ] Находит дату документа в тексте."""
        if not payment_purpose:
            return None
        match = re.search(r'(?:от|с)\s+(\d{2}\.\d{2}\.\d{4})', payment_purpose, re.IGNORECASE)
        if not match:
            match = re.search(r'\b(\d{2}\.\d{2}\.\d{4})\b', payment_purpose)
        if match:
            try:
                from datetime import datetime
                return datetime.strptime(match.group(1), "%d.%m.%Y").date()
            except ValueError:
                return None
        return None

    @staticmethod
    def extract_vat_context(payment_purpose: str) -> str:
        """[2. НАЛОГОВЫЙ КОНТЕКСТ] Строковый маркер НДС для фильтрации/анализа."""
        if not payment_purpose:
            return "Не указано"
        text = payment_purpose.lower()
        if "без ндс" in text:
            return "Без НДС"
        if "ндс не облагается" in text:
            return "НДС не облагается"
        if "ндс" in text:
            return "С НДС"
        return "Не указано"

    @staticmethod
    def extract_document_type(payment_purpose: str) -> str:
        """[3. ТИПЫ ДОКУМЕНТОВ] Извлекает тип документа."""
        if not payment_purpose:
            return "Прочее"
        text = payment_purpose.lower()
        if "упд" in text: return "УПД"
        if "счет" in text or "с/ф" in text or "счёт" in text: return "Счет"
        if "акт" in text: return "Акт"
        if "договор" in text: return "Договор"
        if "накладн" in text: return "Накладная"
        return "Прочее"

    @staticmethod
    def extract_document_type_and_number(payment_purpose: str) -> tuple[str, str | None]:
        """[3. ТИПЫ И НОМЕРА ДОКУМЕНТОВ] Совмещенный метод для обратной совместимости."""
        doc_type = PaymentAnalyzer.extract_document_type(payment_purpose)
        if not payment_purpose:
            return doc_type, None
        num_match = re.search(r'[№#]\s*([а-яА-Яa-zA-Z0-9\-/]+)', payment_purpose)
        doc_number = num_match.group(1) if num_match else None
        return doc_type, doc_number


    def analyze(self, raw_op: RawOperation) -> StoredOperation:
        """Принимает сырую операцию, возвращает обогащенную StoredOperation."""
        text = raw_op.payment_purpose or ""
        counterparty = raw_op.counterparty_name or ""
        full_text = f"{counterparty} {text}"
        
        category = "other"
        contract = None
        vat = None

        # Определение категории через master_regex
        match = self.master_regex.search(full_text)
        if match:
            for cat_name in self.patterns.keys():
                if match.group(cat_name):
                    category = cat_name
                    break

        # [3. НОМЕРА ДОКУМЕНТОВ И ДОГОВОРОВ] Универсальный парсинг номеров
        # Сначала ищем по стандартным группам, как у вас
        if category in ["loan", "fuel"]:
            contract_match = re.search(r"договору\s+№\s*([^\s,]+)", text, re.IGNORECASE)
            if not contract_match and category == "fuel":
                contract_match = re.search(r"договору\s+№\s*(\d+)", text, re.IGNORECASE)
            if contract_match:
                contract = contract_match.group(1)
        
        # Если номер не найден специализированным паттерном, ищем любой знак № или #
        if not contract:
            num_match = re.search(r'[№#]\s*([а-яА-Яa-zA-Z0-9\-/]+)', text)
            if num_match:
                contract = num_match.group(1)

        # Вычисление числовой суммы НДС
        vat_match = re.search(r"(?:В т\.ч\.|в том числе)\s*(?:НДС)?\s*\(?(?:20|22)%?\)?\s*[-:]?\s*([\d\s.,]+)", text, re.IGNORECASE)
        if vat_match:
            vat = self._clean_decimal(vat_match.group(1))

        return StoredOperation(
            operation_id=raw_op.operation_id,
            operation_date=raw_op.operation_date,
            amount=raw_op.amount,
            direction=raw_op.direction,
            counterparty_name=raw_op.counterparty_name,
            payment_purpose=raw_op.payment_purpose,
            category=category,              # Передаем обновленные или базовые категории
            contract_number=contract,        # Сюда пишется номер документа/договора
            vat_amount=vat,                 # Сумма НДС
            balance_eod=raw_op.balance_eod
        )
