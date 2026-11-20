from decimal import Decimal, InvalidOperation
from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()


@register.filter
def money(value):
    try:
        amount = Decimal(value or 0)
        if not amount.is_finite():
            return '—'
        places = 0 if amount == amount.to_integral() else 2
        return (
            format(amount, f',.{places}f').replace(',', '_').replace('.', ',').replace('_', '.')
            + '₫'
        )
    except (InvalidOperation, TypeError, ValueError):
        return '—'


@register.filter
def get_item(mapping, key):
    return mapping.get(key)


@register.filter
def as_string(value):
    return str(value)


@register.simple_tag
def icon(name):
    names = {
        'search': '8-2-imgSvg1.svg',
        'external': '8-2-imgSvg2.svg',
        'calendar': '8-2-imgSvg3.svg',
        'chevron': '8-2-imgSvg4.svg',
        'user': '8-2-imgSvg5.svg',
        'map': '8-2-imgSvg6.svg',
        'truck': '8-2-imgSvg7.svg',
        'check': '8-2-imgSvg8.svg',
        'print': '8-2-imgSvg9.svg',
        'close': '8-2-imgSvg10.svg',
        'logout': '8-2-imgSvg.svg',
    }
    names.update(
        {
            'orders': '6-930-imgSvg1.svg',
            'cart': '6-930-imgSvg2.svg',
            'lock': '6-930-imgSvg6.svg',
            'customer_logout': '6-930-imgSvg7.svg',
            'chat': '6-930-imgSvg15.svg',
            'facebook': '6-930-imgSvg13.svg',
            'instagram': '6-930-imgSvg14.svg',
            'camera': '8-366-imgSvg6.svg',
            'shield': '8-366-imgSvg4.svg',
        }
    )
    filename = names.get(name)
    return (
        format_html(
            '<img class="ui-icon" src="{}" alt="" aria-hidden="true">',
            static('orders/assets/figma/' + filename),
        )
        if filename
        else ''
    )
