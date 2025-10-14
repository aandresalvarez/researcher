from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Tuple

from uamm.rag.corpus import add_doc as rag_add_doc
from uamm.rag.vector_store import (
    LanceDBUnavailable,
    upsert_document_embedding,
)
from uamm.security.redaction import redact
import logging


ALLOWED_EXTS = {".txt", ".md", ".markdown", ".html", ".htm", ".pdf", ".docx"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB per file default safeguard


def _read_file_text(path: Path) -> Optional[Tuple[str, str]]:
    if not path.is_file():
        return None
    if path.suffix.lower() not in ALLOWED_EXTS:
        return None
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
    except Exception:
        return None
    ext = path.suffix.lower()
    # Structured parsers first
    if ext in {".pdf"}:
        text = _parse_pdf(path)
        if text is None or not text.strip():
            return None
        return path.stem, text
    if ext in {".docx"}:
        text = _parse_docx(path)
        if text is None:
            return None
        return path.stem, text
    # Plain text/HTML
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    title = path.stem
    if ext in {".html", ".htm"}:
        text = re.sub(r"<[^>]+>", " ", raw)
    else:
        text = raw
    return title, text


def _parse_pdf(path: Path) -> Optional[str]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        logging.getLogger("uamm.rag.ingest").warning(
            "pdf_parser_missing", extra={"path": str(path)}
        )
        return None
    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n\n".join(p.strip() for p in parts if p and p.strip())
        return text or None
    except Exception as exc:
        logging.getLogger("uamm.rag.ingest").warning(
            "pdf_parse_failed", extra={"path": str(path), "error": str(exc)}
        )
        return None


def _parse_docx(path: Path) -> Optional[str]:
    try:
        import docx  # type: ignore
    except Exception:
        logging.getLogger("uamm.rag.ingest").warning(
            "docx_parser_missing", extra={"path": str(path)}
        )
        return None
    try:
        doc = docx.Document(str(path))  # type: ignore[attr-defined]
        parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        return "\n\n".join(parts) or None
    except Exception as exc:
        logging.getLogger("uamm.rag.ingest").warning(
            "docx_parse_failed", extra={"path": str(path), "error": str(exc)}
        )
        return None


def _ocr_pdf(path: Path) -> Optional[str]:
    """Best-effort OCR for scanned PDFs.

    Requires external dependencies and system binaries:
    - pdf2image (and poppler) to render pages to images
    - pytesseract (and tesseract) to OCR each page
    Returns None on failure.
    """
    try:
        from pdf2image import convert_from_path  # type: ignore
        import pytesseract  # type: ignore
    except Exception:
        logging.getLogger("uamm.rag.ingest").warning(
            "ocr_deps_missing", extra={"path": str(path)}
        )
        return None
    try:
        images = convert_from_path(str(path))
        parts: list[str] = []
        for img in images:
            try:
                txt = pytesseract.image_to_string(img)
                if txt:
                    parts.append(txt)
            except Exception:
                continue
        return "\n\n".join(p.strip() for p in parts if p and p.strip()) or None
    except Exception as exc:
        logging.getLogger("uamm.rag.ingest").warning(
            "ocr_failed", extra={"path": str(path), "error": str(exc)}
        )
        return None
    


def chunk_text(text: str, *, chunk_chars: int, overlap_chars: int) -> list[str]:
    chunk_chars = max(200, int(chunk_chars or 1400))
    overlap_chars = max(0, int(overlap_chars or 200))
    clean = (text or "").strip()
    if not clean:
        return []
    out: list[str] = []
    i = 0
    n = len(clean)
    while i < n:
        j = min(n, i + chunk_chars)
        # try not to cut a word
        if j < n:
            k = clean.rfind(" ", i + int(0.8 * chunk_chars), j)
            if k != -1:
                j = k
        out.append(clean[i:j].strip())
        if j >= n:
            break
        # move window forward with overlap
        i = max(0, j - overlap_chars)
    return [seg for seg in out if seg]


def token_chunk_text(
    text: str, *, chunk_tokens: int, overlap_tokens: int, encoding: str = "cl100k_base"
) -> list[str]:
    """Chunk text by tokens using optional tiktoken.

    If tiktoken isn't available, falls back to character chunking using an
    approximate character size for the target token count.
    """
    try:
        import tiktoken  # type: ignore
    except Exception:
        approx_chars = max(200, int(chunk_tokens * 4))
        approx_overlap = max(0, int(overlap_tokens * 4))
        return chunk_text(text, chunk_chars=approx_chars, overlap_chars=approx_overlap)
    enc = tiktoken.get_encoding(encoding)
    toks = enc.encode(text or "")
    if not toks:
        return []
    chunks: list[str] = []
    i = 0
    n = len(toks)
    while i < n:
        j = min(n, i + max(50, int(chunk_tokens)))
        sub = toks[i:j]
        chunks.append(enc.decode(sub))
        if j >= n:
            break
        i = max(0, j - max(0, int(overlap_tokens)))
    return [c.strip() for c in chunks if c and c.strip()]


def make_chunks(text: str, *, settings=None) -> list[str]:
    if not settings:
        return chunk_text(text, chunk_chars=1400, overlap_chars=200)
    mode = str(getattr(settings, "docs_chunk_mode", "chars") or "chars").lower()
    if mode == "tokens":
        return token_chunk_text(
            text,
            chunk_tokens=getattr(settings, "docs_chunk_tokens", 600),
            overlap_tokens=getattr(settings, "docs_overlap_tokens", 100),
        )
    return chunk_text(
        text,
        chunk_chars=getattr(settings, "docs_chunk_chars", 1400),
        overlap_chars=getattr(settings, "docs_overlap_chars", 200),
    )


def _ensure_corpus_files_table(db_path: str) -> None:
    con = sqlite3.connect(db_path, check_same_thread=False)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_files (
              path TEXT PRIMARY KEY,
              mtime REAL,
              doc_id TEXT,
              meta TEXT
            )
            """
        )
        con.commit()
    finally:
        con.close()


def ingest_file(db_path: str, file_path: str, *, settings=None) -> Optional[str]:
    """Ingest a single file into the RAG corpus; returns doc_id or None if skipped.

    Skips unknown extensions and oversized files. When vector backend is enabled,
    stores an embedding for dense search.
    """
    path = Path(file_path)
    payload = _read_file_text(path)
    if payload is None:
        # Fallback to OCR for PDF if enabled
        if getattr(settings, "docs_ocr_enabled", True) and path.suffix.lower() == ".pdf":
            text = _ocr_pdf(path)
            if text and text.strip():
                payload = (path.stem, text)
        if payload is None:
            return None
    title, text = payload
    # Redact before persisting
    text, _ = redact(text)
    url = f"file:{path}"
    meta: Dict[str, str] = {"path": str(path), "source": "local_folder"}

    # Chunking
    chunks = make_chunks(text, settings=settings)
    if not chunks:
        chunks = [text]

    first_id: Optional[str] = None
    chunk_ids: list[str] = []
    total = len(chunks)
    for idx, segment in enumerate(chunks):
        meta_i = dict(meta)
        meta_i.update({"chunk_index": idx, "chunk_total": total})
        did = rag_add_doc(
            db_path,
            title=title,
            url=url,
            text=segment,
            meta=meta_i,
            workspace=getattr(settings, "workspace", getattr(settings, "default_workspace", "default")) if settings else None,
            created_by="system:ingest",
        )
        if first_id is None:
            first_id = did
        chunk_ids.append(did)
        # Optional vector embedding per chunk
        try:
            if settings is not None and getattr(settings, "vector_backend", "none").lower() == "lancedb":
                upsert_document_embedding(
                    settings, did, segment, meta={"title": title, **meta_i}
                )
        except LanceDBUnavailable:
            pass
        except Exception:
            pass

    # Record file ingestion metadata
    _ensure_corpus_files_table(db_path)
    con = sqlite3.connect(db_path, check_same_thread=False)
    try:
        mtime = path.stat().st_mtime
        # Store the first chunk id and a small meta payload
        meta_blob = "{" + f"\"chunks\":{len(chunk_ids)}" + "}"
        con.execute(
            "INSERT OR REPLACE INTO corpus_files(path, mtime, doc_id, meta) VALUES (?, ?, ?, ?)",
            (str(path), mtime, first_id, meta_blob),
        )
        con.commit()
    finally:
        con.close()

    return first_id


def scan_folder(db_path: str, folder: str, *, settings=None) -> Dict[str, int]:
    """Scan a folder recursively and ingest new/changed files.

    Returns a dict with counts: {"ingested": n, "skipped": m}.
    """
    base = Path(folder)
    counts = {"ingested": 0, "skipped": 0}
    if not base.exists() or not base.is_dir():
        return counts
    _ensure_corpus_files_table(db_path)
    con = sqlite3.connect(db_path, check_same_thread=False)
    try:
        cur = con.execute("SELECT path, mtime FROM corpus_files")
        seen = {row[0]: float(row[1]) for row in cur.fetchall()}
    finally:
        con.close()

    for path in base.rglob("*"):
        if path.is_dir() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in ALLOWED_EXTS:
            counts["skipped"] += 1
            continue
        try:
            mtime = path.stat().st_mtime
        except Exception:
            counts["skipped"] += 1
            continue
        prior = seen.get(str(path))
        if prior is not None and mtime <= prior:
            counts["skipped"] += 1
            continue
        did = ingest_file(db_path, str(path), settings=settings)
        if did:
            counts["ingested"] += 1
        else:
            counts["skipped"] += 1
    return counts
