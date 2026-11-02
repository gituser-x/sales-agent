# Source excerpt; private application dependencies are intentionally omitted.
# Dependencies: query analysis, config, DB models, Milvus client and reranker.
def search(
    db: Session,
    query: str,
    top_k: int = 5,
    hint_type: str | None = None,
    doc_types: list[str] | None = None,
    metadata_filter: dict[str, Any] | None = None,
    include_meta_chunks: bool = False,
    only_meta_chunks: bool = False,
) -> list[RetrievedChunk]:
    cfg = retrieval_config_cache
    requested_top_k = max(1, min(int(top_k or cfg.get_int("top_k", 5)), 500))
    dense_top_k = max(cfg.get_int("dense_top_k", 30), requested_top_k)
    sparse_top_k = max(cfg.get_int("sparse_top_k", 30), requested_top_k)
    hybrid_top_k = max(cfg.get_int("hybrid_top_k", 40), requested_top_k * 8)
    rrf_k = cfg.get_int("rrf_k", 60)
    final_top_k = requested_top_k

    query_class = hint_type or classify_query(query)
    qinfo = understand_query(query, hint_type=query_class)
    retrieval_query = qinfo.rewritten_query if cfg.get_float("query_rewrite_enabled", 1.0) else query
    trace: dict[str, Any] = {
        "query": query,
        "query_class": query_class,
        "rewritten_query": retrieval_query,
        "qinfo": {
            "keywords": getattr(qinfo, "keywords", []),
            "filters": getattr(qinfo, "filters", {}),
            "intent": getattr(qinfo, "intent", None),
        },
        "milvus_expr": None,
        "milvus_raw_hit_count": 0,
        "milvus_after_filter_count": 0,
        "mysql_keyword_hit_count": 0,
        "mysql_broad_hit_count": 0,
        "final_count": 0,
        "requested_top_k": top_k,
        "final_top_k": final_top_k,
        "dense_top_k": dense_top_k,
        "sparse_top_k": sparse_top_k,
        "hybrid_top_k": hybrid_top_k,
        "fallback_reason": None,
        "errors": [],
        "include_meta_chunks": include_meta_chunks,
        "only_meta_chunks": only_meta_chunks,
    }
    if only_meta_chunks:
        include_meta_chunks = True

    vector_results: list[RetrievedChunk] = []
    try:
        dense_vec = embedding_service.embed(retrieval_query)
        base_expr = "status == 'active'"
        if doc_types:
            safe = [t for t in doc_types if t in set(ALL_DOC_TYPES)]
            if safe:
                quoted = ", ".join(f"'{t}'" for t in safe)
                base_expr += f" and doc_type in [{quoted}]"

        strict_inferred = bool(cfg.get_float("metadata_inferred_prefilter", 0.0))
        expr = build_milvus_filter_expr(base_expr, metadata_filter, qinfo.filters, strict=strict_inferred)
        trace["milvus_expr"] = expr
        try:
            hits = milvus_client.hybrid_search(
                dense_vec=dense_vec,
                query_text=retrieval_query,
                expr=expr,
                dense_top_k=dense_top_k,
                sparse_top_k=sparse_top_k,
                hybrid_top_k=hybrid_top_k,
                rrf_k=rrf_k,
            )
        except Exception:
            logger.exception("Milvus hybrid_search failed with expr=%s; retrying base expr", expr)
            hits = milvus_client.hybrid_search(
                dense_vec=dense_vec,
                query_text=retrieval_query,
                expr=base_expr,
                dense_top_k=dense_top_k,
                sparse_top_k=sparse_top_k,
                hybrid_top_k=hybrid_top_k,
                rrf_k=rrf_k,
            )

        trace["milvus_raw_hit_count"] = len(hits or [])
        chunk_ids = [h["chunk_id"] for h in hits if h.get("chunk_id")]
        if chunk_ids:
            rows = (
                db.query(KbChunk, KbDocument)
                .join(KbDocument, KbChunk.document_id == KbDocument.id)
                .filter(KbChunk.id.in_(chunk_ids))
                .all()
            )
            chunk_doc = {c.id: (c, d) for c, d in rows}
            from app.config import get_settings
            settings = get_settings()
            rerank_enabled = getattr(settings, "rerank_enabled", True)
            min_score = getattr(settings, "min_retrieval_score", 0.0)
            enriched: list[dict[str, Any]] = []
            for h in hits:
                cid = h["chunk_id"]
                item = chunk_doc.get(cid)
                if not item:
                    continue
                chunk, doc = item
                # Milvus 中可能残留历史 active 元数据；最终必须以 MySQL 当前状态为准。
                # 未审核、待上架、已下架或被打回修订的知识绝不能参与问答检索。
                if chunk.status != "active" or doc.status != "active" or not _document_valid(doc):
                    continue
                if not _meta_chunk_allowed(chunk, include_meta_chunks=include_meta_chunks, only_meta_chunks=only_meta_chunks):
                    continue
                if not _metadata_match(doc, chunk, metadata_filter):
                    continue
                score = _apply_type_boost(float(h.get("score") or 0.0), h.get("doc_type") or doc.doc_type, query_class)
                if rerank_enabled:
                    score += 0.08 * _light_rerank_score(query, chunk.content, doc.title)
                    score += business_rerank_bonus(query, doc.doc_type, doc.metadata_json or {}, chunk.metadata_json or {})
                if score < min_score:
                    continue
                enriched.append({"score": score, "chunk": chunk, "doc": doc})
            enriched.sort(key=lambda x: -x["score"])
            vector_results = [_to_result(x["chunk"], x["doc"], x["score"], "vector") for x in enriched[:final_top_k]]
            trace["milvus_after_filter_count"] = len(vector_results)
    except Exception:
        # embedding 服务或 Milvus 整体不可用时，不让对话直接失败；退回 MySQL 关键词检索。
        logger.exception("Vector retrieval failed; falling back to DB keyword retrieval")
        trace["errors"].append("vector retrieval failed")
        trace["fallback_reason"] = "vector_exception"

    vector_results = filter_relevant_chunks(query, vector_results)

    # 仅在向量无相关命中时做 MySQL 补充；不再为凑满 top_k 塞入无关分块。
    fallback_results: list[RetrievedChunk] = []
    if not vector_results:
        fallback_results = _db_keyword_fallback(
            db,
            query,
            qinfo,
            query_class,
            top_k=final_top_k,
            doc_types=doc_types,
            metadata_filter=metadata_filter,
            source="db_keyword",
            broad=False,
            include_meta_chunks=include_meta_chunks,
            only_meta_chunks=only_meta_chunks,
        )
        trace["mysql_keyword_hit_count"] = len(fallback_results)
        if fallback_results and not trace.get("fallback_reason"):
            trace["fallback_reason"] = "no_vector_results"

    results = _dedupe_results(vector_results + fallback_results, final_top_k)
    if results:
        results = production_rerank(query, results, top_k=final_top_k)
        trace["final_count"] = len(results)
        trace["selected_chunk_ids"] = [r.chunk_id for r in results]
        trace["top_score"] = max([float(r.score or 0.0) for r in results], default=None)
        trace["retrieval_mode"] = "vector" if vector_results and not fallback_results else ("mixed" if vector_results and fallback_results else "db_keyword")
        _set_trace(trace)
        return results

    # 宽兜底：仅用于无实体锚点的销售异议/泛咨询；避免「问剑桥」却塞其它 FAQ。
    if not only_meta_chunks and query_class in ("script", "course", "mixed", "general") and not entity_anchor_terms(query):
        broad_results = _db_keyword_fallback(
            db,
            query,
            qinfo,
            query_class,
            top_k=final_top_k,
            doc_types=doc_types,
            metadata_filter=metadata_filter,
            source="db_broad",
            broad=True,
            include_meta_chunks=include_meta_chunks,
            only_meta_chunks=only_meta_chunks,
        )
        trace["mysql_broad_hit_count"] = len(broad_results)
        broad_results = production_rerank(query, broad_results, top_k=final_top_k)
        trace["final_count"] = len(broad_results)
        trace["selected_chunk_ids"] = [r.chunk_id for r in broad_results]
        trace["top_score"] = max([float(r.score or 0.0) for r in broad_results], default=None)
        trace["retrieval_mode"] = "db_broad" if broad_results else "generic"
        trace["fallback_reason"] = trace.get("fallback_reason") or ("broad_fallback" if broad_results else "no_relevant_chunks")
        _set_trace(trace)
        return broad_results
    trace["final_count"] = 0
    trace["retrieval_mode"] = "generic"
    trace["fallback_reason"] = trace.get("fallback_reason") or "no_relevant_chunks"
    _set_trace(trace)
    return []
