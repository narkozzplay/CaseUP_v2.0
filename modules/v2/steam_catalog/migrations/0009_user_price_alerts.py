from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('steam_catalog', '0008_price_movement_alerts'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserPriceAlertRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('scope_type', models.CharField(choices=[('item', 'Конкретный предмет'), ('family', 'Группа / family'), ('item_type', 'Тип предмета')], db_index=True, default='item_type', max_length=16)),
                ('family_name', models.CharField(blank=True, db_index=True, default='', max_length=255)),
                ('item_type', models.CharField(blank=True, choices=[('skin', 'Skin'), ('case', 'Case'), ('sticker', 'Sticker'), ('graffiti', 'Graffiti'), ('charm', 'Charm'), ('other', 'Other')], db_index=True, default='', max_length=16)),
                ('direction', models.CharField(choices=[('up', 'Рост'), ('down', 'Падение'), ('both', 'Оба направления')], db_index=True, default='both', max_length=8)),
                ('reference_point', models.CharField(choices=[('previous_point', 'От предыдущей точки'), ('window_start', 'От старта окна'), ('window_max', 'От максимума за период'), ('window_min', 'От минимума за период'), ('last_local_max', 'От последнего локального максимума'), ('last_local_min', 'От последнего локального минимума'), ('last_extreme', 'От последней экстремальной точки')], db_index=True, default='window_start', max_length=32)),
                ('threshold_mode', models.CharField(choices=[('rub', 'Только RUB'), ('percent', 'Только %'), ('and', 'RUB и %'), ('or', 'RUB или %')], default='and', max_length=16)),
                ('window_kind', models.CharField(choices=[('hours', 'Последние N часов'), ('days', 'Последние N дней'), ('points', 'Последние N точек')], default='hours', max_length=16)),
                ('window_value', models.PositiveIntegerField(default=24)),
                ('min_change_rub', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('min_change_percent', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=7)),
                ('cooldown_minutes', models.PositiveIntegerField(default=360)),
                ('repeat_step_rub', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('repeat_step_percent', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=7)),
                ('repeat_mode', models.CharField(choices=[('once', 'Один раз'), ('step', 'Повторно по шагам'), ('new_extreme', 'После нового экстремума')], db_index=True, default='once', max_length=16)),
                ('transport', models.CharField(choices=[('telegram', 'Telegram')], db_index=True, default='telegram', max_length=16)),
                ('delivery_target', models.CharField(choices=[('user_chat', 'В привязанный chat_id пользователя'), ('default_chat', 'В default_chat_id')], db_index=True, default='user_chat', max_length=16)),
                ('status', models.CharField(choices=[('draft', 'Черновик'), ('active', 'Активно'), ('disabled', 'Выключено')], db_index=True, default='draft', max_length=16)),
                ('last_triggered_at', models.DateTimeField(blank=True, null=True)),
                ('last_sent_at', models.DateTimeField(blank=True, null=True)),
                ('last_error_at', models.DateTimeField(blank=True, null=True)),
                ('last_error_message', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='user_alert_rules', to='steam_catalog.steamitem')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steam_price_alert_rules', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['user_id', 'name', 'id']},
        ),
        migrations.CreateModel(
            name='UserPriceAlertEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('transport', models.CharField(choices=[('telegram', 'Telegram')], db_index=True, default='telegram', max_length=16)),
                ('delivery_target', models.CharField(choices=[('user_chat', 'В привязанный chat_id пользователя'), ('default_chat', 'В default_chat_id')], db_index=True, default='user_chat', max_length=16)),
                ('delivery_status', models.CharField(choices=[('pending', 'Pending'), ('sent', 'Sent'), ('error', 'Error'), ('skipped', 'Skipped')], db_index=True, default='pending', max_length=16)),
                ('trigger_source', models.CharField(blank=True, db_index=True, default='celery', max_length=16)),
                ('direction', models.CharField(choices=[('up', 'Рост'), ('down', 'Падение')], db_index=True, max_length=8)),
                ('reference_point', models.CharField(choices=[('previous_point', 'От предыдущей точки'), ('window_start', 'От старта окна'), ('window_max', 'От максимума за период'), ('window_min', 'От минимума за период'), ('last_local_max', 'От последнего локального максимума'), ('last_local_min', 'От последнего локального минимума'), ('last_extreme', 'От последней экстремальной точки')], db_index=True, max_length=32)),
                ('reference_at', models.DateTimeField()),
                ('current_at', models.DateTimeField()),
                ('window_started_at', models.DateTimeField()),
                ('baseline_price_rub', models.DecimalField(decimal_places=2, max_digits=12)),
                ('current_price_rub', models.DecimalField(decimal_places=2, max_digits=12)),
                ('change_rub', models.DecimalField(decimal_places=2, max_digits=12)),
                ('change_percent', models.DecimalField(decimal_places=2, max_digits=7)),
                ('step_index', models.PositiveIntegerField(db_index=True, default=1)),
                ('repeat_reason', models.CharField(blank=True, default='', max_length=32)),
                ('sent_to', models.CharField(blank=True, default='', max_length=128)),
                ('dedupe_key', models.CharField(max_length=255, unique=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_price_alert_events', to='steam_catalog.steamitem')),
                ('rule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='steam_catalog.userpricealertrule')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steam_price_alert_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at', '-id']},
        ),
        migrations.AddIndex(
            model_name='userpricealertrule',
            index=models.Index(fields=['user', 'status'], name='st_upar_usr_stat_idx'),
        ),
        migrations.AddIndex(
            model_name='userpricealertrule',
            index=models.Index(fields=['scope_type', 'item_type'], name='st_upar_scope_type_idx'),
        ),
        migrations.AddIndex(
            model_name='userpricealertrule',
            index=models.Index(fields=['family_name', 'status'], name='st_upar_fam_stat_idx'),
        ),
        migrations.AddIndex(
            model_name='userpricealertevent',
            index=models.Index(fields=['user', 'delivery_status'], name='st_upae_usr_dstat_idx'),
        ),
        migrations.AddIndex(
            model_name='userpricealertevent',
            index=models.Index(fields=['rule', 'item', 'direction'], name='steam_user_event_rule_item_idx'),
        ),
        migrations.AddIndex(
            model_name='userpricealertevent',
            index=models.Index(fields=['delivery_status', 'created_at'], name='st_upae_dstat_cr_idx'),
        ),
    ]
