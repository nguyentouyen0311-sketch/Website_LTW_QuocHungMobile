"""Public catalog view API, organized by application flow."""

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

from .categories import (
    category_create_view,
    category_edit_view,
    category_list_view,
    category_toggle_visibility_view,
)
from .products import (
    product_create_view,
    product_detail_view,
    product_edit_view,
    product_image_assign_variant_view,
    product_image_delete_view,
    product_image_upload_view,
    product_images_reorder_view,
    product_list_view,
    product_soft_delete_view,
)
from .storefront import (
    homepage_view,
    promotion_list_view,
    public_catalog_view,
    public_product_detail_view,
    storefront_category_view,
)
from .variants import (
    attribute_create_view,
    attribute_delete_view,
    attribute_edit_view,
    attribute_list_view,
    attribute_value_create_view,
    attribute_value_delete_view,
    attribute_value_edit_view,
    product_variant_create_view,
    product_variant_edit_view,
    product_variant_list_view,
)

__all__ = [
    "attribute_create_view", "attribute_delete_view", "attribute_edit_view", "attribute_list_view",
    "attribute_value_create_view", "attribute_value_delete_view", "attribute_value_edit_view",
    "category_create_view", "category_edit_view", "category_list_view",
    "category_toggle_visibility_view", "homepage_view", "parse_positive_id",
    "product_create_view", "product_detail_view", "product_edit_view",
    "product_image_assign_variant_view", "product_image_delete_view",
    "product_image_upload_view", "product_images_reorder_view",
    "product_list_view", "product_soft_delete_view",
    "product_variant_create_view", "product_variant_edit_view",
    "product_variant_list_view", "promotion_list_view", "public_catalog_view",
    "public_product_detail_view", "storefront_category_view",
]
