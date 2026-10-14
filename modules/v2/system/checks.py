from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from common.encryption import configured_keys


@register(Tags.security, deploy=False)
def check_field_encryption_keys(app_configs, **kwargs):
    from cryptography.fernet import Fernet

    messages = []
    for index, key in enumerate(configured_keys(), start=1):
        try:
            Fernet(key.encode())
        except (ValueError, TypeError):
            messages.append(
                Error(
                    f"FIELD_ENCRYPTION_KEYS entry #{index} is not a valid Fernet key.",
                    hint='Generate one: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
                    id="caseup.E001",
                )
            )
    return messages


@register(Tags.security, deploy=True)
def check_field_encryption_key_is_dedicated(app_configs, **kwargs):
    if configured_keys() or settings.DEBUG:
        return []
    return [
        Warning(
            "FIELD_ENCRYPTION_KEYS is not set: stored API keys are encrypted with a key derived from SECRET_KEY, "
            "so rotating SECRET_KEY would make them unreadable.",
            hint="Add FIELD_ENCRYPTION_KEYS to the environment (keep SECRET_KEY unchanged while doing so).",
            id="caseup.W001",
        )
    ]
