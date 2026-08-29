from celery import Celery
from kombu import Exchange, Queue

from smartstock_api.config import get_settings

settings = get_settings()
celery_app = Celery("smartstock", broker=settings.broker_url)

QUEUES = ("imports", "connectors", "documents", "forecasts", "notifications", "exports")
celery_app.conf.update(
    task_queues=tuple(
        Queue(name, Exchange(name, type="direct", durable=True), routing_key=name) for name in QUEUES
    ),
    task_routes={f"smartstock.{name}.*": {"queue": name} for name in QUEUES},
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_default_delivery_mode="persistent",
)
