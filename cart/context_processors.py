from django.db.models import Sum
from accounts.permissions import is_customer
from cart.models import Cart


def cart_badge(request):

    user = getattr(request, 'user', None)

    if user and user.is_authenticated:
        if not user.is_active or not is_customer(user):
            return {
                'cart_item_count': 0,
                'can_use_cart': False,
            }
        # Direct-checkout carts are temporary order workspaces, never the
        # customer's persistent shopping cart or its header badge.
        cart = Cart.objects.filter(user=user, is_direct_checkout=False).first()
        if cart:
            total = cart.items.aggregate(total=Sum('quantity'))['total']
            return {
                'cart_item_count': total if total is not None else 0,
                'can_use_cart': True,
            }
        return {
            'cart_item_count': 0,
            'can_use_cart': True,
        }

    session = getattr(request, 'session', None)
    if not session or not session.session_key:
        return {
            'cart_item_count': 0,
            'can_use_cart': True,
        }

    cart = Cart.objects.filter(session_key=session.session_key, user__isnull=True).first()
    if cart:
        total = cart.items.aggregate(total=Sum('quantity'))['total']
        return {
            'cart_item_count': total if total is not None else 0,
            'can_use_cart': True,
        }

    return {
        'cart_item_count': 0,
        'can_use_cart': True,
    }
