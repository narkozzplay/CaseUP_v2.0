from __future__ import annotations

from django.shortcuts import render

from .services import get_feature_access_state



def render_feature_locked_response(
    request,
    *,
    feature_code: str,
    title: str,
    description: str,
    status: int = 403,
    family: str = "site",
    plans_url_name: str = "subscriptions:plans",
):
    access_state = get_feature_access_state(request.user, feature_code, family=family)
    context = {
        "access_state": access_state,
        "feature_lock_title": title,
        "feature_lock_description": description,
        "plans_url_name": plans_url_name,
    }
    return render(request, "subscriptions/feature_locked.html", context, status=status)
