"""
Custom Template Tags & Filters cho Phân hệ Dashboard — Quốc Hùng Mobile.
Hỗ trợ định dạng số và tiền tệ chuẩn Việt Nam (Vietnamese Formatting),
tính toán an toàn tỷ lệ phần trăm và xử lý hiển thị.
"""

from decimal import Decimal
from django import template

register = template.Library()


@register.filter(name='vn_number')
def vn_number(value):
    """
    Định dạng số nguyên / thập phân theo chuẩn Việt Nam với dấu chấm phân cách hàng nghìn.
    Ví dụ: 4850000000 -> 4.850.000.000, 1840 -> 1.840, 365 -> 365.
    """
    if value is None or value == '':
        return '0'
    try:
        if isinstance(value, (int, float, Decimal)):
            num = round(float(value))
        else:
            num = round(float(str(value).strip()))
        return f"{int(num):,}".replace(',', '.')
    except (ValueError, TypeError):
        return str(value)


@register.filter(name='vn_currency')
def vn_currency(value):
    """
    Định dạng tiền tệ chuẩn Việt Nam với dấu chấm phân cách hàng nghìn và hậu tố 'đ'.
    Ví dụ: 4850000000 -> 4.850.000.000đ.
    """
    if value is None or value == '':
        return '0đ'
    try:
        formatted = vn_number(value)
        return f"{formatted}đ"
    except Exception:
        return f"{value}đ"


@register.filter(name='safe_abs')
def safe_abs(value):
    """
    Trả về giá trị tuyệt đối an toàn.
    """
    try:
        return abs(float(value or 0))
    except (ValueError, TypeError):
        return 0


@register.filter(name='divide_pct')
def divide_pct(numerator, denominator):
    """
    Tính phần trăm giữa tử số và mẫu số an toàn, tránh lỗi chia cho 0.
    """
    try:
        num = float(numerator or 0)
        den = float(denominator or 0)
        if den == 0:
            return 0.0
        return round((num / den) * 100.0, 1)
    except (ValueError, TypeError, ZeroDivisionError):
        return 0.0


@register.filter(name='css_pct')
def css_pct(value):
    """
    Trả về chuỗi số tỷ lệ phần trăm (0 - 100) sử dụng dấu chấm thập phân chuẩn CSS (ASCII period),
    không bị ảnh hưởng bởi định dạng vùng (USE_L10N / Vietnamese locale comma).
    Ví dụ: 33.3 -> '33.3', 0 -> '0', 100 -> '100', 8.333 -> '8.3'.
    """
    if value is None or value == '':
        return '0'
    try:
        val = float(value)
        clamped = max(0.0, min(100.0, val))
        s = f"{clamped:.1f}"
        if s.endswith('.0'):
            return s[:-2]
        return s
    except (ValueError, TypeError):
        return '0'
