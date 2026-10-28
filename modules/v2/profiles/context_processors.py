from decimal import Decimal

from .models import UserProfile
from .presentation import get_profile_avatar_url, get_profile_display_name
from .services.presence import is_profile_online


ZERO = Decimal("0.00")


def _get_request_profile(request):
    cached = getattr(request, "_profile_v2_cache", None)
    if cached is not None:
        return cached

    try:
        profile = request.user.profile_v2
    except UserProfile.DoesNotExist:
        profile, _ = UserProfile.objects.get_or_create(
            user=request.user,
            defaults={"balance_coins": ZERO},
        )

    request._profile_v2_cache = profile
    return profile


def user_balance(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {
            "coins": 0,
            "current_user_avatar_url": "",
            "current_user_display_name": "",
            "current_user_is_online": False,
            "current_user_effects": {},
        }

    profile = _get_request_profile(request)
    coins = profile.balance_coins
    try:
        coins_display = int(coins) if coins == coins.to_integral() else coins
    except Exception:
        coins_display = coins

    return {
        "coins": coins_display,
        "current_user_avatar_url": get_profile_avatar_url(profile),
        "current_user_display_name": get_profile_display_name(profile, fallback_username=request.user.username),
        "current_user_is_online": is_profile_online(profile),
        "current_user_effects": _get_request_effects(request),
    }


def _get_request_effects(request) -> dict[str, str]:
    """Frame and nickname effects for the sidebar card, once per request."""
    cached = getattr(request, "_profile_effects_cache", None)
    if cached is not None:
        return cached
    try:
        from modules.v2.profile_customization.services import get_profile_effect_classes

        effects = get_profile_effect_classes(request.user)
    except Exception:
        effects = {}
    request._profile_effects_cache = effects
    return effects
