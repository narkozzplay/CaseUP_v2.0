from django.apps import apps
from django.core.management.base import BaseCommand

from common.encryption import EncryptedTextField, reencrypt_value


class Command(BaseCommand):
    help = (
        "Re-encrypt stored secrets with the first key of FIELD_ENCRYPTION_KEYS. "
        "Run after putting a new key first; afterwards the old keys can be removed."
    )

    def handle(self, *args, **options):
        total = 0
        for model in apps.get_models():
            fields = [field for field in model._meta.concrete_fields if isinstance(field, EncryptedTextField)]
            if not fields:
                continue
            for obj in model.objects.all():
                for field in fields:
                    # Attribute holds plaintext; reencrypt_value() encrypts it with the current primary key.
                    setattr(obj, field.attname, reencrypt_value(getattr(obj, field.attname)))
                obj.save(update_fields=[field.name for field in fields])
                total += 1
        self.stdout.write(self.style.SUCCESS(f"Re-encrypted secrets in {total} row(s)."))
