from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import OuterRef, Subquery

from catalog.models import ProductVariant


@dataclass(frozen=True)
class PriceSegment:
    key: str
    label: str
    minimum: Decimal
    maximum: Decimal | None
    include_maximum: bool = False
    include_minimum: bool = True


PRICE_SEGMENTS = (
    PriceSegment('under-10', 'Dưới 10 triệu', Decimal('0'), Decimal('10000000')),
    PriceSegment('10-15', 'Từ 10 - 15 triệu', Decimal('10000000'), Decimal('15000000')),
    PriceSegment('15-20', 'Từ 15 - 20 triệu', Decimal('15000000'), Decimal('20000000')),
    PriceSegment('20-25', 'Từ 20 - 25 triệu', Decimal('20000000'), Decimal('25000000')),
    PriceSegment('25-30', 'Từ 25 - 30 triệu', Decimal('25000000'), Decimal('30000000'), True),
    PriceSegment('over-30', 'Trên 30 triệu', Decimal('30000000'), None, include_minimum=False),
)


def _price_filter_url(request, segment_key=None):
    params = request.GET.copy()
    params.pop('price', None)
    params.pop('page', None)
    if segment_key:
        params['price'] = segment_key
    query = params.urlencode()
    return f'{request.path}?{query}' if query else request.path


def apply_catalog_price_filter(request, products):
    selected = next(
        (segment for segment in PRICE_SEGMENTS if segment.key == request.GET.get('price')),
        None,
    )
    if selected:
        card_variant = ProductVariant.objects.filter(
            product_id=OuterRef('pk'), is_active=True,
        ).order_by('id')
        lower_lookup = 'card_price__gte' if selected.include_minimum else 'card_price__gt'
        products = products.annotate(
            card_price=Subquery(card_variant.values('price')[:1]),
        ).filter(**{lower_lookup: selected.minimum})
        if selected.maximum is not None:
            upper_lookup = 'card_price__lte' if selected.include_maximum else 'card_price__lt'
            products = products.filter(**{upper_lookup: selected.maximum})

    category_params = {}
    search_query = ' '.join(request.GET.get('q', '').split())[:100]
    if search_query:
        category_params['q'] = search_query
    if selected:
        category_params['price'] = selected.key

    return products, {
        'price_segments': [
            {
                'label': segment.label,
                'selected': segment == selected,
                'url': _price_filter_url(request, None if segment == selected else segment.key),
            }
            for segment in PRICE_SEGMENTS
        ],
        'selected_price_segment': selected,
        'clear_price_filter_url': _price_filter_url(request),
        'category_price_query': urlencode({'price': selected.key}) if selected else '',
        'category_filter_query': urlencode(category_params),
    }
