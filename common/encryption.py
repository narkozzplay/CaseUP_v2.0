"""Encryption at rest for secrets stored in the database (API keys, bot tokens).

Values are stored as ``enc:v1:<Fernet token>``. Keys come from FIELD_ENCRYPTION_KEYS (comma-separated
Fernet keys, the first one encrypts, all of them decrypt, which allows rotation). A key derived from
SECRET_KEY is always appended as a decryption fallback and is used for encryption only when no
dedicated key is configured, so existing deployments keep working until a dedicated key is set.

Generate a key:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django import forms
from django.conf import settings
from django.db import models


logger = logging.getLogger(__name__)

ENCRYPTED_PREFIX = "enc:v1:"

_fernet_cache: dict[tuple, MultiFernet] = {}


def _derived_key(secret_key: str) -> bytes:
    digest = hashlib.sha256(f"caseup-field-encryption:{secret_key}".encode()).digest()
    return base64.urlsafe_b64encode(digest)


def configured_keys() -> list[str]:
    raw = getattr(settings, "FIELD_ENCRYPTION_KEYS", []) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    return [key.strip() for key in raw if key and key.strip()]


def get_fernet() -> MultiFernet:
    keys = tuple(configured_keys())
    cache_key = (keys, settings.SECRET_KEY)
    fernet = _fernet_cache.get(cache_key)
    if fernet is None:
        fernets = [Fernet(key.encode()) for key in keys]
        fernets.append(Fernet(_derived_key(settings.SECRET_KEY)))
        fernet = MultiFernet(fernets)
        _fernet_cache.clear()
        _fernet_cache[cache_key] = fernet
    return fernet


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)


def encrypt_value(value):
    if value is None or value == "" or is_encrypted(value):
        return value
    token = get_fernet().encrypt(str(value).encode()).decode()
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_value(value):
    if not is_encrypted(value):
        return value  # empty, or plaintext written before encryption was introduced
    try:
        return get_fernet().decrypt(value[len(ENCRYPTED_PREFIX):].encode()).decode()
    except InvalidToken:
        # Keep the ciphertext instead of returning "": a later save then preserves it, and the value
        # becomes readable again once the right key is configured.
        logger.error("Cannot decrypt a stored secret: FIELD_ENCRYPTION_KEYS/SECRET_KEY do not match the data.")
        return value


def reencrypt_value(value):
    """Re-encrypt with the current primary key (after adding a new key to FIELD_ENCRYPTION_KEYS)."""
    if not is_encrypted(value):
        return encrypt_value(value)
    try:
        token = get_fernet().rotate(value[len(ENCRYPTED_PREFIX):].encode()).decode()
    except InvalidToken:
        return value
    return f"{ENCRYPTED_PREFIX}{token}"


class EncryptedTextField(models.TextField):
    """Text column holding an encrypted secret; Python code always sees the plaintext.

    ``plain_max_length`` limits the plaintext in forms (ciphertext is longer than the input).
    Not usable for lookups: every encryption produces a different token.
    """

    def __init__(self, *args, plain_max_length: int | None = 255, **kwargs):
        self.plain_max_length = plain_max_length
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.plain_max_length != 255:
            kwargs["plain_max_length"] = self.plain_max_length
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value)

    def to_python(self, value):
        return decrypt_value(super().to_python(value))

    def get_prep_value(self, value):
        return encrypt_value(super().get_prep_value(value))

    def formfield(self, **kwargs):
        defaults = {"form_class": forms.CharField, "max_length": self.plain_max_length, "widget": forms.TextInput}
        defaults.update(kwargs)
        return super().formfield(**defaults)


def migrate_encrypt_fields(model, field_names: tuple[str, ...]) -> None:
    """Data-migration helper: encrypt plaintext values left from before the field was encrypted."""
    for obj in model.objects.all():
        obj.save(update_fields=list(field_names))


def migrate_decrypt_fields(model, field_names: tuple[str, ...], schema_editor) -> None:
    """Reverse data-migration helper: write plaintext back so the field can become a CharField again."""
    table = schema_editor.quote_name(model._meta.db_table)
    pk_column = schema_editor.quote_name(model._meta.pk.column)
    with schema_editor.connection.cursor() as cursor:
        for row in model.objects.values("pk", *field_names):  # from_db_value decrypts
            for field_name in field_names:
                column = schema_editor.quote_name(model._meta.get_field(field_name).column)
                cursor.execute(f"UPDATE {table} SET {column} = %s WHERE {pk_column} = %s", [row[field_name] or "", row["pk"]])
