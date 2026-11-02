# Source excerpt; private application dependencies are intentionally omitted.
# Depends on upload metadata, part validation and file path helpers.
def assemble_upload_to_file(db: Session, upload_id: str) -> tuple[KbResumableUpload, str, str]:
    """Merge chunks into a durable file path without loading the whole file into memory."""
    upload = get_upload(db, upload_id)
    if upload.status not in ("uploading", "failed"):
        raise HTTPException(400, f"当前任务状态为 {upload.status}，不能完成合并")
    _validate_upload_limits(
        file_name=upload.file_name,
        total_size=int(upload.total_size),
        chunk_size=int(upload.chunk_size),
        total_chunks=int(upload.total_chunks),
    )

    chunks = _chunks(upload)
    missing = [i for i in range(upload.total_chunks) if i not in chunks]
    if missing:
        raise HTTPException(400, f"分片未上传完整，缺失: {missing[:20]}")

    upload.status = "assembling"
    upload.error_message = None
    db.commit()

    merged_path: Path | None = None
    try:
        d = _upload_dir(upload_id)
        merged_dir = _root() / "merged"
        merged_dir.mkdir(parents=True, exist_ok=True)
        merged_path = merged_dir / f"{upload_id}_{_safe_filename(upload.file_name)}"
        sha = hashlib.sha256()
        total = 0
        with merged_path.open("wb") as out:
            for i in range(upload.total_chunks):
                part_path = d / f"{i:08d}.part"
                _validate_part_file(upload, part_path, i)
                with part_path.open("rb") as f:
                    while True:
                        block = f.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > upload.total_size:
                            raise ValueError("合并累计大小超过声明总大小")
                        sha.update(block)
                        out.write(block)

        if total != upload.total_size:
            raise ValueError(f"合并大小与声明不一致：actual={total}, declared={upload.total_size}")
        if upload.file_hash and sha.hexdigest().lower() != upload.file_hash.lower():
            raise ValueError("文件 SHA256 校验失败，请重新上传")

        upload.file_hash = sha.hexdigest()
        upload.completed_at = utc_now()
        db.commit()
        db.refresh(upload)
        return upload, upload.file_name, str(merged_path)
    except Exception as exc:  # noqa: BLE001
        if merged_path:
            merged_path.unlink(missing_ok=True)
        upload.status = "failed"
        upload.error_message = str(exc)[:500]
        db.commit()
        raise HTTPException(400, upload.error_message) from exc
