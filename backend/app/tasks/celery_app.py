from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "ozon_analytics",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.sync"],
)
celery_app.conf.update(
    timezone="Europe/Moscow",
    task_serializer="json",
    accept_content=["json"],
    beat_schedule={
        # раз в час; минута не :00 — чтобы не совпадать с пиком запросов к Ozon
        "sync-products-hourly": {"task": "tasks.sync_products_all", "schedule": crontab(minute=7)},
        # каждые 30 минут, со сдвигом от синхронизации товаров
        "sync-stocks-every-30-min": {"task": "tasks.sync_stocks_all", "schedule": crontab(minute="13,43")},
    },
)


@celery_app.task(name="tasks.ping")
def ping():
    return "pong"
