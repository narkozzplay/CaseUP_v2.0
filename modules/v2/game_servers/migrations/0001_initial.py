from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GameServer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("game", models.CharField(choices=[("cs2", "Counter-Strike 2")], db_index=True, default="cs2", max_length=24)),
                ("host", models.CharField(max_length=255)),
                ("port", models.PositiveIntegerField(default=27015)),
                ("server_path", models.CharField(blank=True, default="", max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("is_enabled", models.BooleanField(db_index=True, default=True)),
                ("sort_order", models.PositiveIntegerField(db_index=True, default=100)),
                ("status", models.CharField(choices=[("unknown", "Unknown"), ("online", "Online"), ("offline", "Offline")], db_index=True, default="unknown", max_length=16)),
                ("last_check_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_seen_online_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=255)),
                ("last_latency_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("shop_label", models.CharField(blank=True, default="", max_length=120)),
                ("shop_description", models.TextField(blank=True, default="")),
                ("sync_notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Game server",
                "verbose_name_plural": "Game servers",
                "ordering": ("sort_order", "id"),
                "indexes": [models.Index(fields=["is_enabled", "sort_order"], name="game_server_is_enab_615554_idx"), models.Index(fields=["game", "status"], name="game_server_game_68501a_idx")],
            },
        ),
        migrations.CreateModel(
            name="GameServerPrivilege",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=64)),
                ("name", models.CharField(max_length=120)),
                ("privilege_type", models.CharField(choices=[("vip", "VIP"), ("admin", "Admin"), ("moderation", "Moderation"), ("other", "Other")], db_index=True, default="vip", max_length=16)),
                ("price", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("duration_days", models.PositiveIntegerField(default=30)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("description", models.TextField(blank=True, default="")),
                ("sort_order", models.PositiveIntegerField(db_index=True, default=100)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("server", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="privileges", to="game_servers.gameserver")),
            ],
            options={
                "verbose_name": "Game server privilege",
                "verbose_name_plural": "Game server privileges",
                "ordering": ("server", "sort_order", "id"),
                "indexes": [models.Index(fields=["server", "is_active", "sort_order"], name="game_server_server__de8d09_idx"), models.Index(fields=["server", "privilege_type", "is_active"], name="game_server_server__6cf810_idx")],
                "constraints": [models.UniqueConstraint(fields=("server", "code"), name="game_servers_privilege_server_code_unique")],
            },
        ),
        migrations.CreateModel(
            name="GameServerStatusCheck",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("unknown", "Unknown"), ("online", "Online"), ("offline", "Offline")], db_index=True, max_length=16)),
                ("checked_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("latency_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("error", models.CharField(blank=True, default="", max_length=255)),
                ("server", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="status_checks", to="game_servers.gameserver")),
            ],
            options={
                "verbose_name": "Game server status check",
                "verbose_name_plural": "Game server status checks",
                "ordering": ("-checked_at", "-id"),
                "indexes": [models.Index(fields=["server", "-checked_at"], name="game_server_server__d9aeb0_idx"), models.Index(fields=["status", "-checked_at"], name="game_server_status_7bdb75_idx")],
            },
        ),
    ]
