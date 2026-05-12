from django.urls import path

from .views import (
    activate_module_slot_page,
    activate_theme_page,
    add_review_page,
    analytics_page,
    author_product_edit_page,
    author_product_create_page,
    author_product_update_page,
    author_products_page,
    author_submit_moderation_page,
    deactivate_module_slot_page,
    deactivate_theme_page,
    library_page,
    moderation_action_page,
    moderation_queue_page,
    product_detail_page,
    purchase_bundle_page,
    purchase_product_page,
    purchase_theme_page,
    storefront_page,
)

app_name = "store"

urlpatterns = [
    path("", storefront_page, name="storefront"),
    path("library/", library_page, name="library"),
    path("products/<slug:listing_key>/", product_detail_page, name="product_detail"),
    path("products/<int:listing_id>/purchase/", purchase_product_page, name="purchase_product"),
    path("bundles/<int:listing_id>/purchase/", purchase_bundle_page, name="purchase_bundle"),
    path("themes/<int:listing_id>/purchase/", purchase_theme_page, name="purchase_theme"),
    path("themes/<int:listing_id>/activate/", activate_theme_page, name="activate_theme"),
    path("themes/deactivate/", deactivate_theme_page, name="deactivate_theme"),
    path("modules/<int:listing_id>/activate/", activate_module_slot_page, name="activate_module_slot"),
    path("modules/deactivate/", deactivate_module_slot_page, name="deactivate_module_slot"),
    path("products/<int:listing_id>/reviews/add/", add_review_page, name="add_review"),
    path("author/products/", author_products_page, name="author_products"),
    path("author/products/<int:listing_id>/edit/", author_product_edit_page, name="author_product_edit"),
    path("author/products/create/", author_product_create_page, name="author_product_create"),
    path("author/products/<int:listing_id>/update/", author_product_update_page, name="author_product_update"),
    path("author/products/<int:listing_id>/submit/", author_submit_moderation_page, name="author_product_submit"),
    path("moderation/", moderation_queue_page, name="moderation_queue"),
    path("moderation/<int:listing_id>/action/", moderation_action_page, name="moderation_action"),
    path("analytics/", analytics_page, name="analytics"),
]
