"""Shared helpers for inventory views."""

from accounts.permissions import is_admin, is_inventory_staff, is_sales_staff
from urllib.parse import parse_qsl, urlencode, urlsplit

from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def parse_positive_id(val, max_digits=12):
    if not isinstance(val, str):
        return None
    val = val.strip()
    if not val or len(val) > max_digits:
        return None
    if not (val.isascii() and val.isdigit()):
        return None
    try:
        parsed = int(val)
        return parsed if parsed > 0 else None
    except ValueError:
        return None


def _get_inventory_return_url(request, inventory_id):
    next_url = request.POST.get('next', '').strip()
    if not next_url or not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return None
    parsed = urlsplit(next_url)
    list_url = reverse('inventory:inventory_list')
    if parsed.path != list_url:
        return None
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params['selected'] = str(inventory_id)
    return f'{list_url}?{urlencode(params)}'


def _build_inventory_form_context(inventory, form, request_token, user):
    return {
        'inventory': inventory,
        'variant': inventory.variant,
        'product': inventory.variant.product if inventory.variant else None,
        'form': form,
        'request_token': request_token,
        'active_nav': 'inventory_list',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(user),
        'is_sales_staff': is_sales_staff(user),
        'is_inventory_staff': is_inventory_staff(user),
    }


def _add_validation_error_to_form(form, exc):
    if hasattr(exc, 'message_dict'):
        for field, errs in exc.message_dict.items():
            for err in errs:
                if field in form.fields:
                    form.add_error(field, err)
                else:
                    form.add_error(None, err)
    else:
        for err in exc.messages:
            form.add_error(None, err)
