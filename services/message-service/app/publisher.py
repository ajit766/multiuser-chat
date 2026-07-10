import json
import logging

import pika

from .config import settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "message.created"


def publish_message_created(payload: dict) -> None:
    """Publish a message.created event so Delivery Service can push it to
    the recipient in real time. Postgres (not the queue) is the source of
    truth: if this publish fails, the message is still safely stored and
    will show up when the recipient's client fetches history, just without
    the instant push / double-tick."""
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=settings.rabbitmq_host,
                port=settings.rabbitmq_port,
                credentials=pika.PlainCredentials(
                    settings.rabbitmq_user, settings.rabbitmq_password
                ),
            )
        )
        try:
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.basic_publish(
                exchange="",
                routing_key=QUEUE_NAME,
                body=json.dumps(payload, default=str),
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            )
        finally:
            connection.close()
    except Exception:
        logger.exception("Failed to publish message.created event for message_id=%s", payload.get("message_id"))
