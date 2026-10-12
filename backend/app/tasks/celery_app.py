from celery import Celery

from app.core.config import settings

celery_app = Celery("ozon_analytics", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
celery_app.conf.update(timezone="Europe/Moscow", task_serializer="json", accept_content=["json"])


@celery_app.task(name="tasks.ping")
def ping():
    return "pong"
