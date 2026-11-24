from inventory.forms import PurchaseReceivingForm, StocktakeAdjustmentForm
from inventory.services import generate_adjustment_token, generate_receiving_token

from .common import parse_positive_id


def _attach_action_tokens(inventory, actor_id):
    inventory.recv_token, _ = generate_receiving_token(inventory_id=inventory.pk, actor_id=actor_id)
    inventory.adj_token, _ = generate_adjustment_token(inventory=inventory, actor_id=actor_id)


def build_stock_action_context(request, inventories, base_queryset):
    selected_id = (
        parse_positive_id(request.GET.get('selected', ''))
        or parse_positive_id(request.GET.get('inventory_id', ''))
    )
    selected = None
    for inventory in inventories:
        _attach_action_tokens(inventory, request.user.pk)
        if inventory.pk == selected_id:
            selected = inventory
    if selected_id and selected is None:
        selected = base_queryset.filter(pk=selected_id).first()
        if selected:
            _attach_action_tokens(selected, request.user.pk)
    if selected is None and inventories:
        selected = inventories[0]
    return {
        'selected_inventory': selected,
        'receive_form': PurchaseReceivingForm(initial={
            'request_token': selected.recv_token if selected else '',
        }),
        'adjust_form': StocktakeAdjustmentForm(initial={
            'counted_quantity': selected.physical_sellable if selected else 0,
            'request_token': selected.adj_token if selected else '',
        }),
    }
