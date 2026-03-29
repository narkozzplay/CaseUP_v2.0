from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("celery_tasks", "0002_priceautoupdaterunlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="priceautoupdaterule",
            name="hourly_minute",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="priceautoupdaterule",
            name="schedule_mode",
            field=models.CharField(
                choices=[
                    ("interval", "От точки старта"),
                    ("hourly", "Каждый час в выбранную минуту"),
                ],
                db_index=True,
                default="interval",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="priceautoupdaterule",
            name="start_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]