from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache

from .models import HouseBanner
from .services import register_click


@never_cache
def banner_click(request, banner_id: int):
    """Counts a click on an own banner and sends the visitor on. The target is set by the admin, not the request."""
    banner = get_object_or_404(HouseBanner, pk=banner_id)
    register_click(banner, request.user)
    return HttpResponseRedirect(banner.url or "/")
