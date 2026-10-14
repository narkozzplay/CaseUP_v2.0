from __future__ import annotations

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def check_yookassa_live_keys(app_configs, **kwargs):
    secret = str(getattr(settings, "YOOKASSA_SECRET_KEY", "") or "")
    if settings.DEBUG or not secret.startswith("test_"):
        return []
    return [
        Warning(
            "YooKassa runs with a test secret key in production: test cards top up real balances.",
            hint="Switch YOOKASSA_SHOP_ID/YOOKASSA_SECRET_KEY to the live shop (key starts with live_) before accepting users.",
            id="caseup.W002",
        )
    ]
