from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.utils.translation import get_language
from django.views.decorators.cache import never_cache

from .models import HouseBanner
from .services import register_click


@never_cache
def banner_click(request, banner_id: int):
    """Counts a click on an own banner and sends the visitor on. The target is set by the admin, not the request."""
    banner = get_object_or_404(HouseBanner, pk=banner_id)
    register_click(banner, request.user)
    target = banner.url or "/"
    if not banner.is_external:
        # Internal paths are stored without a language prefix; keep the visitor on their language version.
        language = get_language() or settings.LANGUAGE_CODE
        prefixes = tuple(f"/{code}/" for code, _name in settings.LANGUAGES)
        if language != settings.LANGUAGE_CODE and not target.startswith(prefixes):
            target = f"/{language}{target}"
    return HttpResponseRedirect(target)
