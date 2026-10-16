from pathlib import Path
from unittest import TestCase


BASE_DIR = Path(__file__).resolve().parent.parent


def _lines(filename: str) -> list[str]:
    return (BASE_DIR / filename).read_text(encoding="utf-8").splitlines()


def _code_lines(filename: str) -> list[str]:
    return [line for line in _lines(filename) if line.strip() and not line.lstrip().startswith("#")]


class ComposeFilesTests(TestCase):
    """Compose appends ports/volumes from every -f file, so anything published or
    bind-mounted in docker-compose.yml would also leak into the prod stack."""

    def test_base_compose_publishes_no_ports(self):
        offending = [line for line in _code_lines("docker-compose.yml") if line.strip() == "ports:"]
        self.assertEqual(offending, [], "Move published ports to docker-compose.override.yml or docker-compose.prod.yml")

    def test_base_compose_does_not_mount_source_tree(self):
        offending = [line for line in _code_lines("docker-compose.yml") if line.strip().startswith("- .:")]
        self.assertEqual(offending, [], "Move the source bind mount to docker-compose.override.yml")

    def test_prod_compose_publishes_web_on_loopback_only(self):
        published = [
            line.strip().lstrip("-").strip().strip('"')
            for line in _code_lines("docker-compose.prod.yml")
            if line.strip().startswith("- \"") and line.strip().endswith(':8000"')
        ]
        self.assertEqual(published, ["127.0.0.1:18001:8000"])

    def test_dev_override_keeps_local_workflow(self):
        content = "\n".join(_code_lines("docker-compose.override.yml"))
        self.assertIn('"${DJANGO_PORT:-8000}:8000"', content)
        # web, celery_worker, celery_beat, telegram_poller
        self.assertEqual(content.count("- .:/app"), 4)
