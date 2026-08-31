from datetime import datetime
from decimal import Decimal
from uuid import UUID

from smartstock_api.api.schemas import StrictModel
from smartstock_api.domain.inventory import StockCondition
from smartstock_api.domain.reporting import ReceiptSummary, ReorderSuggestion, StockSummary


class StockSummaryItemResponse(StrictModel):
    product_id: UUID
    sku: str
    product_name: str
    warehouse_id: UUID
    condition: StockCondition
    uom: str
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    incoming: Decimal
    inventory_value: Decimal
    updated_at: datetime

    @classmethod
    def from_domain(cls, item: StockSummary) -> "StockSummaryItemResponse":
        return cls(**{name: getattr(item, name) for name in cls.model_fields})


class StockSummaryResponse(StrictModel):
    items: list[StockSummaryItemResponse]
    generated_at: datetime


class ReorderSuggestionResponse(StrictModel):
    product_id: UUID
    sku: str
    product_name: str
    uom: str
    available: Decimal
    incoming: Decimal
    reorder_point: Decimal
    safety_stock: Decimal
    target_stock: Decimal
    suggested_quantity: Decimal
    updated_at: datetime

    @classmethod
    def from_domain(cls, item: ReorderSuggestion) -> "ReorderSuggestionResponse":
        return cls(**{name: getattr(item, name) for name in cls.model_fields})


class ReorderSuggestionsResponse(StrictModel):
    items: list[ReorderSuggestionResponse]
    generated_at: datetime


class ReceiptTodayItemResponse(StrictModel):
    receipt_id: UUID
    receipt_number: str
    purchase_order_id: UUID
    warehouse_id: UUID
    accepted_quantity: Decimal
    rejected_quantity: Decimal
    posted_at: datetime

    @classmethod
    def from_domain(cls, item: ReceiptSummary) -> "ReceiptTodayItemResponse":
        return cls(**{name: getattr(item, name) for name in cls.model_fields})


class ReceiptsTodayResponse(StrictModel):
    items: list[ReceiptTodayItemResponse]
    generated_at: datetime
