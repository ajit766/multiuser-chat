import json
import logging
import time

import pika

from . import clients
from .config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

QUEUE_NAME = "message.created"


def handle_message_created(event: dict) -> None:
    message_id = event["message_id"]
    to_user_id = event["to_user_id"]

    if not clients.is_user_online(to_user_id):
        clients.mark_message_pending(message_id)
        logger.info("Recipient %s offline, message %s marked PENDING", to_user_id, message_id)
        return

    delivered = clients.push_to_gateway(
        to_user_id,
        {
            "type": "message",
            "data": {
                "id": message_id,
                "from_user_id": event["from_user_id"],
                "to_user_id": to_user_id,
                "message": event["message"],
                "created_at": event["created_at"],
                "status": "DELIVERED",
            },
        },
    )

    if delivered:
        clients.mark_message_delivered(message_id)
        logger.info("Delivered message %s to %s", message_id, to_user_id)

        # The recipient's copy already shows DELIVERED (pushed above with
        # that status). The sender also needs to know, so their own tick
        # flips from single to double - otherwise their UI would only
        # learn about it on next page reload / history fetch.
        clients.push_to_gateway(
            event["from_user_id"],
            {
                "type": "status_update",
                "data": {"id": message_id, "to_user_id": to_user_id, "status": "DELIVERED"},
            },
        )
    else:
        # Presence said online but Gateway had no live socket for them
        # (e.g. they disconnected a moment ago). Same as the offline case -
        # mark PENDING so gateway-service's connect-time catch-up picks it
        # back up next time they reconnect.
        clients.mark_message_pending(message_id)
        logger.info("No live socket for %s, message %s marked PENDING", to_user_id, message_id)


def _on_message(channel, method, properties, body):
    try:
        event = json.loads(body)
        handle_message_created(event)
    except Exception:
        # No dead-letter queue / retry in v1 - a poison message would
        # otherwise loop forever. The message is already durable in
        # Postgres, so we just log and move on.
        logger.exception("Failed to process message.created event: %s", body)
    finally:
        channel.basic_ack(delivery_tag=method.delivery_tag)


def _connect_with_retry(max_attempts: int = 10, delay_seconds: float = 3.0) -> pika.BlockingConnection:
    for attempt in range(1, max_attempts + 1):
        try:
            return pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=settings.rabbitmq_host,
                    port=settings.rabbitmq_port,
                    credentials=pika.PlainCredentials(
                        settings.rabbitmq_user, settings.rabbitmq_password
                    ),
                )
            )
        except pika.exceptions.AMQPConnectionError:
            logger.warning(
                "RabbitMQ not ready yet (attempt %s/%s), retrying in %ss...",
                attempt,
                max_attempts,
                delay_seconds,
            )
            time.sleep(delay_seconds)
    raise RuntimeError("Could not connect to RabbitMQ after retries")


def run_forever() -> None:
    connection = _connect_with_retry()
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    channel.basic_qos(prefetch_count=10)
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=_on_message)

    logger.info("Delivery Service listening on queue '%s'", QUEUE_NAME)
    channel.start_consuming()


if __name__ == "__main__":
    run_forever()
