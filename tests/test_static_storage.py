import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from common.storage import CaseUPStaticFilesStorage


class CaseUPStaticFilesStorageTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "staticfiles.json").write_text(
            json.dumps({"version": "1.1", "paths": {"css/app.css": "css/app.abc123.css"}, "hash": "x"}),
            encoding="utf-8",
        )
        self.storage = CaseUPStaticFilesStorage(location=str(self.root), base_url="/static/")

    def tearDown(self):
        self._tmp.cleanup()

    def test_known_file_uses_hashed_name(self):
        self.assertEqual(self.storage.url("css/app.css"), "/static/css/app.abc123.css")

    def test_missing_file_falls_back_to_plain_url(self):
        with self.assertLogs("common.storage", level="WARNING"):
            self.assertEqual(self.storage.url("css/themes/typo.css"), "/static/css/themes/typo.css")


class ProdStorageSettingsTests(SimpleTestCase):
    def test_prod_settings_use_storages_setting(self):
        source = (Path(__file__).resolve().parent.parent / "config" / "settings" / "prod.py").read_text(encoding="utf-8")
        self.assertIn('"common.storage.CaseUPStaticFilesStorage"', source)
        self.assertNotIn("\nSTATICFILES_STORAGE", source)
