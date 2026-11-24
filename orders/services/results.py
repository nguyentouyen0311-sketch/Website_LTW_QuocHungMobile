from typing import NamedTuple, Optional
from inventory.models import InventoryTransaction
from orders.models import Order, OrderCancellation, OrderStatusHistory
from payments.models import Payment


class OrderCreationResult(NamedTuple):
    order: Order
    is_replay: bool


class CancelOrderResult(NamedTuple):
    order: Order
    cancellation: OrderCancellation
    history_entry: OrderStatusHistory
    is_replay: bool


class ConfirmOrderResult(NamedTuple):
    order: Order
    payment: Payment
    history_entry: Optional[OrderStatusHistory]
    already_confirmed: bool = False


class ShipOrderResult(NamedTuple):
    order: Order
    payment: Payment
    history_entry: Optional[OrderStatusHistory]
    transactions: list[InventoryTransaction]
    already_shipped: bool = False


class DeliverOrderResult(NamedTuple):
    order: Order
    payment: Payment
    history_entry: Optional[OrderStatusHistory]
    already_delivered: bool = False
