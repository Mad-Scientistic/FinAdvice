from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import re
from typing import Any

from dto.OperationModels import StoredOperation
from dto.FutureCashflowModels import (
    CashflowEvent,
    ExternalInvoice,
    ExternalTaxObligation,
)
from service.RecurringPaymentService import (
    RecurringPaymentService,
    normalize_direction,
)


class FutureCashflowCalendarService:
    """
    Формирует календарь будущих регулярных операций, счетов и налогов.

    Неоплаченные просроченные счета/налоги сохраняются в выдаче и помечаются
    как overdue. Налоговые обязательства не дедуплицируются автоматически
    с банковскими операциями.
    """

    @staticmethod
    def _as_date(value: date | datetime) -> date:
        return value.date() if isinstance(value, datetime) else value

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").casefold()).strip()

    @classmethod
    def _invoice_matches_recurring(
        cls,
        invoice: ExternalInvoice,
        recurring: dict[str, Any],
        date_tolerance_days: int = 5,
        amount_tolerance_pct: Decimal = Decimal("0.10"),
    ) -> bool:
        try:
            invoice_direction = normalize_direction(invoice.direction)
            recurring_direction = normalize_direction(recurring["direction"])
        except ValueError:
            return False

        if invoice_direction != recurring_direction:
            return False

        inv_name = cls._norm(invoice.counterparty_name)
        rec_name = cls._norm(recurring.get("counterparty_name", ""))
        if not inv_name or not rec_name or inv_name != rec_name:
            return False

        recurring_date = cls._as_date(recurring["date"])
        if abs((invoice.due_date - recurring_date).days) > date_tolerance_days:
            return False

        recurring_amount = abs(Decimal(str(recurring["amount"])))
        invoice_amount = abs(Decimal(str(invoice.amount)))
        if invoice_amount == 0:
            return False

        return (
            abs(invoice_amount - recurring_amount) / invoice_amount
            <= amount_tolerance_pct
        )

    @classmethod
    def build_calendar(
        cls,
        operations: list[StoredOperation],
        invoices: list[ExternalInvoice] | None = None,
        taxes: list[ExternalTaxObligation] | None = None,
        horizon_days: int = 30,
        as_of: date | datetime | None = None,
    ) -> dict[str, Any]:
        if horizon_days < 0:
            raise ValueError("horizon_days должен быть >= 0")

        invoices = invoices or []
        taxes = taxes or []

        if as_of is None:
            as_of_date = (
                max(cls._as_date(op.operation_date) for op in operations)
                if operations
                else date.today()
            )
        else:
            as_of_date = cls._as_date(as_of)

        end_date = as_of_date + timedelta(days=horizon_days)

        patterns = RecurringPaymentService.detect_recurring_operations(operations)
        recurring_raw = RecurringPaymentService.generate_future_obligations(
            patterns,
            as_of_date,
            horizon_days=horizon_days,
        )

        # Не исключаем неоплаченные обязательства только потому, что их срок прошёл.
        # Просроченные события будут иметь исходную дату и явный статус.
        relevant_invoices = [invoice for invoice in invoices if invoice.due_date <= end_date]
        relevant_taxes = [tax for tax in taxes if tax.pay_date <= end_date]

        upcoming_invoices = [
            invoice for invoice in relevant_invoices if invoice.due_date > as_of_date
        ]

        kept_recurring: list[dict[str, Any]] = []
        suppressed_recurring: list[dict[str, Any]] = []

        for recurring in recurring_raw:
            matched_invoice = next(
                (
                    invoice for invoice in upcoming_invoices
                    if cls._invoice_matches_recurring(invoice, recurring)
                ),
                None,
            )
            if matched_invoice:
                suppressed_recurring.append({
                    "recurring_event_id": recurring.get("event_id"),
                    "invoice_id": matched_invoice.invoice_id,
                    "reason": "Похожий выставленный счёт имеет приоритет",
                })
            else:
                kept_recurring.append(recurring)

        events: list[CashflowEvent] = []

        for item in kept_recurring:
            event_date = cls._as_date(item["date"])
            events.append(CashflowEvent(
                event_id=item["event_id"],
                event_date=event_date,
                direction=normalize_direction(item["direction"]),
                amount=abs(Decimal(str(item["amount"]))),
                source_type="recurring",
                source_id=item.get("pattern_id", ""),
                counterparty_name=item.get("counterparty_name", ""),
                purpose=item.get("description", ""),
                category=item.get("category", "other"),
                status="forecast",
                confidence=float(item.get("confidence", 0.5)),
            ))

        for invoice in relevant_invoices:
            overdue = invoice.due_date <= as_of_date
            events.append(CashflowEvent(
                event_id=f"invoice:{invoice.invoice_id}",
                event_date=invoice.due_date,
                direction=normalize_direction(invoice.direction),
                amount=abs(Decimal(str(invoice.amount))),
                source_type="invoice",
                source_id=invoice.invoice_id,
                counterparty_name=invoice.counterparty_name,
                purpose=invoice.purpose,
                category="invoice",
                status="overdue" if overdue else "planned",
                confidence=1.0,
            ))

        for tax in relevant_taxes:
            overdue = tax.pay_date <= as_of_date
            events.append(CashflowEvent(
                event_id=f"tax:{tax.tax_id}",
                event_date=tax.pay_date,
                direction="OUT",
                amount=abs(Decimal(str(tax.amount))),
                source_type="tax",
                source_id=tax.tax_id,
                counterparty_name="",
                purpose=tax.tax_name,
                category="tax",
                status="overdue" if overdue else "planned",
                confidence=1.0,
            ))

        events.sort(
            key=lambda event: (
                event.event_date,
                event.source_type,
                event.event_id,
            )
        )

        totals: dict[date, dict[str, Decimal]] = {}
        for event in events:
            bucket = totals.setdefault(
                event.event_date,
                {"IN": Decimal("0.00"), "OUT": Decimal("0.00")},
            )
            direction = normalize_direction(event.direction)
            bucket[direction] += abs(Decimal(str(event.amount)))

        daily_totals = [
            {
                "date": event_date,
                "inflow": values["IN"].quantize(Decimal("0.01")),
                "outflow": values["OUT"].quantize(Decimal("0.01")),
                "net": (values["IN"] - values["OUT"]).quantize(Decimal("0.01")),
            }
            for event_date, values in sorted(totals.items())
        ]

        return {
            "as_of": as_of_date,
            "horizon_end": end_date,
            "patterns": patterns,
            "events": events,
            "daily_totals": daily_totals,
            "suppressed_recurring_events": suppressed_recurring,
            "overdue_events": [
                event for event in events if event.status == "overdue"
            ],
            "counts": {
                "patterns": len(patterns),
                "recurring_events": sum(e.source_type == "recurring" for e in events),
                "invoices": sum(e.source_type == "invoice" for e in events),
                "taxes": sum(e.source_type == "tax" for e in events),
                "overdue": sum(e.status == "overdue" for e in events),
                "total_events": len(events),
            },
        }
