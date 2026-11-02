# Source excerpt; private application dependencies are intentionally omitted.

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties

from app.config import get_settings

logger = logging.getLogger(__name__)

INGEST_QUEUE = "kb.ingest"
INGEST_RETRY_QUEUE = "kb.ingest.retry"
INGEST_DLQ = "kb.ingest.dlq"
VECTOR_QUEUE = "kb.vector"
VECTOR_RETRY_QUEUE = "kb.vector.retry"
VECTOR_DLQ = "kb.vector.dlq"

_QUEUE_ARGS = {
    INGEST_QUEUE: {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": INGEST_RETRY_QUEUE},
    INGEST_RETRY_QUEUE: {"x-message-ttl": 30000, "x-dead-letter-exchange": "", "x-dead-letter-routing-key": INGEST_QUEUE},
    VECTOR_QUEUE: {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": VECTOR_RETRY_QUEUE},
    VECTOR_RETRY_QUEUE: {"x-message-ttl": 30000, "x-dead-letter-exchange": "", "x-dead-letter-routing-key": VECTOR_QUEUE},
    INGEST_DLQ: {},
    VECTOR_DLQ: {},
}


class RabbitMQUnavailable(RuntimeError):
    pass


def enabled() -> bool:
    return bool((get_settings().rabbitmq_url or "").strip())


def _connection_parameters() -> pika.URLParameters:
    url = (get_settings().rabbitmq_url or "").strip()
    if not url:
        raise RabbitMQUnavailable("RABBITMQ_URL 未配置")
    params = pika.URLParameters(url)
    params.heartbeat = int(get_settings().rabbitmq_heartbeat_seconds)
    params.blocked_connection_timeout = int(get_settings().rabbitmq_blocked_timeout_seconds)
    return params


@contextmanager
def channel() -> Iterator[BlockingChannel]:
    conn = pika.BlockingConnection(_connection_parameters())
    ch = conn.channel()
    try:
        declare_queues(ch)
        yield ch
    finally:
        try:
            ch.close()
        except Exception:
            pass
        conn.close()


def declare_queues(ch: BlockingChannel) -> None:
    for name, args in _QUEUE_ARGS.items():
        ch.queue_declare(queue=name, durable=True, arguments=args or None)


def publish(queue: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with channel() as ch:
        ch.basic_publish(
            exchange="",
            routing_key=queue,
            body=body,
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
            ),
            mandatory=True,
        )


def publish_ingest(payload: dict[str, Any]) -> None:
    publish(INGEST_QUEUE, payload)


def publish_vector(payload: dict[str, Any]) -> None:
    publish(VECTOR_QUEUE, payload)


def publish_retry(queue: str, payload: dict[str, Any]) -> None:
    retry_queue = INGEST_RETRY_QUEUE if queue == INGEST_QUEUE else VECTOR_RETRY_QUEUE
    publish(retry_queue, payload)


def parse_message(body: bytes) -> dict[str, Any]:
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("RabbitMQ message body must be a JSON object")
    return data


def reject_to_dlq(ch: BlockingChannel, method: Basic.Deliver, queue: str, payload: dict[str, Any], error: str) -> None:
    dlq = INGEST_DLQ if queue == INGEST_QUEUE else VECTOR_DLQ
    failed_payload = {**payload, "last_error": error[:1000]}
    ch.basic_publish(
        exchange="",
        routing_key=dlq,
        body=json.dumps(failed_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        properties=BasicProperties(content_type="application/json", delivery_mode=2),
    )
    ch.basic_ack(delivery_tag=method.delivery_tag)
