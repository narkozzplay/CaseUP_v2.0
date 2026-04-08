from django.contrib.auth.decorators import login_required

from modules.v2.profiles.rbac import get_user_role
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title


@login_required
def dashboard_page(request):
    return render_dashboard_page(
        request,
        full_template="shop/dashboard.html",
        partial_template="shop/dashboard.html",
        context={"role_access": get_user_role(request.user)},
        page_title=build_page_title("Магазин"),
        active_menu_key="shop",
    )
