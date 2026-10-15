from celery import shared_task

from .services import remind_streaks


@shared_task(name="daily_bonus.remind_streaks")
def remind_streaks_task() -> int:
    return remind_streaks()
