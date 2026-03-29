from __future__ import annotations

from .models import SubscriptionPlan
from .services import get_subscription_context


def subscription_context(request):
    user = getattr(request, "user", None)
    return {
        "subscription_context": get_subscription_context(user, family=SubscriptionPlan.Family.SITE),
        "farmer_subscription_context": get_subscription_context(user, family=SubscriptionPlan.Family.FARMER),
    }