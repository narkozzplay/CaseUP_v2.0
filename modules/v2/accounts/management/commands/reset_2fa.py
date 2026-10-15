from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from modules.v2.accounts.two_factor import disable


class Command(BaseCommand):
    help = "Turn off two-factor authentication for a user who lost the phone and the backup codes (by username or id)."

    def add_arguments(self, parser):
        parser.add_argument("user", help="username (e.g. steam_7656119...) or numeric user id")

    def handle(self, *args, **options):
        User = get_user_model()
        ident = str(options["user"]).strip()
        user = User.objects.filter(username=ident).first()
        if user is None and ident.isdigit():
            user = User.objects.filter(pk=int(ident)).first()
        if user is None:
            raise CommandError(f"User {ident!r} not found")
        if not disable(user, summary="2FA сброшена с сервера (manage.py reset_2fa)"):
            self.stdout.write(f"{user.username}: 2FA was not enabled")
            return
        self.stdout.write(self.style.SUCCESS(f"{user.username}: 2FA is off; it can be set up again after the next login"))
