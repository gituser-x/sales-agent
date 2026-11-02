# Source excerpt; private application dependencies are intentionally omitted.

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import update

from app.config import get_settings
from app.db.models import BackgroundTask, KbImportBatch
from app.db.session import SessionLocal
from app.services import kb_ingest_service
from app.services.rabbitmq_service import (
    INGEST_QUEUE,
    channel,
    parse_message,
    publish_ingest,
    publish_retry,
    reject_to_dlq,
)
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


def _find_task(db, payload: dict[str, Any]) -> BackgroundTask | None:
    task_id = payload.get("background_task_id")
    if task_id:
        return db.get(BackgroundTask, int(task_id))
    batch_id = payload.get("batch_id")
    if not batch_id:
        return None
    return (
        db.query(BackgroundTask)
        .filter(BackgroundTask.task_type == "kb_ingest_file")
        .filter(BackgroundTask.payload_json["batch_id"].as_integer() == int(batch_id))
        .order_by(BackgroundTask.id.desc())
        .first()
    )


def _claim_task(db, task: BackgroundTask | None, *, allow_processing: bool = False) -> bool:
    if not task:
        return True
    if task.status == "success":
        return False
    if task.status == "processing":
        return allow_processing
    result = db.execute(
        update(BackgroundTask)
        .where(BackgroundTask.id == task.id, BackgroundTask.status == "pending")
        .values(
            status="processing",
            started_at=utc_now(),
            progress=max(int(task.progress or 0), 10),
            error_message=None,
        )
    )
    db.commit()
    return bool(result.rowcount)


def _finish_task(db, task: BackgroundTask | None, batch: KbImportBatch, payload: dict[str, Any]) -> None:
    if not task:
        return
    task.progress = 100
    task.finished_at = utc_now()
    task.result_json = {
        "batch_id": batch.id,
        "status": batch.status,
        "success_rows": batch.success_rows,
        "failed_rows": batch.failed_rows,
        "document_ids": (batch.document_ids_json or {}).get("document_ids", []),
    }
    if batch.status == "completed":
        task.status = "success"
        task.error_message = None
    else:
        task.status = "failed"
        log = batch.error_log or {}
        if isinstance(log, dict):
            task.error_message = str(log.get("reason") or log.get("message") or log.get("queue_message") or "入库失败")[:1000]
        else:
            task.error_message = str(log or "入库失败")[:1000]
    db.commit()


def _mark_retry(db, task: BackgroundTask | None, error: str) -> int:
    retry_count = 0
    if task:
        task.retry_count = int(task.retry_count or 0) + 1
        retry_count = task.retry_count
        task.status = "pending"
        task.error_message = error[:1000]
        task.finished_at = utc_now()
        db.commit()
    return retry_count


def _mark_failed(db, task: BackgroundTask | None, payload: dict[str, Any], error: str) -> None:
    if task:
        task.status = "failed"
        task.error_message = error[:1000]
        task.finished_at = utc_now()
    batch_id = payload.get("batch_id")
    if batch_id:
        batch = db.get(KbImportBatch, int(batch_id))
        if batch and batch.status not in ("completed", "failed"):
            batch.status = "failed"
            batch.stage = "failed"
            batch.error_log = {"reason": error[:500]}
            batch.completed_at = utc_now()
    db.commit()


def process_payload(payload: dict[str, Any], *, allow_processing: bool = False) -> None:
    db = SessionLocal()
    try:
        task = _find_task(db, payload)
        if not _claim_task(db, task, allow_processing=allow_processing):
            return
        batch_id = int(payload.get("batch_id") or 0)
        existing_batch = db.get(KbImportBatch, batch_id) if batch_id else None
        if existing_batch and existing_batch.status == "completed":
            _finish_task(db, task, existing_batch, payload)
            return
        batch = kb_ingest_service.ingest_file_from_object(
            db,
            filename=str(payload.get("filename") or "upload"),
            object_key=str(payload.get("object_key") or ""),
            doc_type=str(payload.get("doc_type") or ""),
            user_id=payload.get("user_id"),
            batch_id=batch_id,
            file_hash=payload.get("file_hash"),
        )
        _finish_task(db, task, batch, payload)
    finally:
        db.close()


def republish_pending(limit: int = 500) -> int:
    db = SessionLocal()
    try:
        tasks = (
            db.query(BackgroundTask)
            .filter(BackgroundTask.task_type == "kb_ingest_file", BackgroundTask.status == "pending")
            .order_by(BackgroundTask.id.asc())
            .limit(limit)
            .all()
        )
        count = 0
        for task in tasks:
            payload = dict(task.payload_json or {})
            if not payload.get("object_key"):
                continue
            publish_ingest({**payload, "background_task_id": task.id, "attempt": int(task.retry_count or 0) + 1})
            count += 1
        return count
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=getattr(logging, get_settings().log_level.upper(), logging.INFO))
    logger.info("starting RabbitMQ ingest worker")
    while True:
        try:
            republished = republish_pending()
            if republished:
                logger.info("republished pending ingest tasks: %s", republished)
            with channel() as ch:
                ch.basic_qos(prefetch_count=max(1, int(get_settings().rabbitmq_prefetch_count)))

                def callback(channel_, method, properties, body):  # noqa: ANN001
                    payload: dict[str, Any] = {}
                    try:
                        payload = parse_message(body)
                        process_payload(payload, allow_processing=bool(method.redelivered))
                        channel_.basic_ack(delivery_tag=method.delivery_tag)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("ingest task failed")
                        db = SessionLocal()
                        task_id = None
                        try:
                            task = _find_task(db, payload) if payload else None
                            task_id = int(task.id) if task else None
                            retry_count = _mark_retry(db, task, str(exc))
                            max_attempts = int(get_settings().rabbitmq_ingest_max_attempts)
                        finally:
                            db.close()
                        attempt = int(payload.get("attempt") or retry_count or 1) if payload else 1
                        if payload and attempt < max_attempts:
                            publish_retry(INGEST_QUEUE, {**payload, "attempt": attempt + 1})
                            channel_.basic_ack(delivery_tag=method.delivery_tag)
                        else:
                            db = SessionLocal()
                            try:
                                failed_task = db.get(BackgroundTask, task_id) if task_id else None
                                _mark_failed(db, failed_task, payload, str(exc))
                            finally:
                                db.close()
                            reject_to_dlq(channel_, method, INGEST_QUEUE, payload or {}, str(exc))

                ch.basic_consume(queue=INGEST_QUEUE, on_message_callback=callback)
                ch.start_consuming()
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("ingest worker loop failed: %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
