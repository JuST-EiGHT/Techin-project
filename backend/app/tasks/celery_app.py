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
        # начисления: каждые 6 часов — вчера и сегодня (плюс незагруженные дни за 90 дней)
        "sync-finance-every-6h": {"task": "tasks.sync_finance_all", "schedule": crontab(minute=23, hour="*/6")},
        # баланс у Ozon и выплаты: каждые 6 часов, после начислений
        "sync-balance-every-6h": {"task": "tasks.sync_balance_all", "schedule": crontab(minute=28, hour="*/6")},
        # банки по API (шаг 3.12): раз в час — новые операции и остатки
        "sync-bank-hourly": {"task": "tasks.sync_bank_all", "schedule": crontab(minute=52)},
        # акции: раз в 3 часа
        "sync-promotions-every-3h": {"task": "tasks.sync_promotions_all", "schedule": crontab(minute=33, hour="*/3")},
        # отправления FBO и FBS — продажи по кластерам для поставок (шаг 5.2): каждые 6 часов, последние 35 дней
        "sync-postings-every-6h": {"task": "tasks.sync_postings_all", "schedule": crontab(minute=38, hour="*/6")},
        # аномалии (шаг 7.1): раз в час, после синхронизаций часа (товары :07, остатки :43, банки :52)
        "detect-anomalies-hourly": {"task": "tasks.detect_anomalies_all", "schedule": crontab(minute=57)},
    },
)


@celery_app.task(name="tasks.ping")
def ping():
    return "pong"
