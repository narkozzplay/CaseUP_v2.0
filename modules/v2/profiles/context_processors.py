from decimal import Decimal

from .models import UserProfile


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
        return {"coins": 0}

    profile = _get_request_profile(request)
    coins = profile.balance_coins
    try:
        coins_display = int(coins) if coins == coins.to_integral() else coins
    except Exception:
        coins_display = coins

    return {"coins": coins_display}
