from __future__ import annotations

import logging

from whitenoise.storage import CompressedManifestStaticFilesStorage


logger = logging.getLogger(__name__)


class CaseUPStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Hashed + compressed static files that never take a page down.

    Theme CSS and store preview paths come from the database (admin input). With the stock
    manifest storage a single wrong path raises ValueError on every render; here it falls
    back to the unhashed URL and logs a warning instead.
    """

    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:
            logger.warning("Static file is missing from the manifest: %s", name)
            return name
