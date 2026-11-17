"""SQLite 하나로 끝내는 지식 저장소.

- 별도 벡터 DB 서버가 필요 없습니다. 파일 하나(index.sqlite3)라 복사·백업·폰으로 옮기기 쉽습니다.
- 임베딩은 float32 바이트(BLOB)로 저장하고, 검색할 때 numpy 행렬로 한 번에 읽어 코사인 유사도를 계산합니다.
  조각이 수만 개 수준이면 이 방식으로도 수 ms 안에 검색됩니다.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nunchi.rag.chunker import Chunk
from nunchi.rag.loaders import Document

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    source TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    chars INTEGER NOT NULL,
    added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    heading TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
"""


@dataclass
class StoredChunk:
    id: int
    doc_id: int
    source: str
    title: str
    heading: str
    text: str

    @property
    def label(self) -> str:
        return " > ".join(x for x in [self.title, self.heading] if x)

    def search_text(self) -> str:
        return Chunk(ord=0, heading=self.heading, text=self.text).embed_text(self.title)


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)

    def stamp(self) -> str:
        """내용이 바뀔 때마다 달라지는 값. 검색기는 이 값이 바뀌면 캐시를 다시 만듭니다.
        (CLI로 색인하는 동안 서버가 떠 있어도 자동으로 새 내용을 반영하기 위함)"""
        return self.get_meta("updated_at", "0") or "0"

    def _touch(self) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('updated_at', ?)", (f"{time.time():.6f}",)
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # meta -------------------------------------------------------------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))

    # documents ----------------------------------------------------------
    def get_document(self, source: str) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute("SELECT * FROM documents WHERE source=?", (source,)).fetchone()

    def list_documents(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT d.*, COUNT(c.id) AS n_chunks FROM documents d "
                "LEFT JOIN chunks c ON c.doc_id=d.id GROUP BY d.id ORDER BY d.source"
            ).fetchall()
        return [dict(r) for r in rows]

    def replace_document(self, doc: Document, chunks: list[Chunk], embeddings: np.ndarray | None) -> int:
        if embeddings is not None and len(embeddings) != len(chunks):
            raise ValueError("조각 수와 임베딩 수가 달라요")
        with self._lock, self._db:
            self._db.execute("DELETE FROM documents WHERE source=?", (doc.source,))
            cur = self._db.execute(
                "INSERT INTO documents(source, title, kind, sha256, chars, added_at) VALUES(?,?,?,?,?,?)",
                (doc.source, doc.title, doc.kind, doc.sha256, len(doc.text), time.time()),
            )
            doc_id = int(cur.lastrowid)
            rows = []
            for i, ch in enumerate(chunks):
                blob = None
                if embeddings is not None:
                    blob = np.asarray(embeddings[i], dtype=np.float32).tobytes()
                rows.append((doc_id, ch.ord, ch.heading, ch.text, blob))
            self._db.executemany(
                "INSERT INTO chunks(doc_id, ord, heading, text, embedding) VALUES(?,?,?,?,?)", rows
            )
            self._touch()
        return doc_id

    def delete_document(self, source: str) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM documents WHERE source=?", (source,))
            self._touch()
        return cur.rowcount > 0

    # chunks -------------------------------------------------------------
    def load_chunks(self) -> list[StoredChunk]:
        with self._lock:
            rows = self._db.execute(
                "SELECT c.id, c.doc_id, d.source, d.title, c.heading, c.text FROM chunks c "
                "JOIN documents d ON d.id=c.doc_id ORDER BY d.source, c.ord"
            ).fetchall()
        return [StoredChunk(**dict(r)) for r in rows]

    def load_embeddings(self) -> tuple[np.ndarray, np.ndarray]:
        """(chunk_id 배열, 정규화된 임베딩 행렬). 임베딩이 없는 조각은 빠집니다."""
        with self._lock:
            rows = self._db.execute("SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL").fetchall()
        if not rows:
            return np.zeros(0, dtype=np.int64), np.zeros((0, 0), dtype=np.float32)
        dims = {len(r["embedding"]) for r in rows}
        dim_bytes = max(dims, key=lambda d: sum(1 for r in rows if len(r["embedding"]) == d))
        rows = [r for r in rows if len(r["embedding"]) == dim_bytes]  # 섞인 차원은 제외
        ids = np.array([r["id"] for r in rows], dtype=np.int64)
        mat = np.frombuffer(b"".join(r["embedding"] for r in rows), dtype=np.float32).reshape(len(rows), -1)
        return ids, mat

    def chunks_without_embeddings(self) -> list[StoredChunk]:
        with self._lock:
            rows = self._db.execute(
                "SELECT c.id, c.doc_id, d.source, d.title, c.heading, c.text FROM chunks c "
                "JOIN documents d ON d.id=c.doc_id WHERE c.embedding IS NULL ORDER BY c.id"
            ).fetchall()
        return [StoredChunk(**dict(r)) for r in rows]

    def set_embeddings(self, ids: list[int], embeddings: np.ndarray) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE chunks SET embedding=? WHERE id=?",
                [(np.asarray(e, dtype=np.float32).tobytes(), int(i)) for i, e in zip(ids, embeddings)],
            )
            self._touch()

    def clear_embeddings(self) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE chunks SET embedding=NULL")
            self._touch()

    def stats(self) -> dict:
        with self._lock:
            n_docs = self._db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            n_chunks = self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            n_emb = self._db.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
        return {
            "documents": n_docs,
            "chunks": n_chunks,
            "embedded_chunks": n_emb,
            "embed_model": self.get_meta("embed_model"),
            "path": self.path,
        }
