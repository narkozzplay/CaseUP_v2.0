"""Server load: what the VPS is doing now and over the last day / week / month (Control Center → «Сервер»).

- CPU, load average, memory, swap and uptime come from /proc. Docker does not virtualise these files, so the
  web and worker containers see the whole VPS, not only themselves.
- Disk: the filesystem that holds the docker volumes (MEDIA_ROOT), the same one Postgres and backups live on.
- Containers: docker is only reachable through its socket, which the site must not hold. The container_stats
  sidecar (docker-compose.prod.yml, no network) writes `docker ps` / `docker stats` into a shared volume once a
  minute, and the site only reads those files.
- system.server_snapshot (Celery beat, every 5 minutes) stores a ServerMetricSnapshot and raises an admin alert
  when memory or CPU stay overloaded for SUSTAINED_SNAPSHOTS snapshots in a row. The disk alert is the
  watchdog's (health.check_disk).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

CPU_STATE_KEY = "ops:server:cpu_times"
CONTAINERS_STALE_SECONDS = 5 * 60
SUSTAINED_SNAPSHOTS = 3
SWAP_WARN_PERCENT = 80
LOAD_WARN_PER_CPU = 2.0

RANGES = {
    # code: (label, period, bucket seconds; 0 = every snapshot)
    "day": ("Сутки", timedelta(days=1), 0),
    "week": ("Неделя", timedelta(days=7), 3600),
    "month": ("Месяц", timedelta(days=30), 6 * 3600),
}
DEFAULT_RANGE = "day"


@dataclass
class Reading:
    cpu_count: int
    cpu_percent: float | None
    load1: float
    load5: float
    load15: float
    mem_total: int
    mem_available: int
    swap_total: int
    swap_used: int
    disk_total: int
    disk_used: int
    uptime_seconds: int
    containers: list[dict]
    containers_at: float | None = None


def _proc(name: str) -> Path:
    return Path(getattr(settings, "SERVER_PROC_ROOT", "/proc")) / name


def _read(name: str) -> str:
    try:
        return _proc(name).read_text(encoding="utf-8")
    except OSError:
        return ""


def read_meminfo() -> dict[str, int]:
    """/proc/meminfo in bytes."""
    values = {}
    for line in _read("meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key.strip()] = int(parts[0]) * (1024 if len(parts) > 1 and parts[1].lower() == "kb" else 1)
    return values


def read_loadavg() -> tuple[float, float, float]:
    parts = _read("loadavg").split()
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (IndexError, ValueError):
        return 0.0, 0.0, 0.0


def read_uptime() -> int:
    try:
        return int(float(_read("uptime").split()[0]))
    except (IndexError, ValueError):
        return 0


def read_cpu_times() -> tuple[int, int] | None:
    """(busy, total) jiffies of all CPUs since boot."""
    for line in _read("stat").splitlines():
        if line.startswith("cpu "):
            numbers = [int(value) for value in line.split()[1:]]
            idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)  # idle + iowait
            total = sum(numbers[:8])  # guest time is already inside user/nice
            return total - idle, total
    return None


def cpu_count() -> int:
    count = 0
    for line in _read("stat").splitlines():
        if line.startswith("cpu") and line[3:4].isdigit():
            count += 1
    return count or os.cpu_count() or 1


def _cpu_percent_between(before: tuple[int, int], after: tuple[int, int]) -> float | None:
    busy = after[0] - before[0]
    total = after[1] - before[1]
    if total <= 0 or busy < 0:
        return None
    return round(min(100.0, busy * 100 / total), 1)


def cpu_percent(*, remember: bool = False) -> float | None:
    """Average CPU use since the previous snapshot (remember=True stores the point for the next one);
    without a recent previous point, a one-second sample."""
    now = read_cpu_times()
    if now is None:
        return None
    previous = cache.get(CPU_STATE_KEY)
    if remember:
        cache.set(CPU_STATE_KEY, {"times": now, "at": time.time()}, 3600)
    if previous and time.time() - previous["at"] < 15 * 60:
        value = _cpu_percent_between(tuple(previous["times"]), now)
        if value is not None:
            return value
    time.sleep(1)
    after = read_cpu_times()
    return _cpu_percent_between(now, after) if after else None


def disk_usage() -> tuple[int, int]:
    target = Path(getattr(settings, "MEDIA_ROOT", "") or settings.BASE_DIR)
    if not target.exists():
        target = Path(settings.BASE_DIR)
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return 0, 0
    return usage.total, usage.used


# --------------------------------------------------------------------------- containers

_SIZE_UNITS = {
    "b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
}


def parse_size(text: str) -> int:
    """'123.4MiB' → bytes (docker stats format)."""
    text = (text or "").strip().lower()
    number = text.rstrip("abcdefghijklmnopqrstuvwxyz")
    unit = text[len(number):] or "b"
    try:
        return int(float(number) * _SIZE_UNITS.get(unit, 1))
    except ValueError:
        return 0


def _parse_percent(text: str) -> float | None:
    try:
        return float((text or "").strip().rstrip("%"))
    except ValueError:
        return None


def _json_lines(path: Path) -> list[dict]:
    rows = []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def stats_dir() -> Path:
    return Path(getattr(settings, "SERVER_STATS_DIR", "") or Path(settings.BASE_DIR) / "var" / "server_stats")


def read_containers() -> tuple[list[dict], float | None]:
    """Containers from the sidecar files: [{name, state, status, cpu, mem, mem_limit}], file time.
    Empty when the files are missing or older than CONTAINERS_STALE_SECONDS (sidecar not running)."""
    folder = stats_dir()
    ps_path, stats_path = folder / "ps.jsonl", folder / "stats.jsonl"
    try:
        written = ps_path.stat().st_mtime
    except OSError:
        return [], None
    if time.time() - written > CONTAINERS_STALE_SECONDS:
        return [], written
    usage = {}
    for row in _json_lines(stats_path):
        name = str(row.get("Name") or row.get("Container") or "").lstrip("/")
        used, _, limit = str(row.get("MemUsage") or "").partition("/")
        usage[name] = {"cpu": _parse_percent(row.get("CPUPerc")), "mem": parse_size(used), "mem_limit": parse_size(limit)}
    containers = []
    for row in _json_lines(ps_path):
        name = str(row.get("Names") or "").split(",")[0].lstrip("/")
        if not name:
            continue
        item = {
            "name": name[:80],
            "state": str(row.get("State") or "")[:20],
            "status": str(row.get("Status") or "")[:60],
            "cpu": None,
            "mem": 0,
            "mem_limit": 0,
        }
        item.update(usage.get(name, {}))
        containers.append(item)
    containers.sort(key=lambda item: (item["state"] != "running", -(item["mem"] or 0), item["name"]))
    return containers, written


# --------------------------------------------------------------------------- reading / snapshot


def read_server(*, remember_cpu: bool = False) -> Reading:
    mem = read_meminfo()
    mem_total = mem.get("MemTotal", 0)
    mem_available = mem.get("MemAvailable", mem.get("MemFree", 0) + mem.get("Cached", 0) + mem.get("Buffers", 0))
    swap_total = mem.get("SwapTotal", 0)
    load1, load5, load15 = read_loadavg()
    disk_total, disk_used = disk_usage()
    containers, containers_at = read_containers()
    return Reading(
        cpu_count=cpu_count(),
        cpu_percent=cpu_percent(remember=remember_cpu),
        load1=load1,
        load5=load5,
        load15=load15,
        mem_total=mem_total,
        mem_available=min(mem_available, mem_total),
        swap_total=swap_total,
        swap_used=max(0, swap_total - mem.get("SwapFree", swap_total)),
        disk_total=disk_total,
        disk_used=disk_used,
        uptime_seconds=read_uptime(),
        containers=containers,
        containers_at=containers_at,
    )


def take_snapshot():
    from .models import ServerMetricSnapshot

    reading = read_server(remember_cpu=True)
    snapshot = ServerMetricSnapshot.objects.create(
        created_at=timezone.now(),
        cpu_count=reading.cpu_count,
        cpu_percent=reading.cpu_percent,
        load1=reading.load1,
        load5=reading.load5,
        load15=reading.load15,
        mem_total=reading.mem_total,
        mem_available=reading.mem_available,
        swap_total=reading.swap_total,
        swap_used=reading.swap_used,
        disk_total=reading.disk_total,
        disk_used=reading.disk_used,
        uptime_seconds=reading.uptime_seconds,
        containers=[
            {"name": c["name"], "state": c["state"], "cpu": c["cpu"], "mem": c["mem"]} for c in reading.containers
        ],
    )
    keep_days = int(getattr(settings, "SERVER_STATS_KEEP_DAYS", 35))
    ServerMetricSnapshot.objects.filter(created_at__lt=snapshot.created_at - timedelta(days=keep_days)).delete()
    return snapshot


def check_overload() -> dict[str, str]:
    """Alerts when memory / swap or CPU stay overloaded for the last SUSTAINED_SNAPSHOTS snapshots (15 min):
    one spike of a price update is normal, a server that keeps swapping is not."""
    from .models import HostingSettings, ServerMetricSnapshot
    from .watchdog import _transition

    config = HostingSettings.load()
    recent = list(ServerMetricSnapshot.objects.order_by("-created_at")[:SUSTAINED_SNAPSHOTS])
    if len(recent) < SUSTAINED_SNAPSHOTS or not config.alerts_enabled:
        return {}
    url = "/control-center/server/"
    last = recent[0]
    results = {}

    def memory_bad(snap) -> bool:
        return snap.mem_percent >= config.ram_warn_percent or (snap.swap_total and snap.swap_percent >= SWAP_WARN_PERCENT)

    from modules.v2.control_center.services.health import format_bytes

    detail = (
        f"Память {last.mem_percent:.0f}% ({format_bytes(last.mem_used)} из {format_bytes(last.mem_total)})"
        + (f", swap {last.swap_percent:.0f}% ({format_bytes(last.swap_used)})" if last.swap_total else "")
        + ". Посмотрите контейнеры в Control Center → «Сервер»; если так постоянно — пора на тариф с большей памятью."
    )
    outcome = _transition("server_memory", "Память сервера", all(memory_bad(snap) for snap in recent), detail, url)
    if outcome:
        results["memory"] = outcome

    cpu_detail = (
        f"Нагрузка {last.load5:.2f} при {last.cpu_count} ядрах, CPU {last.cpu_percent or 0:.0f}%. "
        "Задачи будут выполняться медленнее; посмотрите, какой контейнер грузит процессор."
    )
    cpu_bad = all(snap.load5 >= LOAD_WARN_PER_CPU * max(1, snap.cpu_count) for snap in recent)
    outcome = _transition("server_cpu", "Процессор сервера", cpu_bad, cpu_detail, url)
    if outcome:
        results["cpu"] = outcome
    return results


# --------------------------------------------------------------------------- history and charts


def normalize_range(code: str) -> str:
    return code if code in RANGES else DEFAULT_RANGE


def _bucketed(rows: list[dict], bucket_seconds: int) -> list[dict]:
    """Averages per bucket (maximum for the memory peak) so a month fits into a readable line."""
    if not bucket_seconds:
        return rows
    buckets: dict[int, list[dict]] = {}
    for row in rows:
        buckets.setdefault(int(row["at"].timestamp()) // bucket_seconds, []).append(row)
    result = []
    for _key, items in sorted(buckets.items()):
        merged = {"at": items[-1]["at"]}
        for field in ("cpu", "mem", "swap", "disk", "load"):
            values = [item[field] for item in items if item[field] is not None]
            merged[field] = round(sum(values) / len(values), 1) if values else None
        merged["mem_peak"] = max(item["mem"] for item in items)
        result.append(merged)
    return result


def history(range_code: str) -> list[dict]:
    from .models import ServerMetricSnapshot

    label, period, bucket = RANGES[normalize_range(range_code)]
    since = timezone.now() - period
    rows = []
    for snap in ServerMetricSnapshot.objects.filter(created_at__gte=since).order_by("created_at").only(
        "created_at", "cpu_percent", "load5", "mem_total", "mem_available", "swap_total", "swap_used", "disk_total", "disk_used"
    ):
        rows.append(
            {
                "at": snap.created_at,
                "cpu": snap.cpu_percent,
                "mem": round(snap.mem_percent, 1),
                "mem_peak": round(snap.mem_percent, 1),
                "swap": round(snap.swap_percent, 1),
                "disk": round(snap.disk_percent, 1),
                "load": snap.load5,
            }
        )
    return _bucketed(rows, bucket)


def _svg_number(value: float) -> str:
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


VIEW_WIDTH, VIEW_HEIGHT = 1000, 200


def build_percent_chart(rows: list[dict], field: str, *, note=None) -> dict:
    """A 0–100% line in the markup of the price chart (price_chart.js draws the tooltip)."""
    points_src = [row for row in rows if row.get(field) is not None]
    if not points_src:
        return {"has_data": False}
    n = len(points_src)
    first, last = points_src[0]["at"], points_src[-1]["at"]
    span = max(1.0, (last - first).total_seconds())

    def x_percent(row) -> float:
        return 50.0 if n == 1 else round((row["at"] - first).total_seconds() / span * 100, 3)

    def y_percent(value) -> float:
        return round(100 - max(0.0, min(100.0, float(value))), 3)

    coords = [(x_percent(row), y_percent(row[field])) for row in points_src]
    if n == 1:
        line = f"M0 {_svg_number(coords[0][1] * VIEW_HEIGHT / 100)} H{VIEW_WIDTH}"
    else:
        line = "M" + " L".join(f"{_svg_number(x * VIEW_WIDTH / 100)} {_svg_number(y * VIEW_HEIGHT / 100)}" for x, y in coords)
    area = f"{line} L{VIEW_WIDTH} {VIEW_HEIGHT} L0 {VIEW_HEIGHT} Z" if n > 1 else ""

    long_period = span > 2 * 86400
    points = []
    for (x, y), row in zip(coords, points_src):
        local = timezone.localtime(row["at"])
        points.append(
            {
                "x": x,
                "y": y,
                "price": f"{row[field]:.0f}",
                "date": local.strftime("%d.%m %H:%M"),
                "note": note(row) if note else "",
                "dir": "flat",
            }
        )

    wanted = min(n, 3)
    indices = [0] if n == 1 else sorted({round(i * (n - 1) / (wanted - 1)) for i in range(wanted)})
    x_ticks = []
    for index in indices:
        x = points[index]["x"]
        local = timezone.localtime(points_src[index]["at"])
        x_ticks.append(
            {
                "x": x,
                "label": local.strftime("%d.%m" if long_period else "%H:%M"),
                "align": "start" if x < 8 else ("end" if x > 92 else "center"),
            }
        )
    values = [row[field] for row in points_src]
    return {
        "has_data": True,
        "is_single": n == 1,
        "points_json": points,
        "line_path": line,
        "area_path": area,
        "view_width": VIEW_WIDTH,
        "view_height": VIEW_HEIGHT,
        "y_ticks": [{"y": y, "label": f"{100 - y}%"} for y in (0, 25, 50, 75, 100)],
        "x_ticks": x_ticks,
        "last_point": {"x": points[-1]["x"], "y": points[-1]["y"], "label": f"{values[-1]:.0f}%"},
        "max": max(values),
        "avg": round(sum(values) / len(values), 1),
    }


def container_peaks(hours: int = 24) -> dict[str, int]:
    """Largest memory of each container over the last hours, from the snapshots."""
    from .models import ServerMetricSnapshot

    peaks: dict[str, int] = {}
    since = timezone.now() - timedelta(hours=hours)
    for containers in ServerMetricSnapshot.objects.filter(created_at__gte=since).values_list("containers", flat=True):
        for item in containers or []:
            name = item.get("name")
            if name:
                peaks[name] = max(peaks.get(name, 0), int(item.get("mem") or 0))
    return peaks


def format_uptime(seconds: int) -> str:
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"
