from cryptography.fernet import Fernet
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

from common.encryption import ENCRYPTED_PREFIX, decrypt_value, encrypt_value, reencrypt_value
from modules.v2.system.checks import check_field_encryption_key_is_dedicated, check_field_encryption_keys
from modules.v2.system.models import ExternalServiceIntegration
from modules.v2.tg_notifier.models import TelegramNotifierConfig


def _raw(model, field, pk):
    table = model._meta.db_table
    column = model._meta.get_field(field).column
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT "{column}" FROM "{table}" WHERE id = %s', [pk])
        return cursor.fetchone()[0]


class EncryptedFieldStorageTests(TestCase):
    def test_secrets_are_stored_encrypted_and_read_as_plaintext(self):
        integration = ExternalServiceIntegration.objects.create(steam_api_key="STEAM-SECRET", telegram_bot_token="TG-SECRET")

        raw = _raw(ExternalServiceIntegration, "steam_api_key", integration.pk)
        self.assertTrue(raw.startswith(ENCRYPTED_PREFIX))
        self.assertNotIn("STEAM-SECRET", raw)
        self.assertEqual(ExternalServiceIntegration.objects.get(pk=integration.pk).steam_api_key, "STEAM-SECRET")

    def test_empty_values_stay_empty(self):
        integration = ExternalServiceIntegration.objects.create(steam_api_key="")
        self.assertEqual(_raw(ExternalServiceIntegration, "steam_api_key", integration.pk), "")

    def test_telegram_webhook_secret_is_encrypted(self):
        config = TelegramNotifierConfig.objects.create(bot_token="123:abc")

        self.assertTrue(_raw(TelegramNotifierConfig, "webhook_secret", config.pk).startswith(ENCRYPTED_PREFIX))
        reloaded = TelegramNotifierConfig.objects.get(pk=config.pk)
        self.assertEqual(reloaded.webhook_secret, config.webhook_secret)
        self.assertEqual(reloaded.bot_token, "123:abc")

    def test_reencrypt_command_moves_secrets_to_the_new_primary_key(self):
        from django.core.management import call_command
        from io import StringIO

        with override_settings(SECRET_KEY="old-secret", FIELD_ENCRYPTION_KEYS=[]):
            integration = ExternalServiceIntegration.objects.create(steam_api_key="ROTATE-ME")
        new_key = Fernet.generate_key().decode()
        with override_settings(SECRET_KEY="old-secret", FIELD_ENCRYPTION_KEYS=[new_key]):
            call_command("reencrypt_secrets", stdout=StringIO())
        raw = _raw(ExternalServiceIntegration, "steam_api_key", integration.pk)
        self.assertEqual(Fernet(new_key.encode()).decrypt(raw[len(ENCRYPTED_PREFIX):].encode()), b"ROTATE-ME")

    def test_legacy_plaintext_rows_are_still_readable(self):
        integration = ExternalServiceIntegration.objects.create()
        with connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE "{ExternalServiceIntegration._meta.db_table}" SET steam_api_key = %s WHERE id = %s',
                ["OLD-PLAINTEXT", integration.pk],
            )
        self.assertEqual(ExternalServiceIntegration.objects.get(pk=integration.pk).steam_api_key, "OLD-PLAINTEXT")


class EncryptionKeyTests(SimpleTestCase):
    def test_wrong_key_keeps_ciphertext_instead_of_losing_it(self):
        with override_settings(SECRET_KEY="first-secret-key"):
            stored = encrypt_value("API-KEY")
        with override_settings(SECRET_KEY="rotated-secret-key"), self.assertLogs("common.encryption", level="ERROR"):
            self.assertEqual(decrypt_value(stored), stored)
        with override_settings(SECRET_KEY="first-secret-key"):
            self.assertEqual(decrypt_value(stored), "API-KEY")

    def test_adding_dedicated_key_keeps_old_values_readable_and_rotates(self):
        dedicated = Fernet.generate_key().decode()
        with override_settings(SECRET_KEY="stable-secret", FIELD_ENCRYPTION_KEYS=[]):
            stored = encrypt_value("API-KEY")
        with override_settings(SECRET_KEY="stable-secret", FIELD_ENCRYPTION_KEYS=[dedicated]):
            self.assertEqual(decrypt_value(stored), "API-KEY")
            rotated = reencrypt_value(stored)
        # After rotation only the dedicated key is needed.
        with override_settings(SECRET_KEY="another-secret", FIELD_ENCRYPTION_KEYS=[dedicated]):
            self.assertEqual(decrypt_value(rotated), "API-KEY")

    def test_invalid_key_is_reported_at_startup(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=["not-a-fernet-key"]):
            errors = check_field_encryption_keys(None)
        self.assertEqual([error.id for error in errors], ["caseup.E001"])

    def test_deploy_check_warns_without_dedicated_key(self):
        with override_settings(DEBUG=False, FIELD_ENCRYPTION_KEYS=[]):
            self.assertEqual([w.id for w in check_field_encryption_key_is_dedicated(None)], ["caseup.W001"])
        with override_settings(DEBUG=False, FIELD_ENCRYPTION_KEYS=[Fernet.generate_key().decode()]):
            self.assertEqual(check_field_encryption_key_is_dedicated(None), [])
