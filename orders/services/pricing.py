from decimal import Decimal
from typing import Optional
from coupons.models import Coupon


def _allocate_coupon_discount(
    evaluated_lines: list,
    coupon: Coupon,
    total_discount: Decimal,
    eligible_product_ids: Optional[set] = None,
) -> dict:
    count = len(evaluated_lines)
    if total_discount <= Decimal('0.00'):
        return {i: Decimal('0.00') for i in range(count)}

    eligible_indices = []
    for i, line in enumerate(evaluated_lines):
        is_eligible = coupon.applies_to_all or (
            eligible_product_ids is not None and line['product'].id in eligible_product_ids
        )
        if is_eligible and line['line_total'] > Decimal('0.00'):
            eligible_indices.append(i)

    if not eligible_indices:
        return {i: Decimal('0.00') for i in range(count)}

    eligible_subtotal = sum((evaluated_lines[i]['line_total'] for i in eligible_indices), Decimal('0.00'))
    if eligible_subtotal <= Decimal('0.00'):
        return {i: Decimal('0.00') for i in range(count)}

    total_discount_cents = int((total_discount * Decimal('100')).to_integral_value())

    base_cents_map = {}
    remainders_map = {}

    for i in eligible_indices:
        line_gross = evaluated_lines[i]['line_total']
        exact_cents = (line_gross * Decimal(total_discount_cents)) / eligible_subtotal
        floor_cents = int(exact_cents)
        base_cents_map[i] = floor_cents
        remainders_map[i] = exact_cents - Decimal(floor_cents)

    allocated_sum = sum(base_cents_map.values())
    remaining_cents = total_discount_cents - allocated_sum

    if remaining_cents > 0:
        sorted_indices = sorted(
            eligible_indices,
            key=lambda idx: (
                -remainders_map[idx],
                evaluated_lines[idx]['variant'].id,
                evaluated_lines[idx]['item'].id,
            )
        )
        for idx in range(min(remaining_cents, len(sorted_indices))):
            target_i = sorted_indices[idx]
            base_cents_map[target_i] += 1

    allocated_discounts = {}
    for i in range(count):
        if i in base_cents_map:
            cents = base_cents_map[i]
            disc = Decimal(cents) / Decimal('100')
            disc = max(Decimal('0.00'), min(disc, evaluated_lines[i]['line_total']))
            allocated_discounts[i] = disc
        else:
            allocated_discounts[i] = Decimal('0.00')

    return allocated_discounts
