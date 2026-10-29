from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from modules.v2.profiles.view_as_user import SESSION_KEY, can_view_as_user


@login_required
@require_POST
def view_as_user_start(request):
    if not can_view_as_user(getattr(request, "real_user", request.user)):
        return HttpResponseForbidden("Недостаточно прав")
    request.session[SESSION_KEY] = True
    return redirect("frontend:dashboard_preview")


@login_required
@require_POST
def view_as_user_exit(request):
    request.session.pop(SESSION_KEY, None)
    messages.success(request, "Вы снова смотрите сайт как администратор.")
    return redirect("frontend:dashboard_preview")
