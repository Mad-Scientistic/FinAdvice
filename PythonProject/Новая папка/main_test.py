import traceback
from datetime import datetime, time, timedelta
from pathlib import Path

import flet as ft
import flet_charts as fch
from dto.OperationModels import OperationFilter, SortOrder
from repository.AnalysisRepository import AnalysisRepository
from service.PredictionService import PredictionService
from service.RecurringPaymentService import RecurringPaymentService


def money_fmt(value):
    try:
        return f"{float(value):,.0f} ₽".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def is_income_operation(operation):
    direction = str(getattr(operation, "direction", "")).strip().lower()
    return direction in {
        "income", "in", "приход", "поступление", "доход", "credit"
    }


def calculate_net_cashflow(operations):
    total = 0.0
    for operation in operations:
        amount = float(getattr(operation, "amount", 0) or 0)
        total += amount if is_income_operation(operation) else -amount
    return total


def safe_date(operation):
    value = getattr(operation, "operation_date", None)
    if isinstance(value, datetime):
        return value
    if value is not None:
        try:
            return datetime.combine(value, time.min)
        except (TypeError, ValueError):
            pass
    return datetime.min


def padded_range(values):
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return 0.0, 100.0
    low, high = min(clean), max(clean)
    span = high - low
    padding = span * 0.1 if span else max(abs(high) * 0.1, 1.0)
    return low - padding, high + padding


def main(page: ft.Page):
    page.title = "FinAdvice — Финансовый советник"
    page.window_width = 1200
    page.window_height = 850
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#12141c"
    page.padding = 0

    state = {
        "selected_period": "Всё время",
        "forecast_days": 30,
        "show_forecast": True,
    }

    data_dir = str(Path(__file__).parent / "cashflow_dataset_participants")
    repo = AnalysisRepository(data_dir=data_dir)
    company_id = "C002"
    load_error = None

    try:
        loaded_count = repo.load_and_process(company_id)
        print(f"Успешно загружено операций для {company_id}: {loaded_count}")
    except Exception as exc:
        load_error = str(exc)
        print(f"Ошибка загрузки данных: {exc}")
        traceback.print_exc()

    all_ops = list(getattr(repo, "_storage", []) or [])
    from collections import Counter

    print("\n=== ДИАГНОСТИКА ОПЕРАЦИЙ ===")
    print("Всего операций:", len(all_ops))

    print("Направления операций:")
    for direction, count in Counter(
        str(getattr(op, "direction", None))
        for op in all_ops
    ).items():
        print(f"  {direction!r}: {count}")

    print("\nПримеры операций:")
    for op in all_ops[:10]:
        print(
            "Дата:", getattr(op, "operation_date", None),
            "| Направление:", repr(getattr(op, "direction", None)),
            "| Сумма:", getattr(op, "amount", None),
            "| Контрагент:", getattr(op, "counterparty_name", None),
        )
    all_ops.sort(key=safe_date)
    max_data_date = max((safe_date(op) for op in all_ops), default=datetime.now())
    user_name = f"Компания {company_id}"

    # Общие элементы интерфейса
    def show_message(message):
        page.open(ft.SnackBar(content=ft.Text(message)))

    def section_card(title, content, height=None):
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(title, size=15, weight=ft.FontWeight.BOLD),
                    ft.Container(height=5),
                    content,
                ],
                spacing=5,
            ),
            bgcolor="#1a1f2c",
            padding=18,
            border_radius=14,
            height=height,
        )

    # Вкладка 1 — главная
    current_balance = 100000.0  # Демонстрационный баланс, не баланс из базы.
    dashboard_view = ft.ListView(
        controls=[
            ft.Text(user_name, size=24, weight=ft.FontWeight.BOLD),
            ft.Container(height=8),
            ft.Text(f"Баланс: {money_fmt(current_balance)}", size=20, color="#a0aec0"),
            ft.Text(
                "Баланс задан демонстрационным значением."
                if not load_error else f"Ошибка загрузки: {load_error}",
                size=12,
                color="#a0aec0" if not load_error else ft.Colors.RED_400,
            ),
            ft.Container(height=10),
            ft.Text(f"Операций загружено: {len(all_ops)}", size=14),
            ft.Text(
                f"Последняя дата в данных: {max_data_date:%d.%m.%Y}"
                if all_ops else "Данные операций отсутствуют",
                size=14,
                color="#a0aec0",
            ),
        ],
        expand=True,
        padding=20,
        spacing=8,
    )

    # Вкладка 2 — история операций
    transactions_column = ft.Column(spacing=6)
    income_checkbox = ft.Checkbox(label="Доходы", value=True)
    expense_checkbox = ft.Checkbox(label="Расходы", value=True)
    sort_dropdown = ft.Dropdown(
        label="Сортировка",
        options=[
            ft.dropdown.Option(key=SortOrder.DATE_DESC.value, text="Сначала новые"),
            ft.dropdown.Option(key=SortOrder.DATE_ASC.value, text="Сначала старые"),
        ],
        value=SortOrder.DATE_DESC.value,
        width=200,
    )
    period_radio = ft.RadioGroup(
        value="month",
        content=ft.Row(
            controls=[
                ft.Radio(value="week", label="Неделя"),
                ft.Radio(value="month", label="Месяц"),
                ft.Radio(value="year", label="Год"),
                ft.Radio(value="all", label="Все"),
            ],
            wrap=True,
        ),
    )
    date_from_field = ft.TextField(label="С даты (ДД.ММ.ГГГГ)", width=190)
    date_to_field = ft.TextField(label="По дату (ДД.ММ.ГГГГ)", width=190)
    filter_mode = {"value": "period"}
    filter_panel = ft.Container(visible=False, padding=10)

    def get_history_dates():
        date_from = date_to = None
        if filter_mode["value"] == "period":
            base = max_data_date.date()
            mode = period_radio.value or "month"
            if mode == "week":
                start = base - timedelta(days=7)
            elif mode == "month":
                start = base.replace(day=1)
            elif mode == "year":
                start = base.replace(month=1, day=1)
            elif mode == "all":
                start = None
            else:
                start = base
            if start:
                date_from = datetime.combine(start, time.min)
                date_to = datetime.combine(base + timedelta(days=1), time.min)
        else:
            if (date_from_field.value or "").strip():
                date_from = datetime.strptime(date_from_field.value.strip(), "%d.%m.%Y")
            if (date_to_field.value or "").strip():
                end_date = datetime.strptime(date_to_field.value.strip(), "%d.%m.%Y").date()
                date_to = datetime.combine(end_date + timedelta(days=1), time.min)
            if date_from and date_to and date_from >= date_to:
                raise ValueError("Начальная дата должна быть раньше конечной.")
        return date_from, date_to

    def update_transactions_ui(e=None):
        transactions_column.controls.clear()
        try:
            date_from, date_to = get_history_dates()
            order = SortOrder(sort_dropdown.value or SortOrder.DATE_DESC.value)
            filters = OperationFilter(date_from=date_from, date_to=date_to, sort_by=order)
            operations = repo.get_operations_for_ui(filters)
            operations = [
                op for op in operations
                if (income_checkbox.value and is_income_operation(op))
                or (expense_checkbox.value and not is_income_operation(op))
            ]
            operations.sort(key=safe_date, reverse=(order == SortOrder.DATE_DESC))

            if not operations:
                transactions_column.controls.append(
                    ft.Text("Нет операций за выбранный период", color=ft.Colors.AMBER_400)
                )
            else:
                grouped = {}
                for op in operations:
                    grouped.setdefault(safe_date(op).date(), []).append(op)
                for date_key in sorted(grouped, reverse=(order == SortOrder.DATE_DESC)):
                    if date_key == max_data_date.date():
                        heading = "Последняя дата в данных"
                    elif date_key == (max_data_date.date() - timedelta(days=1)):
                        heading = "Предыдущий день"
                    else:
                        heading = date_key.strftime("%d.%m.%Y")
                    transactions_column.controls.append(
                        ft.Text(heading, color="#38bdf8", weight=ft.FontWeight.BOLD)
                    )
                    for op in grouped[date_key]:
                        income = is_income_operation(op)
                        amount = float(getattr(op, "amount", 0) or 0)
                        counterparty = getattr(op, "counterparty_name", "") or "Без контрагента"
                        purpose = getattr(op, "payment_purpose", "") or "Без назначения"
                        transactions_column.controls.append(
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        ft.Column(
                                            controls=[
                                                ft.Text(counterparty, weight=ft.FontWeight.BOLD),
                                                ft.Text(
                                                    purpose,
                                                    size=12,
                                                    color="#a0aec0",
                                                    max_lines=1,
                                                    overflow=ft.TextOverflow.ELLIPSIS,
                                                ),
                                                ft.Text(safe_date(op).strftime("%H:%M"), size=11, color="#777777"),
                                            ],
                                            expand=True,
                                            spacing=3,
                                        ),
                                        ft.Text(
                                            f"{'+' if income else '-'}{money_fmt(amount)}",
                                            color=ft.Colors.GREEN_400 if income else ft.Colors.RED_400,
                                            weight=ft.FontWeight.BOLD,
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                bgcolor="#202635",
                                padding=12,
                                border_radius=8,
                            )
                        )
                    transactions_column.controls.append(ft.Divider(color="#222736"))

            # Регулярные платежи выводятся отдельным блоком, чтобы не смешивать
            # прогнозные события с фактически проведёнными банковскими операциями.
            transactions_column.controls.append(ft.Divider(color="#222736", height=20))
            transactions_column.controls.append(
                ft.Text(
                    "Ожидаемые регулярные платежи",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color="#fbbf24",
                )
            )
            transactions_column.controls.append(
                ft.Text(
                    "Прогноз на 90 дней от последней даты в загруженных данных.",
                    size=12,
                    color="#a0aec0",
                )
            )

            try:
                patterns = RecurringPaymentService.detect_recurring_operations(all_ops)
                print("Обнаруженные регулярные шаблоны:")
                for pattern in patterns:
                    print(
                        pattern.get("direction"),
                        pattern.get("counterparty_name"),
                        pattern.get("occurrences"),
                        pattern.get("average_amount"),
                    )
                patterns = [
                    pattern for pattern in patterns
                    if str(pattern.get("direction", "")).lower()
                    in ("expense", "расход", "outgoing", "debit")
                ]
                recurring_events = RecurringPaymentService.generate_future_obligations(
                    patterns,
                    max_data_date,
                    horizon_days=90,
                )

                recurring_events = [
                    event for event in recurring_events
                    if (
                        income_checkbox.value
                        and str(event.get("direction", "")).strip().upper()
                        in {"IN", "INCOME", "ПРИХОД", "ПОСТУПЛЕНИЕ"}
                    ) or (
                        expense_checkbox.value
                        and str(event.get("direction", "")).strip().upper()
                        in {"OUT", "EXPENSE", "РАСХОД", "СПИСАНИЕ"}
                    )
                ]

                def recurring_date(event):
                    value = event.get("date")
                    return value.date() if isinstance(value, datetime) else value

                # При фильтре по конкретным датам ограничиваем прогноз этим диапазоном.
                if filter_mode["value"] == "date":
                    if date_from:
                        recurring_events = [
                            event for event in recurring_events
                            if recurring_date(event) >= date_from.date()
                        ]
                    if date_to:
                        recurring_events = [
                            event for event in recurring_events
                            if recurring_date(event) < date_to.date()
                        ]

                if not recurring_events:
                    transactions_column.controls.append(
                        ft.Text(
                            "Регулярные платежи не обнаружены или нет ожидаемых платежей "
                            "в выбранном диапазоне.",
                            color="#a0aec0",
                        )
                    )
                else:
                    recurring_groups = {}
                    for event in recurring_events:
                        recurring_groups.setdefault(recurring_date(event), []).append(event)

                    for event_date in sorted(
                        recurring_groups,
                        reverse=(order == SortOrder.DATE_DESC),
                    ):
                        transactions_column.controls.append(
                            ft.Text(
                                event_date.strftime("%d.%m.%Y"),
                                color="#fbbf24",
                                weight=ft.FontWeight.BOLD,
                            )
                        )
                        for event in recurring_groups[event_date]:
                            direction = str(event.get("direction", "")).strip().upper()
                            is_income = direction in {
                                "IN", "INCOME", "ПРИХОД", "ПОСТУПЛЕНИЕ"
                            }
                            amount = float(event.get("amount", 0) or 0)
                            counterparty = event.get("counterparty_name") or "Без контрагента"
                            description = event.get("description") or "Регулярный платёж"

                            transactions_column.controls.append(
                                ft.Container(
                                    content=ft.Row(
                                        controls=[
                                            ft.Column(
                                                controls=[
                                                    ft.Text(
                                                        counterparty,
                                                        weight=ft.FontWeight.BOLD,
                                                    ),
                                                    ft.Text(
                                                        description,
                                                        size=12,
                                                        color="#a0aec0",
                                                        max_lines=2,
                                                        overflow=ft.TextOverflow.ELLIPSIS,
                                                    ),
                                                    ft.Text(
                                                        "ПРОГНОЗ · "
                                                        + ("Поступление" if is_income else "Списание"),
                                                        size=11,
                                                        color="#fbbf24",
                                                    ),
                                                ],
                                                expand=True,
                                                spacing=3,
                                            ),
                                            ft.Text(
                                                f"{'+' if is_income else '-'}{money_fmt(amount)}",
                                                color=(
                                                    ft.Colors.GREEN_400
                                                    if is_income else ft.Colors.RED_400
                                                ),
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                        ],
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    ),
                                    bgcolor="#292a30",
                                    padding=12,
                                    border_radius=8,
                                    border=ft.Border(
                                        left=ft.BorderSide(3, "#fbbf24")
                                    ),
                                )
                            )
            except Exception as recurring_exc:
                print(f"Ошибка отображения регулярных платежей: {recurring_exc}")
                traceback.print_exc()
                transactions_column.controls.append(
                    ft.Text(
                        f"Не удалось загрузить регулярные платежи: {recurring_exc}",
                        color=ft.Colors.AMBER_400,
                    )
                )
        except ValueError as exc:
            transactions_column.controls.append(
                ft.Text(f"Проверьте фильтр дат: {exc}", color=ft.Colors.AMBER_400)
            )
        except Exception as exc:
            transactions_column.controls.append(
                ft.Text(f"Ошибка истории: {exc}", color=ft.Colors.RED_400)
            )
            traceback.print_exc()
        page.update()

    def toggle_filters(e):
        filter_panel.visible = not filter_panel.visible
        page.update()

    def set_period_mode(e):
        filter_mode["value"] = "period"
        update_transactions_ui()

    def set_date_mode(e):
        filter_mode["value"] = "date"
        update_transactions_ui()

    filter_panel.content = ft.Column(
        controls=[
            ft.Row([income_checkbox, expense_checkbox], wrap=True),
            ft.Row(
                controls=[
                    ft.FilledButton("По периоду", on_click=set_period_mode),
                    ft.FilledButton("По датам", on_click=set_date_mode),
                    sort_dropdown,
                ],
                wrap=True,
            ),
            ft.Row(
                controls=[
                    period_radio,
                    ft.FilledButton("Применить период", on_click=update_transactions_ui),
                ],
                wrap=True,
            ),
            ft.Row(
                controls=[
                    date_from_field,
                    date_to_field,
                    ft.FilledButton("Применить даты", on_click=update_transactions_ui),
                ],
                wrap=True,
            ),
        ],
        spacing=10,
    )

    # Панель обнаруженных регулярных платежей
    recurring_drawer_open = {"value": False}

    def build_recurring_drawer():
        items = []

        try:
            patterns = RecurringPaymentService.detect_recurring_operations(all_ops)

            events = RecurringPaymentService.generate_future_obligations(
                patterns,
                max_data_date,
                horizon_days=90,
            )
            
            items.append(
                ft.Text(
                    f"Обнаружено регулярных шаблонов: {len(patterns)}",
                    size=13,
                    color="#a0aec0",
                )
            )

            if not events:
                items.append(
                    ft.Text(
                        "Предстоящие регулярные платежи не найдены.",
                        color="#a0aec0",
                    )
                )

            for event in sorted(
                events,
                key=lambda item: item.get("date") or datetime.max,
            ):
                event_date = event.get("date")
                if isinstance(event_date, datetime):
                    date_label = event_date.strftime("%d.%m.%Y")
                elif event_date:
                    date_label = str(event_date)
                else:
                    date_label = "Дата не указана"

                direction = str(
                    event.get("direction", "")
                ).strip().upper()

                is_income = direction in {
                    "IN", "INCOME", "ПРИХОД", "ПОСТУПЛЕНИЕ"
                }

                amount = float(event.get("amount", 0) or 0)
                name = (
                    event.get("counterparty_name")
                    or "Без контрагента"
                )
                description = (
                    event.get("description")
                    or "Регулярный платёж"
                )

                items.append(
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text(
                                    name,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Text(
                                    description,
                                    size=12,
                                    color="#a0aec0",
                                ),
                                ft.Text(
                                    date_label + " · прогноз",
                                    size=11,
                                    color="#fbbf24",
                                ),
                                ft.Text(
                                    f"{'+' if is_income else '-'}"
                                    f"{money_fmt(amount)}",
                                    color=(
                                        ft.Colors.GREEN_400
                                        if is_income
                                        else ft.Colors.RED_400
                                    ),
                                    weight=ft.FontWeight.BOLD,
                                ),
                            ],
                            spacing=5,
                        ),
                        bgcolor="#202635",
                        padding=12,
                        border_radius=8,
                        border=ft.Border(
                            left=ft.BorderSide(3, "#fbbf24")
                        ),
                    )
                )

        except Exception as exc:
            traceback.print_exc()
            items.append(
                ft.Text(
                    f"Не удалось загрузить платежи: {exc}",
                    color=ft.Colors.RED_400,
                )
            )

        return ft.Column(
            controls=items,
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def toggle_recurring_drawer(e=None):
        recurring_drawer_open["value"] = not recurring_drawer_open["value"]

        recurring_drawer.visible = recurring_drawer_open["value"]

        if recurring_drawer_open["value"]:
            recurring_drawer.content = ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text(
                                "Регулярные платежи",
                                size=18,
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                tooltip="Закрыть",
                                on_click=toggle_recurring_drawer,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Text(
                        "Ожидаемые платежи на ближайшие 90 дней",
                        size=12,
                        color="#a0aec0",
                    ),
                    ft.Divider(color="#303747"),
                    build_recurring_drawer(),
                ],
                spacing=12,
                expand=True,
            )

        page.update()

    recurring_banner = ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(
                    ft.Icons.EVENT_REPEAT,
                    color="#fbbf24",
                    size=24,
                ),
                ft.Column(
                    controls=[
                        ft.Text(
                            "Обнаружены регулярные платежи",
                            size=15,
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text(
                            "Нажмите, чтобы посмотреть список",
                            size=12,
                            color="#a0aec0",
                        ),
                    ],
                    spacing=3,
                    expand=True,
                ),
                ft.Icon(
                    ft.Icons.CHEVRON_RIGHT,
                    color="#fbbf24",
                ),
            ],
            spacing=10,
        ),
        bgcolor="#292a30",
        padding=14,
        border_radius=12,
        border=ft.Border(
            left=ft.BorderSide(4, "#fbbf24")
        ),
        on_click=toggle_recurring_drawer,
        ink=True,
    )

    recurring_drawer = ft.Container(
        width=360,
        padding=16,
        bgcolor="#1a1f2c",
        visible=False,
    )

    history_view = ft.Column(
        controls=[
            ft.Row(
                controls=[
                    ft.Text(
                        "История операций",
                        size=24,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.FILTER_LIST,
                        tooltip="Фильтры",
                        on_click=toggle_filters,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            recurring_banner,
            filter_panel,
            ft.Divider(color="#222736"),
            ft.Container(
                content=transactions_column,
                expand=True,
            ),
        ],
        expand=True,
        spacing=10,
    )

    # Вкладка 3 — прогноз
    main_chart_container = ft.Container(height=320)
    balance_chart_container = ft.Container(height=220)
    forecast_chart_container = ft.Container(height=220)
    scenarios_chart_container = ft.Container(height=260)

    risk_score_text = ft.Text("— / 100", size=22, weight=ft.FontWeight.BOLD)
    risk_level_text = ft.Text("Уровень: неизвестен", size=12)
    risk_date_text = ft.Text("Нет доступного прогноза", size=15)
    minimum_balance_text = ft.Text("—", size=22, weight=ft.FontWeight.BOLD)
    deficit_text = ft.Text("—", size=22, weight=ft.FontWeight.BOLD)
    reason_text = ft.Text("Ожидание данных...", size=13, color="#a0aec0")
    status_text = ft.Text("", size=12, color="#a0aec0")

    period_buttons = ft.Row(spacing=6, wrap=True)
    horizon_buttons = ft.Row(spacing=6, wrap=True)
    period_controls = {}
    horizon_controls = {}

    def clear_prediction(message):
        risk_score_text.value = "— / 100"
        risk_level_text.value = "Уровень: неизвестен"
        risk_date_text.value = "Нет доступного прогноза"
        minimum_balance_text.value = "—"
        deficit_text.value = "—"
        reason_text.value = message
        status_text.value = ""
        for container in (
            main_chart_container,
            balance_chart_container,
            forecast_chart_container,
            scenarios_chart_container,
        ):
            container.content = ft.Text(message, color="#a0aec0")
        page.update()

    def make_line_chart(series, values, point_count):
        min_y, max_y = padded_range(values)
        return fch.LineChart(
            data_series=series,
            border=ft.Border(bottom=ft.BorderSide(1, "#38ffffff")),
            min_x=0,
            max_x=max(point_count - 1, 1),
            min_y=min_y,
            max_y=max_y,
        )

    def update_period_styles():
        for name, control in period_controls.items():
            selected = name == state["selected_period"]
            control.bgcolor = "#1e3a8a" if selected else "#252b3b"
            control.content.weight = ft.FontWeight.BOLD if selected else ft.FontWeight.NORMAL

    def update_horizon_styles():
        for days, control in horizon_controls.items():
            selected = days == state["forecast_days"]
            control.bgcolor = "#1e3a8a" if selected else "#252b3b"
            control.content.weight = ft.FontWeight.BOLD if selected else ft.FontWeight.NORMAL

    def update_prediction(e=None):
        if not all_ops:
            clear_prediction(
                f"Нет операций для анализа. {load_error or ''}".strip()
            )
            return

        period = state["selected_period"]
        horizon = state["forecast_days"]
        try:
            result = PredictionService.predict_future(
                all_ops,
                period=period,
                days_ahead=horizon,
            )

            print("\n=== ДИАГНОСТИКА ПРОГНОЗА ===")
            print("Период:", period, "| Горизонт:", horizon)
            print("Доступен:", result.get("is_available"))
            print("История:", len(result.get("history", [])))
            print("Реалистичный:", len(result.get("realistic", [])))
            print("Оптимистичный:", len(result.get("optimistic", [])))
            print("Пессимистичный:", len(result.get("pessimistic", [])))
            print("Причина:", result.get("reason"))

            if not result.get("is_available"):
                clear_prediction(result.get("reason") or f"Недостаточно данных для периода «{period}».")
                return

            history = result.get("history") or []
            realistic = result.get("realistic") or []
            optimistic = result.get("optimistic") or []
            pessimistic = result.get("pessimistic") or []

            if not history:
                clear_prediction("Сервис не вернул исторические значения баланса.")
                return

            risk_score_text.value = f"{result.get('risk_score', '—')} / 100"
            risk_level_text.value = f"Уровень: {result.get('risk_level', 'неизвестен')}"

            risk_date = result.get("risk_date")
            if risk_date:
                risk_date_text.value = (
                    f"Через {result.get('days_to_risk', '—')} дн. "
                    f"({risk_date.strftime('%d.%m.%Y')})"
                )
                risk_date_text.color = ft.Colors.RED_400
            else:
                risk_date_text.value = "В ближайшем горизонте риск не выявлен"
                risk_date_text.color = ft.Colors.GREEN_400

            minimum_balance_text.value = money_fmt(result.get("minimum_forecast_balance"))
            deficit = float(result.get("estimated_deficit", 0) or 0)
            deficit_text.value = money_fmt(deficit)
            deficit_text.color = ft.Colors.RED_400 if deficit > 0 else ft.Colors.GREEN_400
            reason_text.value = result.get("reason") or "Оценка рассчитана по историческим данным."

            hist_values = [float(value) for _, value in history]
            hist_points = [
                fch.LineChartDataPoint(
                    i, float(value),
                    tooltip=f"История {date:%d.%m.%Y}: {money_fmt(value)}",
                )
                for i, (date, value) in enumerate(history)
            ]
            series_main = [
                fch.LineChartData(
                    points=hist_points,
                    color=ft.Colors.BLUE_400,
                    stroke_width=2.5,
                    curved=True,
                )
            ]
            all_values = list(hist_values)
            offset = len(history)

            if state["show_forecast"] and realistic:
                last_balance = float(history[-1][1])
                real_points = [fch.LineChartDataPoint(offset - 1, last_balance)]
                for i, (date, value) in enumerate(realistic):
                    real_points.append(
                        fch.LineChartDataPoint(
                            offset + i, float(value),
                            tooltip=f"Реалистичный {date:%d.%m.%Y}: {money_fmt(value)}",
                        )
                    )
                series_main.append(
                    fch.LineChartData(
                        points=real_points,
                        color=ft.Colors.AMBER_400,
                        stroke_width=2.5,
                        curved=True,
                    )
                )
                for scenario, color in (
                    (optimistic, ft.Colors.GREEN_400),
                    (pessimistic, ft.Colors.RED_400),
                ):
                    if scenario:
                        points = [fch.LineChartDataPoint(offset - 1, last_balance)]
                        points.extend(
                            fch.LineChartDataPoint(offset + i, float(value))
                            for i, (_, value) in enumerate(scenario)
                        )
                        series_main.append(
                            fch.LineChartData(points=points, color=color, stroke_width=1.5)
                        )
                all_values.extend(float(value) for scenario in (realistic, optimistic, pessimistic) for _, value in scenario)

            main_chart_container.content = make_line_chart(
                series_main, all_values, len(history) + (horizon if state["show_forecast"] else 0)
            )

            # Панель 1: история баланса
            balance_chart_container.content = make_line_chart(
                [
                    fch.LineChartData(
                        points=hist_points,
                        color=ft.Colors.GREEN_400,
                        stroke_width=2,
                        curved=True,
                    )
                ],
                hist_values,
                len(hist_points),
            )

            # Панель 2: последние 30 исторических точек + реалистичный сценарий
            window = history[-30:]
            window_points = [
                fch.LineChartDataPoint(i, float(value))
                for i, (_, value) in enumerate(window)
            ]
            forecast_series = [
                fch.LineChartData(points=window_points, color=ft.Colors.BLUE_400, stroke_width=2)
            ]
            panel2_values = [float(value) for _, value in window]
            if realistic:
                last_x = len(window) - 1
                future_points = [fch.LineChartDataPoint(last_x, float(window[-1][1]))]
                for i, (_, value) in enumerate(realistic, start=1):
                    future_points.append(fch.LineChartDataPoint(last_x + i, float(value)))
                forecast_series.append(
                    fch.LineChartData(points=future_points, color=ft.Colors.AMBER_400, stroke_width=2)
                )
                panel2_values.extend(float(value) for _, value in realistic)
            forecast_chart_container.content = make_line_chart(
                forecast_series, panel2_values, len(window) + len(realistic)
            )

            # Панель 3: оба сценария. Если их нет — показываем причину вместо пустого графика.
            if optimistic and pessimistic:
                opt_points = [
                    fch.LineChartDataPoint(i, float(value))
                    for i, (_, value) in enumerate(optimistic)
                ]
                pess_points = [
                    fch.LineChartDataPoint(i, float(value))
                    for i, (_, value) in enumerate(pessimistic)
                ]
                scenario_values = [float(value) for _, value in optimistic]
                scenario_values += [float(value) for _, value in pessimistic]
                scenarios_chart_container.content = make_line_chart(
                    [
                        fch.LineChartData(
                            points=opt_points,
                            color=ft.Colors.GREEN_400,
                            stroke_width=2,
                            curved=True,
                        ),
                        fch.LineChartData(
                            points=pess_points,
                            color=ft.Colors.RED_400,
                            stroke_width=2,
                            curved=True,
                        ),
                    ],
                    scenario_values,
                    max(len(optimistic), len(pessimistic)),
                )
                status_text.value = (
                    f"Сценарии построены: реалистичный — {len(realistic)}, "
                    f"оптимистичный — {len(optimistic)}, пессимистичный — {len(pessimistic)} точек."
                )
            else:
                missing = []
                if not realistic:
                    missing.append("реалистичный")
                if not optimistic:
                    missing.append("оптимистичный")
                if not pessimistic:
                    missing.append("пессимистичный")
                scenarios_chart_container.content = ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=28, color=ft.Colors.AMBER_400),
                            ft.Text("Сценарии не сформированы сервисом", weight=ft.FontWeight.BOLD),
                            ft.Text(
                                "Отсутствуют данные: " + ", ".join(missing) + ". "
                                "Проверьте возвращаемые ключи и логи PredictionService.",
                                color="#a0aec0",
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    alignment=ft.alignment.center,
                    expand=True,
                )
                status_text.value = "Сценарии отсутствуют в ответе PredictionService."

        except Exception as exc:
            traceback.print_exc()
            clear_prediction(f"Не удалось рассчитать прогноз: {exc}")
        page.update()

    def select_period(name):
        try:
            _, available = PredictionService.filter_operations_by_period(all_ops, name)
            if not available:
                show_message(f"Недостаточно данных для периода «{name}».")
                return
        except Exception:
            # Если сервис не поддерживает проверку периода, пусть сам вернёт диагностический ответ.
            pass
        state["selected_period"] = name
        update_period_styles()
        update_prediction()

    def select_horizon(days):
        state["forecast_days"] = days
        update_horizon_styles()
        update_prediction()

    for name in ["Неделя", "Месяц", "Год", "Два года", "Всё время"]:
        button = ft.Container(
            content=ft.Text(name, size=12, color="#ffffff"),
            bgcolor="#252b3b",
            padding=9,
            border_radius=6,
            on_click=lambda e, value=name: select_period(value),
        )
        period_controls[name] = button
        period_buttons.controls.append(button)

    for label, days in [("7 дней", 7), ("30 дней", 30), ("90 дней", 90), ("180 дней", 180)]:
        button = ft.Container(
            content=ft.Text(label, size=12, color="#ffffff"),
            bgcolor="#252b3b",
            padding=9,
            border_radius=6,
            on_click=lambda e, value=days: select_horizon(value),
        )
        horizon_controls[days] = button
        horizon_buttons.controls.append(button)

    def toggle_forecast(e):
        state["show_forecast"] = bool(e.control.value)
        update_prediction()

    forecast_checkbox = ft.Checkbox(
        label="Показывать прогноз на главном графике",
        value=True,
        on_change=toggle_forecast,
    )

    prediction_view = ft.ListView(
        controls=[
            ft.Text("Интеллектуальный прогноз и анализ рисков", size=24, weight=ft.FontWeight.BOLD),
            ft.Text(f"Сценарное моделирование для {company_id}", size=14, color="#a0aec0"),
            ft.ResponsiveRow(
                controls=[
                    ft.Column(
                        [section_card("Индекс риска", ft.Column([risk_score_text, risk_level_text], spacing=6))],
                        col={"xs": 12, "sm": 6, "md": 3},
                    ),
                    ft.Column(
                        [section_card("Дата риска", risk_date_text)],
                        col={"xs": 12, "sm": 6, "md": 3},
                    ),
                    ft.Column(
                        [section_card("Минимальный прогнозный остаток", minimum_balance_text)],
                        col={"xs": 12, "sm": 6, "md": 3},
                    ),
                    ft.Column(
                        [section_card("Оценка дефицита", deficit_text)],
                        col={"xs": 12, "sm": 6, "md": 3},
                    ),
                ],
                spacing=10,
                run_spacing=10,
            ),
            section_card("Причина / оценка", reason_text),
            ft.Text("Период истории:", size=13, color="#a0aec0"),
            period_buttons,
            ft.Text("Горизонт прогноза:", size=13, color="#a0aec0"),
            horizon_buttons,
            forecast_checkbox,
            status_text,
            section_card("История и сценарии прогноза", main_chart_container, height=380),
            ft.ResponsiveRow(
                controls=[
                    ft.Column([section_card("1. Фактический баланс", balance_chart_container, height=300)], col={"xs": 12, "md": 6}),
                    ft.Column([section_card("2. Прогноз баланса", forecast_chart_container, height=300)], col={"xs": 12, "md": 6}),
                    ft.Column([section_card("3. Оптимистичный и пессимистичный сценарии", scenarios_chart_container, height=340)], col={"xs": 12, "md": 12}),
                ],
                spacing=12,
                run_spacing=12,
            ),
        ],
        expand=True,
        padding=20,
        spacing=12,
    )

    # Вкладка 4 — настройки
    settings_view = ft.ListView(
        controls=[
            ft.Text("Настройки", size=24, weight=ft.FontWeight.BOLD),
            ft.Text("Раздел находится в разработке.", size=14, color="#a0aec0"),
        ],
        expand=True,
        padding=20,
    )

    content_area = ft.Container(content=dashboard_view, expand=True)

    def change_tab(e):
        index = e.control.selected_index
        if index == 0:
            content_area.content = dashboard_view
        elif index == 1:
            update_transactions_ui()
            content_area.content = history_view
        elif index == 2:
            content_area.content = prediction_view
            update_prediction()
        else:
            content_area.content = settings_view
        page.update()

    navigation = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=100,
        min_extended_width=180,
        bgcolor="#161922",
        destinations=[
            ft.NavigationRailDestination(icon=ft.Icons.HOME, label="Главная"),
            ft.NavigationRailDestination(icon=ft.Icons.HISTORY, label="История"),
            ft.NavigationRailDestination(icon=ft.Icons.TRENDING_UP, label="Прогноз"),
            ft.NavigationRailDestination(icon=ft.Icons.SETTINGS, label="Настройки"),
        ],
        on_change=change_tab,
    )

    page.add(
        ft.Row(
            controls=[
                navigation,
                ft.VerticalDivider(width=1, color="#222736"),
                content_area,
                recurring_drawer,
            ],
            expand=True,
            spacing=0,
        )
    )

    update_period_styles()
    update_horizon_styles()
    update_transactions_ui()
    update_prediction()


ft.run(main)
