from django.urls import reverse

from catalog.models import Product

from .models import ChatFAQ
from .services import is_case_specific_request, keyword_matches_text, normalize_text, split_keywords


HUMAN_TRIGGERS = ('gap nhan vien', 'gap nguoi that', 'noi chuyen voi nhan vien', 'cskh', 'tu van vien')
STOCK_TRIGGERS = ('ton kho', 'con hang', 'het hang', 'so luong con', 'con bao nhieu')
PRODUCT_TRIGGERS = ('gia', 'san pham', 'dien thoai', 'laptop', 'iphone', 'samsung', 'ipad', 'airpods', 'macbook', 'xiaomi', 'oppo', 'vivo')
PRODUCT_STOP_WORDS = set('cho toi minh ban xin hoi xem tim kiem gia bao nhieu tien dong co cua hang san pham dien thoai laptop may mot chiec tu van ve voi la nao muon mua hien tai dang ban giup thong tin thong so cau hinh va thi nay the nao duoc khong nhe a oi'.split())


def product_results(text):
    normalized = normalize_text(text)
    tokens = set(normalized.split()) - PRODUCT_STOP_WORDS
    if not tokens:
        return []
    found = []
    queryset = Product.objects.filter(is_active=True, is_deleted=False, category__is_visible=True).only('id', 'name', 'brand', 'description')
    for product in queryset.iterator():
        name_tokens = set(normalize_text(f'{product.name} {product.brand or ""}').split())
        if not (tokens & name_tokens):
            continue
        variants = []
        for variant in product.variants.filter(is_active=True).prefetch_related('attribute_values__attribute_value').order_by('price', 'pk'):
            summary = ', '.join(link.attribute_value.value for link in variant.attribute_values.all()) or 'Mặc định'
            searchable = name_tokens | set(normalize_text(f'{variant.sku} {summary}').split())
            if not tokens.issubset(searchable):
                continue
            variants.append({'label': summary, 'sku': variant.sku, 'price': f'{variant.price:,.0f}'.replace(',', '.') + 'đ'})
        if variants:
            image = product.images.order_by('sort_order', 'id').first()
            found.append({'name': product.name, 'image': image.image.url if image and image.image else '', 'description': (product.description or '')[:300], 'url': reverse('catalog:public_product_detail', args=[product.pk]), 'variants': variants[:6], 'more_variants': len(variants) > 6})
        if len(found) == 5:
            break
    return found


def resolve_question(text):
    normalized = normalize_text(text)
    if not normalized:
        return {'kind': 'empty', 'answer': 'Vui lòng nhập câu hỏi.'}
    if is_case_specific_request(normalized) or any(keyword_matches_text(item, normalized) for item in HUMAN_TRIGGERS):
        return {'kind': 'handoff', 'answer': 'Yêu cầu cần nhân viên kiểm tra và hỗ trợ trực tiếp.'}
    if any(keyword_matches_text(item, normalized) for item in STOCK_TRIGGERS):
        return {'kind': 'stock', 'answer': 'Trợ lý không cung cấp thông tin tồn kho. Bạn có thể chọn “Gặp nhân viên” để được hỗ trợ, hoặc hỏi tên sản phẩm và giá bán.'}
    matched = []
    for faq in ChatFAQ.objects.filter(is_active=True).order_by('-priority', 'id'):
        hits = [key for key in split_keywords(faq.keywords) if keyword_matches_text(key, normalized)]
        if normalize_text(faq.question) == normalized:
            hits.append(normalized)
        if hits:
            matched.append((faq, hits))
    if matched:
        top_priority = matched[0][0].priority
        candidates = [(faq, hits) for faq, hits in matched if faq.priority == top_priority]
        if len(candidates) == 1:
            faq, hits = candidates[0]
            return {'kind': 'faq', 'answer': faq.answer, 'faq': faq, 'payload': {'source': faq.category}, 'matched_keywords': hits}
        return {'kind': 'choices', 'answer': 'Câu hỏi liên quan đến nhiều chủ đề. Bạn muốn tìm hiểu nội dung nào?', 'payload': {'choices': [{'id': faq.pk, 'label': faq.question} for faq, hits in candidates[:6]]}, 'matched_keywords': sorted({hit for faq, hits in candidates for hit in hits})}
    products = product_results(text)
    if products:
        return {'kind': 'products', 'answer': 'Đây là thông tin và giá bán hiện tại theo từng phiên bản. Mở sản phẩm để xem đầy đủ các lựa chọn.', 'payload': {'products': products, 'source': 'Danh mục sản phẩm'}}
    if any(keyword_matches_text(item, normalized) for item in PRODUCT_TRIGGERS):
        return {'kind': 'product_prompt', 'answer': 'Bạn vui lòng ghi rõ tên sản phẩm, ví dụ “Giá iPhone 16”. Nếu đã ghi tên mà chưa có kết quả, hãy kiểm tra cách viết hoặc chọn “Gặp nhân viên”.', 'payload': {'links': [{'label': 'Xem danh mục sản phẩm', 'url': reverse('catalog:public_catalog')}]}}
    return {'kind': 'handoff', 'answer': 'Chưa tìm thấy nội dung phù hợp trong bộ tri thức đã công bố.'}
