import importlib
import os
import sys
from unittest import TestCase
from unittest.mock import patch


class DevSettingsGuardTests(TestCase):
    def _import_dev_settings(self):
        sys.modules.pop("config.settings.dev", None)
        sys.modules.pop("config.settings.base", None)
        return importlib.import_module("config.settings.dev")

    def test_dev_settings_allow_local_hosts_without_explicit_secret(self):
        with patch.dict(
            os.environ,
            {
                "DJANGO_SETTINGS_MODULE": "config.settings.dev",
                "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost,.ngrok-free.dev",
                "DJANGO_SECRET_KEY": "",
            },
            clear=False,
        ):
            module = self._import_dev_settings()

        self.assertEqual(module.SECRET_KEY, "dev-secret-key-not-for-production")

    def test_dev_settings_reject_unsafe_hosts(self):
        with patch.dict(
            os.environ,
            {
                "DJANGO_SETTINGS_MODULE": "config.settings.dev",
                "DJANGO_ALLOWED_HOSTS": "example.com,127.0.0.1",
                "DJANGO_SECRET_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                self._import_dev_settings()
