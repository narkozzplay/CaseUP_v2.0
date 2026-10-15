import ipaddress

from django.db import migrations


def _public(ip):
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified or address.is_reserved)


def backfill(apps, schema_editor):
    """Seed the IP history from the signed-in devices recorded so far (accounts.UserSession)."""
    UserSession = apps.get_model("accounts_v2", "UserSession")
    UserIp = apps.get_model("antifraud", "UserIp")
    seen = {}
    for user_id, ip, last_ip, created_at, last_seen_at in UserSession.objects.values_list("user_id", "ip_address", "last_ip", "created_at", "last_seen_at").iterator():
        for address in {ip, last_ip}:
            if not _public(address):
                continue
            key = (user_id, address)
            first, last = seen.get(key, (created_at, last_seen_at))
            seen[key] = (min(first, created_at), max(last, last_seen_at))
    UserIp.objects.bulk_create(
        [UserIp(user_id=user_id, ip=address, first_seen=first, last_seen=last) for (user_id, address), (first, last) in seen.items()],
        ignore_conflicts=True,
        batch_size=1000,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("antifraud", "0001_initial"),
        ("accounts_v2", "0003_two_factor_auth"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
