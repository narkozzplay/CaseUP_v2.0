from celery import shared_task


@shared_task(name="support.send_telegram_copy", ignore_result=True)
def send_telegram_copy(user_id: int, text: str, url: str) -> bool:
    from .services import send_telegram_now

    return send_telegram_now(user_id, text, url)
