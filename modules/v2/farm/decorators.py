from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden

from .auth import get_farm_access


def farm_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        access = get_farm_access(request.user)
        if not access.has_access:
            return HttpResponseForbidden("Farm доступен только farmer+.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def farm_staff_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        access = get_farm_access(request.user)
        if not access.staff_access:
            return HttpResponseForbidden("Farm staff section.")
        return view_func(request, *args, **kwargs)

    return _wrapped