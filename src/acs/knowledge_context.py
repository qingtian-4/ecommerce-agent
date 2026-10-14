"""Small-corpus baselines using whole, versioned source documents."""

from __future__ import annotations

import hashlib

from .config import Settings
from .rag import _parse_frontmatter
from .sessions import TaskValidationError


def load_source_documents(settings: Settings) -> list[dict]:
    if not settings.docs_dir.is_dir():
        raise FileNotFoundError("知识正文目录不存在")
    documents = []
    for path in sorted(settings.docs_dir.glob("*.md")):
        data = path.read_bytes()
        meta, content = _parse_frontmatter(data.decode("utf-8"))
        documents.append({"document_id": meta.get("document_id", path.stem),
                          "title": meta.get("title", path.stem), "source": path.name,
                          "category": meta.get("category", "unknown"),
                          "product_id": meta.get("product_id"),
                          "version": meta.get("version"),
                          "effective_date": meta.get("effective_date"),
                          "hash": hashlib.sha256(data).hexdigest(), "content": content})
    if not documents:
        raise ValueError("知识正文目录为空")
    return documents


def select_source_documents(settings: Settings, document_ids: list[str]) -> list[dict]:
    if (not isinstance(document_ids, list) or not document_ids
            or not all(isinstance(d, str) for d in document_ids)):
        raise TaskValidationError("document_ids须为非空文档标识列表")
    documents = {d["document_id"]: d for d in load_source_documents(settings)}
    if any(d not in documents for d in document_ids):
        raise TaskValidationError("未知文档标识，请从工具列出的目录选择")
    return [documents[d] for d in dict.fromkeys(document_ids)]
