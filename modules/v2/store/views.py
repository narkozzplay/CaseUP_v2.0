from __future__ import annotations

from django.contrib.auth.decorators import login_required

from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title


@login_required
def storefront_page(request):
    context = {
        "quick_sections": (
            {"title": "Catalog foundations", "description": "Подготовка витрины товаров и динамической фильтрации."},
            {"title": "Checkout roadmap", "description": "Каркас под будущие платежные и order-flow интеграции."},
            {"title": "Ops hooks", "description": "Слой для связки с подписками, промо и module flags."},
        )
    }
    return render_dashboard_page(
        request,
        full_template="store/storefront.html",
        partial_template="store/partials/storefront_content.html",
        context=context,
        page_title=build_page_title("Store"),
        active_menu_key="store",
    )
