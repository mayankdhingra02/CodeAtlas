from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, DEFAULT_IGNORE_DIRS, resolve_repo_root
from .models import (
    ArchitectureFinding,
    CoChangeLink,
    CompressedContext,
    ComponentSummary,
    DecisionAnswer,
    EvidenceRef,
    HistoryEvent,
    HotspotEntry,
    ImpactReport,
    ImpactedFile,
    MemoryEntity,
    MemoryEntityKind,
    MemoryEvidence,
    MemoryIndexReport,
    MemoryRelationship,
    MemoryRelationshipKind,
    OwnershipEntry,
    TokenReport,
    estimate_tokens,
)
from .retrieval import RetrievalEngine


ARCHITECTURE_TERMS = {
    "api",
    "architecture",
    "auth",
    "authentication",
    "authorization",
    "cache",
    "database",
    "docker",
    "gateway",
    "jwt",
    "kafka",
    "kubernetes",
    "migration",
    "microservice",
    "postgres",
    "queue",
    "redis",
    "retry",
    "service",
    "terraform",
}

DOCUMENT_PATTERNS = (
    "README*",
    "CHANGELOG*",
    "RELEASE*",
    "docs/**/*.md",
    "docs/**/*.rst",
    "docs/**/*.txt",
    "adr/**/*.md",
    "adrs/**/*.md",
    "rfcs/**/*.md",
    "design/**/*.md",
    "designs/**/*.md",
)


@dataclass(frozen=True)
class GitCommitRecord:
    sha: str
    author_name: str
    author_email: str
    timestamp: str
    subject: str
    files: tuple[str, ...]


class MemoryStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(database_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_entities (
              key TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              name TEXT NOT NULL,
              summary TEXT NOT NULL,
              confidence REAL NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS memory_evidence (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_type TEXT NOT NULL,
              source_id TEXT NOT NULL,
              path TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL,
              snippet TEXT NOT NULL,
              author TEXT,
              timestamp TEXT,
              url TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              content_hash TEXT NOT NULL,
              UNIQUE(source_type, source_id, path, title)
            );

            CREATE TABLE IF NOT EXISTS memory_relationships (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_key TEXT NOT NULL,
              target_key TEXT NOT NULL,
              relationship TEXT NOT NULL,
              confidence REAL NOT NULL,
              evidence_id INTEGER REFERENCES memory_evidence(id) ON DELETE SET NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              UNIQUE(source_key, target_key, relationship, evidence_id)
            );

            CREATE INDEX IF NOT EXISTS idx_memory_entities_kind
              ON memory_entities(kind);
            CREATE INDEX IF NOT EXISTS idx_memory_entities_name
              ON memory_entities(name);
            CREATE INDEX IF NOT EXISTS idx_memory_evidence_source
              ON memory_evidence(source_type, source_id);
            CREATE INDEX IF NOT EXISTS idx_memory_evidence_timestamp
              ON memory_evidence(timestamp);
            CREATE INDEX IF NOT EXISTS idx_memory_relationships_source
              ON memory_relationships(source_key);
            CREATE INDEX IF NOT EXISTS idx_memory_relationships_target
              ON memory_relationships(target_key);
            CREATE INDEX IF NOT EXISTS idx_memory_relationships_type
              ON memory_relationships(relationship);

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_evidence_fts
              USING fts5(evidence_id UNINDEXED, title, snippet, path);
            """
        )
        self.connection.commit()

    def clear(self) -> None:
        self.connection.executescript(
            """
            DELETE FROM memory_relationships;
            DELETE FROM memory_evidence_fts;
            DELETE FROM memory_evidence;
            DELETE FROM memory_entities;
            """
        )
        self.connection.commit()

    def upsert_entity(self, entity: MemoryEntity) -> None:
        self.connection.execute(
            """
            INSERT INTO memory_entities(key, kind, name, summary, confidence, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              kind = excluded.kind,
              name = excluded.name,
              summary = excluded.summary,
              confidence = max(memory_entities.confidence, excluded.confidence),
              metadata_json = excluded.metadata_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                entity.key,
                entity.kind.value,
                entity.name,
                entity.summary,
                entity.confidence,
                json.dumps(entity.metadata, sort_keys=True),
            ),
        )

    def upsert_evidence(self, evidence: MemoryEvidence) -> int:
        path = evidence.path or ""
        content_hash = hashlib.sha256(
            "|".join(
                [
                    evidence.source_type,
                    evidence.source_id,
                    path,
                    evidence.title,
                    evidence.snippet,
                ]
            ).encode("utf-8")
        ).hexdigest()
        self.connection.execute(
            """
            INSERT INTO memory_evidence(
              source_type, source_id, path, title, snippet, author, timestamp, url,
              metadata_json, content_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_type, source_id, path, title) DO UPDATE SET
              snippet = excluded.snippet,
              author = excluded.author,
              timestamp = excluded.timestamp,
              url = excluded.url,
              metadata_json = excluded.metadata_json,
              content_hash = excluded.content_hash
            """,
            (
                evidence.source_type,
                evidence.source_id,
                path,
                evidence.title,
                evidence.snippet,
                evidence.author,
                evidence.timestamp,
                evidence.url,
                json.dumps(evidence.metadata, sort_keys=True),
                content_hash,
            ),
        )
        row = self.connection.execute(
            """
            SELECT id FROM memory_evidence
            WHERE source_type = ? AND source_id = ? AND path = ? AND title = ?
            """,
            (evidence.source_type, evidence.source_id, path, evidence.title),
        ).fetchone()
        evidence_id = int(row["id"])
        self.connection.execute(
            "DELETE FROM memory_evidence_fts WHERE evidence_id = ?",
            (evidence_id,),
        )
        self.connection.execute(
            """
            INSERT INTO memory_evidence_fts(evidence_id, title, snippet, path)
            VALUES (?, ?, ?, ?)
            """,
            (evidence_id, evidence.title, evidence.snippet, path),
        )
        return evidence_id

    def insert_relationship(self, relationship: MemoryRelationship) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO memory_relationships(
              source_key, target_key, relationship, confidence, evidence_id, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                relationship.source_key,
                relationship.target_key,
                relationship.relationship.value,
                relationship.confidence,
                relationship.evidence_id,
                json.dumps(relationship.metadata, sort_keys=True),
            ),
        )

    def commit(self) -> None:
        self.connection.commit()

    def counts(self) -> dict[str, int]:
        row = self.connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM memory_entities) AS entities,
              (SELECT COUNT(*) FROM memory_relationships) AS relationships,
              (SELECT COUNT(*) FROM memory_evidence) AS evidence
            """
        ).fetchone()
        return {
            "entities": int(row["entities"]),
            "relationships": int(row["relationships"]),
            "evidence": int(row["evidence"]),
        }

    def search_evidence(self, query: str, *, limit: int = 30) -> list[sqlite3.Row]:
        terms = search_terms(query)
        if not terms:
            return []
        fts_query = " OR ".join(escape_fts_token(term) for term in terms)
        try:
            rows = self.connection.execute(
                """
                SELECT e.*
                FROM memory_evidence_fts f
                JOIN memory_evidence e ON e.id = f.evidence_id
                WHERE memory_evidence_fts MATCH ?
                ORDER BY bm25(memory_evidence_fts), COALESCE(e.timestamp, '') DESC
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
            if rows:
                return list(rows)
        except sqlite3.OperationalError:
            pass
        clauses = []
        params: list[str] = []
        for term in terms:
            like = f"%{term.lower()}%"
            clauses.append(
                "(LOWER(title) LIKE ? OR LOWER(snippet) LIKE ? OR LOWER(path) LIKE ?)"
            )
            params.extend([like, like, like])
        rows = self.connection.execute(
            f"""
            SELECT * FROM memory_evidence
            WHERE {' OR '.join(clauses)}
            ORDER BY COALESCE(timestamp, '') DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return list(rows)

    def commit_evidence(self, *, limit: int = 1000) -> list[sqlite3.Row]:
        rows = self.connection.execute(
            """
            SELECT * FROM memory_evidence
            WHERE source_type = 'commit'
            ORDER BY COALESCE(timestamp, '') DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return list(rows)

    def related_file_rows(self, file_paths: tuple[str, ...], *, limit: int = 20) -> list[sqlite3.Row]:
        if not file_paths:
            return []
        keys = [file_entity_key(path) for path in file_paths]
        placeholders = ",".join("?" for _ in keys)
        rows = self.connection.execute(
            f"""
            SELECT * FROM memory_relationships
            WHERE relationship = ?
              AND (source_key IN ({placeholders}) OR target_key IN ({placeholders}))
            ORDER BY confidence DESC, id DESC
            LIMIT ?
            """,
            (MemoryRelationshipKind.RELATED_TO.value, *keys, *keys, limit),
        ).fetchall()
        return list(rows)

    def search_entities(
        self,
        query: str,
        *,
        kinds: tuple[MemoryEntityKind, ...] = (),
        limit: int = 30,
    ) -> list[sqlite3.Row]:
        terms = search_terms(query)
        if not terms:
            return []
        clauses = []
        params: list[str] = []
        for term in terms:
            like = f"%{term.lower()}%"
            clauses.append("(LOWER(name) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(key) LIKE ?)")
            params.extend([like, like, like])
        kind_clause = ""
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            kind_clause = f" AND kind IN ({placeholders})"
            params.extend(kind.value for kind in kinds)
        rows = self.connection.execute(
            f"""
            SELECT * FROM memory_entities
            WHERE ({' OR '.join(clauses)}){kind_clause}
            ORDER BY confidence DESC, updated_at DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return list(rows)

    def evidence_for_source(self, source_type: str, source_id: str) -> list[sqlite3.Row]:
        rows = self.connection.execute(
            """
            SELECT * FROM memory_evidence
            WHERE source_type = ? AND source_id = ?
            ORDER BY COALESCE(timestamp, '') DESC, id DESC
            """,
            (source_type, source_id),
        ).fetchall()
        return list(rows)

    def relationships_for_key(self, key: str) -> list[sqlite3.Row]:
        rows = self.connection.execute(
            """
            SELECT * FROM memory_relationships
            WHERE source_key = ? OR target_key = ?
            ORDER BY confidence DESC, relationship
            """,
            (key, key),
        ).fetchall()
        return list(rows)


class RepositoryMemoryIndexer:
    def index(
        self,
        repo_path: str | Path,
        *,
        max_commits: int = 500,
        incremental: bool = False,
    ) -> MemoryIndexReport:
        start = time.perf_counter()
        repo_root = resolve_repo_root(repo_path)
        paths = CodeAtlasPaths(repo_root)
        store = MemoryStore(paths.database_path)
        warnings: list[str] = []
        commits_indexed = 0
        documents_indexed = 0
        git_available = False
        try:
            store.initialize()
            if not incremental:
                store.clear()
            repository_entity = MemoryEntity(
                key="repository:local",
                kind=MemoryEntityKind.REPOSITORY,
                name=repo_root.name,
                summary=f"Local repository at {repo_root}",
                confidence=1.0,
                metadata={"path": str(repo_root)},
            )
            store.upsert_entity(repository_entity)

            git_available = self._is_git_repo(repo_root)
            if git_available:
                commits = self._load_commits(repo_root, max_commits=max_commits)
                for commit in commits:
                    self._index_commit(store, commit)
                commits_indexed = len(commits)
            else:
                warnings.append("Git history was not available; only repository documents were indexed.")

            for document_path in iter_memory_documents(repo_root):
                self._index_document(store, repo_root, document_path)
                documents_indexed += 1

            store.commit()
            counts = store.counts()
            return MemoryIndexReport(
                repo_root=repo_root,
                database_path=paths.database_path,
                duration_seconds=time.perf_counter() - start,
                git_available=git_available,
                commits_indexed=commits_indexed,
                documents_indexed=documents_indexed,
                entities_indexed=counts["entities"],
                relationships_indexed=counts["relationships"],
                evidence_indexed=counts["evidence"],
                warnings=tuple(warnings),
            )
        finally:
            store.close()

    def _is_git_repo(self, repo_root: Path) -> bool:
        result = run_git(repo_root, ["rev-parse", "--is-inside-work-tree"], check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _load_commits(self, repo_root: Path, *, max_commits: int) -> tuple[GitCommitRecord, ...]:
        result = run_git(
            repo_root,
            [
                "log",
                f"--max-count={max_commits}",
                "--reverse",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%an%x1f%ae%x1f%aI%x1f%s",
                "--name-only",
            ],
        )
        records: list[GitCommitRecord] = []
        current: dict[str, Any] | None = None
        for line in result.stdout.splitlines():
            if line.count("\x1f") >= 4:
                if current is not None:
                    records.append(commit_from_payload(current))
                sha, author_name, author_email, timestamp, subject = line.split("\x1f", 4)
                current = {
                    "sha": sha,
                    "author_name": author_name,
                    "author_email": author_email,
                    "timestamp": timestamp,
                    "subject": subject,
                    "files": [],
                }
            elif current is not None and line.strip():
                current["files"].append(line.strip())
        if current is not None:
            records.append(commit_from_payload(current))
        return tuple(records)

    def _index_commit(self, store: MemoryStore, commit: GitCommitRecord) -> None:
        author_key = developer_key(commit.author_email or commit.author_name)
        commit_key = f"commit:{commit.sha}"
        purpose = infer_commit_purpose(commit.subject)
        motivation = infer_motivation(commit.subject)
        components = infer_components(commit.files)
        feature_terms = infer_feature_terms(commit.subject, commit.files)
        risk = infer_risk_level(commit.subject, commit.files)
        architectural_impact = infer_architectural_impact(commit.subject, commit.files)

        evidence_id = store.upsert_evidence(
            MemoryEvidence(
                id=None,
                source_type="commit",
                source_id=commit.sha,
                title=commit.subject,
                snippet=commit_snippet(commit, purpose, motivation, components, risk),
                author=commit.author_name,
                timestamp=commit.timestamp,
                metadata={
                    "files": list(commit.files),
                    "purpose": purpose,
                    "motivation": motivation,
                    "risk": risk,
                    "architectural_impact": architectural_impact,
                    "author_email": commit.author_email,
                },
            )
        )
        store.upsert_entity(
            MemoryEntity(
                key=author_key,
                kind=MemoryEntityKind.DEVELOPER,
                name=commit.author_name or commit.author_email,
                summary=f"{commit.author_name} contributed repository changes.",
                confidence=0.95,
                metadata={"email": commit.author_email},
            )
        )
        store.upsert_entity(
            MemoryEntity(
                key=commit_key,
                kind=MemoryEntityKind.COMMIT,
                name=commit.sha[:7],
                summary=purpose,
                confidence=0.86,
                metadata={
                    "sha": commit.sha,
                    "subject": commit.subject,
                    "timestamp": commit.timestamp,
                    "files": list(commit.files),
                    "motivation": motivation,
                    "risk": risk,
                    "architectural_impact": architectural_impact,
                },
            )
        )
        store.insert_relationship(
            MemoryRelationship(
                source_key=author_key,
                target_key=commit_key,
                relationship=MemoryRelationshipKind.CONTRIBUTES_TO,
                confidence=0.95,
                evidence_id=evidence_id,
            )
        )
        store.insert_relationship(
            MemoryRelationship(
                source_key=commit_key,
                target_key="repository:local",
                relationship=MemoryRelationshipKind.MODIFIED_BY,
                confidence=0.88,
                evidence_id=evidence_id,
            )
        )

        for component in components:
            module_key = f"module:{slug(component)}"
            store.upsert_entity(
                MemoryEntity(
                    key=module_key,
                    kind=MemoryEntityKind.MODULE,
                    name=component,
                    summary=f"Repository area inferred from paths touched by commits: {component}.",
                    confidence=0.72,
                    metadata={"component": component},
                )
            )
            store.insert_relationship(
                MemoryRelationship(
                    source_key=module_key,
                    target_key=author_key,
                    relationship=MemoryRelationshipKind.MODIFIED_BY,
                    confidence=0.74,
                    evidence_id=evidence_id,
                    metadata={"commit": commit.sha},
                )
            )
            store.insert_relationship(
                MemoryRelationship(
                    source_key=commit_key,
                    target_key=module_key,
                    relationship=MemoryRelationshipKind.RELATED_TO,
                    confidence=0.76,
                    evidence_id=evidence_id,
                )
            )

        for file_path in commit.files:
            file_key = file_entity_key(file_path)
            store.upsert_entity(
                MemoryEntity(
                    key=file_key,
                    kind=MemoryEntityKind.FILE,
                    name=file_path,
                    summary=f"File touched by repository history: {file_path}.",
                    confidence=0.9,
                    metadata={"path": file_path, "component": component_for_path(file_path)},
                )
            )
            store.insert_relationship(
                MemoryRelationship(
                    source_key=commit_key,
                    target_key=file_key,
                    relationship=MemoryRelationshipKind.RELATED_TO,
                    confidence=0.86,
                    evidence_id=evidence_id,
                )
            )
            store.insert_relationship(
                MemoryRelationship(
                    source_key=file_key,
                    target_key=author_key,
                    relationship=MemoryRelationshipKind.MODIFIED_BY,
                    confidence=0.84,
                    evidence_id=evidence_id,
                    metadata={"commit": commit.sha},
                )
            )

        for left, right in cochange_pairs(commit.files):
            left_key = file_entity_key(left)
            right_key = file_entity_key(right)
            store.insert_relationship(
                MemoryRelationship(
                    source_key=left_key,
                    target_key=right_key,
                    relationship=MemoryRelationshipKind.RELATED_TO,
                    confidence=0.66,
                    evidence_id=evidence_id,
                    metadata={"commit": commit.sha, "cochange": True},
                )
            )

        for term in feature_terms:
            feature_key = f"feature:{slug(term)}"
            store.upsert_entity(
                MemoryEntity(
                    key=feature_key,
                    kind=MemoryEntityKind.FEATURE,
                    name=term,
                    summary=f"Feature/topic inferred from commit text or changed paths: {term}.",
                    confidence=0.68,
                    metadata={"term": term},
                )
            )
            relationship = (
                MemoryRelationshipKind.INTRODUCED_BY
                if is_introductory_commit(commit.subject)
                else MemoryRelationshipKind.MODIFIED_BY
            )
            store.insert_relationship(
                MemoryRelationship(
                    source_key=feature_key,
                    target_key=commit_key,
                    relationship=relationship,
                    confidence=0.7,
                    evidence_id=evidence_id,
                )
            )

        pr_number = pull_request_number(commit.subject)
        if pr_number is not None:
            pr_key = f"pull_request:{pr_number}"
            store.upsert_entity(
                MemoryEntity(
                    key=pr_key,
                    kind=MemoryEntityKind.PULL_REQUEST,
                    name=f"PR #{pr_number}",
                    summary=f"Pull request inferred from commit message: {commit.subject}",
                    confidence=0.62,
                    metadata={"number": pr_number},
                )
            )
            store.insert_relationship(
                MemoryRelationship(
                    source_key=pr_key,
                    target_key=commit_key,
                    relationship=MemoryRelationshipKind.RELATED_TO,
                    confidence=0.62,
                    evidence_id=evidence_id,
                )
            )

        if architectural_impact != "low":
            decision_key = f"decision:{commit.sha[:12]}"
            store.upsert_entity(
                MemoryEntity(
                    key=decision_key,
                    kind=MemoryEntityKind.ARCHITECTURE_DECISION,
                    name=commit.subject,
                    summary=(
                        f"Architectural signal from commit: {purpose}. "
                        f"Impact assessed as {architectural_impact}."
                    ),
                    confidence=0.58 if architectural_impact == "medium" else 0.72,
                    metadata={
                        "commit": commit.sha,
                        "impact": architectural_impact,
                        "risk": risk,
                    },
                )
            )
            store.insert_relationship(
                MemoryRelationship(
                    source_key=decision_key,
                    target_key=commit_key,
                    relationship=MemoryRelationshipKind.INTRODUCED_BY,
                    confidence=0.7,
                    evidence_id=evidence_id,
                )
            )

    def _index_document(self, store: MemoryStore, repo_root: Path, path: Path) -> None:
        relative_path = path.relative_to(repo_root).as_posix()
        content = path.read_text(encoding="utf-8", errors="replace")
        title = document_title(content, relative_path)
        snippet = compact_snippet(content)
        lower_path = relative_path.lower()
        lower_content = content.lower()
        source_id = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]
        kind = MemoryEntityKind.REPOSITORY_EVENT
        if "adr" in lower_path or "architecture decision" in lower_content:
            kind = MemoryEntityKind.ARCHITECTURE_DECISION
        elif "release" in lower_path or "changelog" in lower_path:
            kind = MemoryEntityKind.RELEASE
        elif "incident" in lower_path or "postmortem" in lower_content:
            kind = MemoryEntityKind.INCIDENT

        evidence_id = store.upsert_evidence(
            MemoryEvidence(
                id=None,
                source_type="document",
                source_id=source_id,
                title=title,
                snippet=snippet,
                path=relative_path,
                metadata={"kind": kind.value},
            )
        )
        entity_key = f"{kind.value.lower()}:{source_id}"
        store.upsert_entity(
            MemoryEntity(
                key=entity_key,
                kind=kind,
                name=title,
                summary=snippet,
                confidence=0.82,
                metadata={"path": relative_path},
            )
        )
        store.insert_relationship(
            MemoryRelationship(
                source_key=entity_key,
                target_key="repository:local",
                relationship=MemoryRelationshipKind.RELATED_TO,
                confidence=0.8,
                evidence_id=evidence_id,
            )
        )
        for term in infer_terms_from_text(f"{relative_path}\n{title}\n{content}"):
            feature_key = f"feature:{slug(term)}"
            store.upsert_entity(
                MemoryEntity(
                    key=feature_key,
                    kind=MemoryEntityKind.FEATURE,
                    name=term,
                    summary=f"Repository topic mentioned in evidence: {term}.",
                    confidence=0.62,
                    metadata={"term": term},
                )
            )
            store.insert_relationship(
                MemoryRelationship(
                    source_key=entity_key,
                    target_key=feature_key,
                    relationship=MemoryRelationshipKind.RELATED_TO,
                    confidence=0.64,
                    evidence_id=evidence_id,
                )
            )


class MemoryQueryEngine:
    def index_memory(
        self,
        repo_path: str | Path,
        *,
        max_commits: int = 500,
        incremental: bool = False,
    ) -> MemoryIndexReport:
        return RepositoryMemoryIndexer().index(
            repo_path, max_commits=max_commits, incremental=incremental
        )

    def search_memory(
        self,
        repo_path: str | Path,
        query: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        store = self._open_store(repo_path)
        try:
            rows = store.search_evidence(query, limit=limit)
            return [evidence_row_to_ref(row).__dict__ for row in rows]
        finally:
            store.close()

    def history(self, repo_path: str | Path, topic: str, *, limit: int = 20) -> tuple[HistoryEvent, ...]:
        store = self._open_store(repo_path)
        try:
            evidence_rows = store.search_evidence(topic, limit=limit * 2)
            events: list[HistoryEvent] = []
            seen: set[str] = set()
            for row in evidence_rows:
                source_key = f"{row['source_type']}:{row['source_id']}"
                if source_key in seen:
                    continue
                seen.add(source_key)
                metadata = parse_json(row["metadata_json"])
                entity_kind = (
                    MemoryEntityKind.COMMIT.value
                    if row["source_type"] == "commit"
                    else str(metadata.get("kind", MemoryEntityKind.REPOSITORY_EVENT.value))
                )
                events.append(
                    HistoryEvent(
                        date=row["timestamp"],
                        title=str(row["title"]),
                        summary=str(row["snippet"]),
                        entity_key=source_key,
                        entity_kind=entity_kind,
                        confidence=score_match(topic, str(row["title"]), str(row["snippet"])),
                        evidence=(evidence_row_to_ref(row),),
                    )
                )
            events.sort(key=lambda event: event.date or "")
            return tuple(events[:limit])
        finally:
            store.close()

    def ownership(
        self,
        repo_path: str | Path,
        topic: str,
        *,
        limit: int = 10,
    ) -> tuple[OwnershipEntry, ...]:
        store = self._open_store(repo_path)
        try:
            evidence_rows = [
                row for row in store.search_evidence(topic, limit=200) if row["source_type"] == "commit"
            ]
            by_author: dict[str, list[sqlite3.Row]] = defaultdict(list)
            for row in evidence_rows:
                author = str(row["author"] or "Unknown")
                by_author[author].append(row)
            entries: list[OwnershipEntry] = []
            for author, rows in by_author.items():
                files = set()
                email = None
                for row in rows:
                    metadata = parse_json(row["metadata_json"])
                    files.update(str(path) for path in metadata.get("files", ()))
                    email = metadata.get("author_email") or email
                commits = len({str(row["source_id"]) for row in rows})
                score = commits * 2.0 + len(files) * 0.5
                entries.append(
                    OwnershipEntry(
                        developer=author,
                        email=email,
                        expertise_score=score,
                        commits=commits,
                        files_touched=len(files),
                        last_active=max(str(row["timestamp"] or "") for row in rows) or None,
                        evidence=tuple(evidence_row_to_ref(row) for row in rows[:3]),
                    )
                )
            entries.sort(key=lambda entry: (-entry.expertise_score, entry.developer))
            return tuple(entries[:limit])
        finally:
            store.close()

    def decisions(
        self,
        repo_path: str | Path,
        question: str,
        *,
        limit: int = 5,
    ) -> tuple[DecisionAnswer, ...]:
        store = self._open_store(repo_path)
        try:
            entity_rows = store.search_entities(
                question,
                kinds=(MemoryEntityKind.ARCHITECTURE_DECISION,),
                limit=limit,
            )
            evidence_rows = store.search_evidence(question, limit=limit * 2)
            decision_evidence = [
                row
                for row in evidence_rows
                if row["source_type"] == "document"
                or infer_architectural_impact(str(row["title"]), metadata_files(row)) != "low"
            ]
            answers: list[DecisionAnswer] = []
            for row in entity_rows:
                answers.append(
                    DecisionAnswer(
                        question=question,
                        answer=str(row["summary"]),
                        confidence=float(row["confidence"]),
                        evidence=tuple(
                            evidence_row_to_ref(evidence)
                            for evidence in decision_evidence[:3]
                        ),
                    )
                )
            if not answers and decision_evidence:
                evidence = decision_evidence[0]
                answers.append(
                    DecisionAnswer(
                        question=question,
                        answer=(
                            "CodeAtlas found related evidence, but no explicit architecture "
                            "decision record. Treat this as inferred context."
                        ),
                        confidence=evidence_row_to_ref(evidence).confidence,
                        evidence=(evidence_row_to_ref(evidence),),
                    )
                )
            if not answers:
                answers.append(
                    DecisionAnswer(
                        question=question,
                        answer="No evidence-backed decision was found in indexed memory.",
                        confidence=0.0,
                        evidence=(),
                    )
                )
            return tuple(answers[:limit])
        finally:
            store.close()

    def architecture(
        self,
        repo_path: str | Path,
        topic: str,
        *,
        limit: int = 8,
    ) -> tuple[ArchitectureFinding, ...]:
        store = self._open_store(repo_path)
        try:
            evidence_rows = [
                row
                for row in store.search_evidence(topic, limit=limit * 4)
                if infer_architectural_impact(str(row["title"]), metadata_files(row)) != "low"
                or any(term in str(row["snippet"]).lower() for term in ARCHITECTURE_TERMS)
            ]
            findings: list[ArchitectureFinding] = []
            for row in evidence_rows[:limit]:
                findings.append(
                    ArchitectureFinding(
                        topic=topic,
                        summary=str(row["snippet"]),
                        confidence=evidence_row_to_ref(row).confidence,
                        evidence=(evidence_row_to_ref(row),),
                    )
                )
            if not findings:
                findings.append(
                    ArchitectureFinding(
                        topic=topic,
                        summary="No architecture-specific evidence found in indexed memory.",
                        confidence=0.0,
                        evidence=(),
                    )
                )
            return tuple(findings)
        finally:
            store.close()

    def compressed_context(
        self,
        repo_path: str | Path,
        query: str,
        *,
        max_tokens: int = 4000,
    ) -> CompressedContext:
        history = self.history(repo_path, query, limit=5)
        decisions = self.decisions(repo_path, query, limit=3)
        ownership = self.ownership(repo_path, query, limit=3)
        architecture = self.architecture(repo_path, query, limit=4)
        dependencies: dict[str, Any]
        critical_files: tuple[str, ...]
        relevant_context: tuple[str, ...]
        try:
            retrieval = RetrievalEngine().retrieve(repo_path, query, depth=2, max_tokens=max_tokens)
            dependencies = {
                "token_report": retrieval.token_report.__dict__
                | {"savings_percent": retrieval.token_report.savings_percent},
                "snippets": [
                    {
                        "file_path": snippet.file_path,
                        "symbol": snippet.qualified_name,
                        "reason": snippet.reason,
                    }
                    for snippet in retrieval.snippets
                ],
            }
            critical_files = tuple(dict.fromkeys(snippet.file_path for snippet in retrieval.snippets))
            relevant_context = tuple(snippet.code for snippet in retrieval.snippets[:3])
        except Exception as exc:
            dependencies = {"error": str(exc)}
            critical_files = ()
            relevant_context = ()

        related_changes = tuple(
            event.entity_key.removeprefix("commit:")
            for event in history
            if event.entity_key.startswith("commit:")
        )
        evidence = collect_evidence(history, decisions, ownership, architecture)
        payload_text = json.dumps(
            {
                "query": query,
                "history": [event.summary for event in history],
                "decisions": [decision.answer for decision in decisions],
                "ownership": [entry.developer for entry in ownership],
                "architecture": [finding.summary for finding in architecture],
                "critical_files": critical_files,
            },
            sort_keys=True,
        )
        return CompressedContext(
            query=query,
            architecture=architecture,
            history=history,
            design_decisions=decisions,
            ownership=ownership,
            dependencies=dependencies,
            critical_files=critical_files,
            related_changes=related_changes,
            relevant_context=relevant_context,
            estimated_tokens=estimate_tokens(payload_text),
            evidence=evidence,
        )

    def impact(
        self,
        repo_path: str | Path,
        *,
        base_ref: str = "HEAD",
        max_related: int = 5,
    ) -> ImpactReport:
        repo_root = resolve_repo_root(repo_path)
        changed = changed_files(repo_root, base_ref=base_ref)
        warnings: list[str] = []
        if not changed:
            warnings.append("No changed files were detected from git diff.")
        impacted: list[ImpactedFile] = []
        related_commits: list[str] = []
        for status, file_path in changed:
            owners = self.ownership(repo_root, file_path, limit=3)
            if not owners:
                owners = self.ownership(repo_root, component_for_path(file_path), limit=3)
            related = self.related_files(repo_root, file_path, limit=max_related)
            evidence_rows = self.search_memory(repo_root, file_path, limit=5)
            evidence = tuple(evidence_ref_from_dict(row) for row in evidence_rows)
            related_commits.extend(
                ref.source_id
                for ref in evidence
                if ref.source_type == "commit"
            )
            risk, reasons = impact_risk(status, file_path, owners, related, evidence)
            impacted.append(
                ImpactedFile(
                    file_path=file_path,
                    status=status,
                    component=component_for_path(file_path),
                    risk=risk,
                    reasons=reasons,
                    owners=owners,
                    related_files=related,
                    evidence=evidence,
                )
            )
        token_report = impact_token_report(repo_root, impacted)
        risk_level = aggregate_risk(tuple(item.risk for item in impacted))
        summary = impact_summary(impacted, token_report)
        return ImpactReport(
            base_ref=base_ref,
            changed_files=tuple(file_path for _, file_path in changed),
            impacted_files=tuple(impacted),
            related_commits=tuple(dict.fromkeys(related_commits)),
            token_report=token_report,
            risk_level=risk_level,
            summary=summary,
            warnings=tuple(warnings),
        )

    def related_files(
        self,
        repo_path: str | Path,
        file_path: str,
        *,
        limit: int = 10,
    ) -> tuple[CoChangeLink, ...]:
        store = self._open_store(repo_path)
        try:
            relationships = store.related_file_rows((file_path,), limit=limit * 4)
            counts: dict[str, int] = defaultdict(int)
            evidence_by_file: dict[str, list[EvidenceRef]] = defaultdict(list)
            target_key = file_entity_key(file_path)
            for relationship in relationships:
                metadata = parse_json(relationship["metadata_json"])
                if not metadata.get("cochange"):
                    continue
                source = str(relationship["source_key"])
                target = str(relationship["target_key"])
                other = target if source == target_key else source
                if other == target_key or not other.startswith("file:"):
                    continue
                other_path = other.removeprefix("file:")
                counts[other_path] += 1
                evidence_id = relationship["evidence_id"]
                if evidence_id is not None:
                    row = store.connection.execute(
                        "SELECT * FROM memory_evidence WHERE id = ?",
                        (int(evidence_id),),
                    ).fetchone()
                    if row is not None:
                        evidence_by_file[other_path].append(evidence_row_to_ref(row))
            links = [
                CoChangeLink(
                    file_path=file_path,
                    related_file_path=other_path,
                    commits=count,
                    confidence=min(0.95, 0.45 + count * 0.12),
                    evidence=tuple(evidence_by_file[other_path][:3]),
                )
                for other_path, count in counts.items()
            ]
            links.sort(key=lambda link: (-link.commits, link.related_file_path))
            return tuple(links[:limit])
        finally:
            store.close()

    def hotspots(
        self,
        repo_path: str | Path,
        *,
        limit: int = 10,
    ) -> tuple[HotspotEntry, ...]:
        store = self._open_store(repo_path)
        try:
            rows = store.commit_evidence(limit=2000)
            by_component: dict[str, list[sqlite3.Row]] = defaultdict(list)
            for row in rows:
                for file_path in metadata_files(row):
                    by_component[component_for_path(file_path)].append(row)
            hotspots: list[HotspotEntry] = []
            for component, component_rows in by_component.items():
                commits = {str(row["source_id"]) for row in component_rows}
                authors = {str(row["author"] or "Unknown") for row in component_rows}
                files = {
                    file_path
                    for row in component_rows
                    for file_path in metadata_files(row)
                    if component_for_path(file_path) == component
                }
                risk_score = len(commits) * 2 + len(authors) + len(files) * 0.4
                last_changed = max(str(row["timestamp"] or "") for row in component_rows) or None
                hotspots.append(
                    HotspotEntry(
                        component=component,
                        commits=len(commits),
                        authors=len(authors),
                        files=len(files),
                        risk_score=risk_score,
                        last_changed=last_changed,
                        evidence=tuple(evidence_row_to_ref(row) for row in component_rows[:3]),
                    )
                )
            hotspots.sort(key=lambda item: (-item.risk_score, item.component))
            return tuple(hotspots[:limit])
        finally:
            store.close()

    def component_summary(
        self,
        repo_path: str | Path,
        topic: str,
        *,
        limit: int = 8,
    ) -> ComponentSummary:
        store = self._open_store(repo_path)
        try:
            rows = store.search_evidence(topic, limit=100)
            commit_rows = [row for row in rows if row["source_type"] == "commit"]
            files = tuple(
                dict.fromkeys(
                    file_path
                    for row in commit_rows
                    for file_path in metadata_files(row)
                    if topic.lower() in file_path.lower()
                    or topic.lower() in component_for_path(file_path).lower()
                )
            )
            if not files:
                files = tuple(
                    dict.fromkeys(
                        file_path
                        for row in commit_rows
                        for file_path in metadata_files(row)
                    )
                )[:limit]
            authors = tuple(
                dict.fromkeys(str(row["author"] or "Unknown") for row in commit_rows)
            )
            related: list[CoChangeLink] = []
            for file_path in files[:3]:
                related.extend(self.related_files(repo_path, file_path, limit=3))
            evidence = tuple(evidence_row_to_ref(row) for row in rows[:5])
            summary = (
                f"{topic} appears in {len(commit_rows)} indexed commits across "
                f"{len(files)} files with {len(authors)} contributors."
            )
            if not commit_rows and evidence:
                summary = f"{topic} appears in indexed documentation but not commit evidence."
            elif not evidence:
                summary = f"No indexed memory evidence was found for {topic}."
            return ComponentSummary(
                component=topic,
                summary=summary,
                commits=len({str(row["source_id"]) for row in commit_rows}),
                authors=authors,
                files=files[:limit],
                related_files=tuple(related[:limit]),
                evidence=evidence,
            )
        finally:
            store.close()

    def _open_store(self, repo_path: str | Path) -> MemoryStore:
        repo_root = resolve_repo_root(repo_path)
        database_path = CodeAtlasPaths(repo_root).database_path
        if not database_path.exists():
            msg = f"No CodeAtlas index found at {database_path}. Run `codeatlas memory {repo_root}` first."
            raise FileNotFoundError(msg)
        store = MemoryStore(database_path)
        store.initialize()
        return store


def iter_memory_documents(repo_root: Path) -> tuple[Path, ...]:
    found: dict[str, Path] = {}
    for pattern in DOCUMENT_PATTERNS:
        for path in repo_root.glob(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(repo_root)
            if any(part in DEFAULT_IGNORE_DIRS for part in relative.parts):
                continue
            found[relative.as_posix()] = path
    return tuple(found[key] for key in sorted(found))


def run_git(repo_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def changed_files(repo_root: Path, *, base_ref: str = "HEAD") -> tuple[tuple[str, str], ...]:
    status_rows: list[tuple[str, str]] = []
    diff = run_git(
        repo_root,
        ["diff", "--name-status", base_ref, "--"],
        check=False,
    )
    if diff.returncode == 0:
        for line in diff.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                status_rows.append((parts[0], parts[-1]))
    untracked = run_git(
        repo_root,
        ["ls-files", "--others", "--exclude-standard"],
        check=False,
    )
    if untracked.returncode == 0:
        for line in untracked.stdout.splitlines():
            if line.strip():
                status_rows.append(("A", line.strip()))
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for status, file_path in status_rows:
        if file_path in seen or ".codeatlas/" in file_path:
            continue
        seen.add(file_path)
        result.append((status, file_path))
    return tuple(result)


def commit_from_payload(payload: dict[str, Any]) -> GitCommitRecord:
    return GitCommitRecord(
        sha=str(payload["sha"]),
        author_name=str(payload["author_name"]),
        author_email=str(payload["author_email"]),
        timestamp=str(payload["timestamp"]),
        subject=str(payload["subject"]),
        files=tuple(str(file_path) for file_path in payload["files"]),
    )


def developer_key(author_email_or_name: str) -> str:
    return f"developer:{slug(author_email_or_name or 'unknown')}"


def file_entity_key(file_path: str) -> str:
    return f"file:{file_path}"


def component_for_path(file_path: str) -> str:
    parts = Path(file_path).parts
    if not parts:
        return "root"
    if parts[0] in {"src", "app", "lib", "services"} and len(parts) > 1:
        return parts[1]
    return parts[0]


def cochange_pairs(files: tuple[str, ...], *, limit: int = 30) -> tuple[tuple[str, str], ...]:
    clean_files = tuple(dict.fromkeys(files[:limit]))
    pairs: list[tuple[str, str]] = []
    for index, left in enumerate(clean_files):
        for right in clean_files[index + 1 :]:
            if left == right:
                continue
            pairs.append((left, right))
            pairs.append((right, left))
    return tuple(pairs)


def infer_commit_purpose(subject: str) -> str:
    clean = subject.strip()
    lower = clean.lower()
    prefixes = {
        "add": "Added",
        "added": "Added",
        "introduce": "Introduced",
        "introduced": "Introduced",
        "fix": "Fixed",
        "fixed": "Fixed",
        "refactor": "Refactored",
        "remove": "Removed",
        "removed": "Removed",
        "update": "Updated",
        "updated": "Updated",
        "migrate": "Migrated",
        "migrated": "Migrated",
    }
    first = lower.split(maxsplit=1)[0] if lower else ""
    if first in prefixes:
        remainder = clean.split(maxsplit=1)[1] if len(clean.split(maxsplit=1)) > 1 else clean
        return f"{prefixes[first]} {remainder}".strip()
    return clean or "Repository change"


def infer_motivation(subject: str) -> str:
    lower = subject.lower()
    keyword_reasons = {
        "timeout": "Likely response to timeout or reliability failures.",
        "retry": "Likely added to improve resilience after transient failures.",
        "bug": "Likely motivated by a reported defect.",
        "fix": "Likely motivated by incorrect behavior or failing tests.",
        "security": "Likely motivated by security hardening.",
        "auth": "Likely related to authentication or authorization behavior.",
        "performance": "Likely motivated by latency or efficiency concerns.",
        "cache": "Likely motivated by repeated work, latency, or load reduction.",
        "migration": "Likely part of a platform or data model migration.",
    }
    for keyword, reason in keyword_reasons.items():
        if keyword in lower:
            return reason
    return "Motivation not explicit in indexed evidence."


def infer_components(files: tuple[str, ...]) -> tuple[str, ...]:
    components: list[str] = []
    for file_path in files:
        path = Path(file_path)
        parts = path.parts
        if not parts:
            continue
        if parts[0] in {"src", "app", "lib", "services"} and len(parts) > 1:
            components.append(parts[1])
        else:
            components.append(parts[0])
    return tuple(sorted(set(components)))


def infer_feature_terms(subject: str, files: tuple[str, ...]) -> tuple[str, ...]:
    candidates = set(infer_terms_from_text(subject))
    for file_path in files:
        stem = Path(file_path).stem.replace("_", " ").replace("-", " ")
        candidates.update(infer_terms_from_text(stem))
        for part in Path(file_path).parts:
            candidates.update(infer_terms_from_text(part.replace("_", " ").replace("-", " ")))
    return tuple(sorted(candidates))


def infer_terms_from_text(text: str) -> tuple[str, ...]:
    lower = text.lower()
    terms = {term for term in ARCHITECTURE_TERMS if term in lower}
    for token in re.findall(r"[a-z][a-z0-9_]{3,}", lower):
        if token in {"readme", "docs", "test", "tests", "file", "files", "code", "update"}:
            continue
        if token.endswith("ing") or token.endswith("tion") or token in ARCHITECTURE_TERMS:
            terms.add(token.replace("_", " "))
    return tuple(sorted(terms))


def infer_risk_level(subject: str, files: tuple[str, ...]) -> str:
    lower = " ".join((subject, *files)).lower()
    high_markers = {"auth", "security", "payment", "migration", "database", "schema", "infra"}
    if any(marker in lower for marker in high_markers) or len(files) >= 12:
        return "high"
    if len(files) >= 5 or any(marker in lower for marker in {"retry", "cache", "api", "service"}):
        return "medium"
    return "low"


def infer_architectural_impact(subject: str, files: tuple[str, ...]) -> str:
    lower = " ".join((subject, *files)).lower()
    high = {"microservice", "kafka", "redis", "database", "terraform", "kubernetes", "migration"}
    medium = {"cache", "queue", "api", "gateway", "service", "architecture", "jwt", "auth"}
    if any(term in lower for term in high):
        return "high"
    if any(term in lower for term in medium):
        return "medium"
    return "low"


def is_introductory_commit(subject: str) -> bool:
    return subject.lower().startswith(("add ", "added ", "introduce ", "introduced "))


def pull_request_number(subject: str) -> int | None:
    match = re.search(r"\(#(\d+)\)|pull request #?(\d+)|pr #?(\d+)", subject, re.IGNORECASE)
    if not match:
        return None
    for value in match.groups():
        if value is not None:
            return int(value)
    return None


def commit_snippet(
    commit: GitCommitRecord,
    purpose: str,
    motivation: str,
    components: tuple[str, ...],
    risk: str,
) -> str:
    component_text = ", ".join(components) if components else "unknown components"
    files_text = ", ".join(commit.files[:6])
    if len(commit.files) > 6:
        files_text += f", and {len(commit.files) - 6} more"
    return (
        f"Purpose: {purpose}. Motivation: {motivation} "
        f"Impacted components: {component_text}. Risk: {risk}. "
        f"Changed files: {files_text}."
    )


def document_title(content: str, relative_path: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or relative_path
        if stripped:
            return stripped[:120]
    return relative_path


def compact_snippet(content: str, *, max_chars: int = 700) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    snippet = " ".join(lines)
    if len(snippet) <= max_chars:
        return snippet
    return snippet[:max_chars].rstrip() + "..."


def search_terms(query: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_./-]+", query)
            if len(token) >= 2
        )
    )


def score_match(query: str, title: str, snippet: str) -> float:
    terms = search_terms(query)
    if not terms:
        return 0.0
    haystack = f"{title}\n{snippet}".lower()
    matches = sum(1 for term in terms if term in haystack)
    exact_bonus = 0.25 if query.lower() in haystack else 0.0
    return min(1.0, 0.35 + matches / len(terms) * 0.45 + exact_bonus)


def evidence_row_to_ref(row: sqlite3.Row) -> EvidenceRef:
    return EvidenceRef(
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]),
        title=str(row["title"]),
        snippet=str(row["snippet"]),
        path=str(row["path"]) or None,
        author=str(row["author"]) if row["author"] else None,
        timestamp=str(row["timestamp"]) if row["timestamp"] else None,
        confidence=score_match("", str(row["title"]), str(row["snippet"])) or 0.75,
    )


def parse_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def metadata_files(row: sqlite3.Row) -> tuple[str, ...]:
    metadata = parse_json(row["metadata_json"])
    return tuple(str(path) for path in metadata.get("files", ()))


def escape_fts_token(token: str) -> str:
    safe = re.sub(r'["\s]+', " ", token).strip()
    if not safe:
        return '""'
    return f'"{safe}"'


def evidence_ref_from_dict(payload: dict[str, Any]) -> EvidenceRef:
    return EvidenceRef(
        source_type=str(payload.get("source_type", "")),
        source_id=str(payload.get("source_id", "")),
        title=str(payload.get("title", "")),
        snippet=str(payload.get("snippet", "")),
        path=payload.get("path"),
        author=payload.get("author"),
        timestamp=payload.get("timestamp"),
        confidence=float(payload.get("confidence", 0.75)),
    )


def impact_risk(
    status: str,
    file_path: str,
    owners: tuple[OwnershipEntry, ...],
    related: tuple[CoChangeLink, ...],
    evidence: tuple[EvidenceRef, ...],
) -> tuple[str, tuple[str, ...]]:
    reasons: list[str] = []
    lower = file_path.lower()
    score = 0
    if status.startswith("D"):
        score += 3
        reasons.append("file deleted")
    elif status.startswith("R"):
        score += 2
        reasons.append("file renamed")
    elif status.startswith("A"):
        score += 1
        reasons.append("new file")
    if any(term in lower for term in ("auth", "payment", "database", "migration", "security")):
        score += 4
        reasons.append("sensitive component name")
    if owners:
        score += 1
        reasons.append("historical owners found")
    else:
        score += 2
        reasons.append("no historical owner found")
    if related:
        score += min(3, len(related))
        reasons.append("co-change neighbors found")
    if evidence:
        score += 1
        reasons.append("historical evidence found")
    if score >= 6:
        return "high", tuple(reasons)
    if score >= 4:
        return "medium", tuple(reasons)
    return "low", tuple(reasons or ("limited historical signal",))


def impact_token_report(repo_root: Path, impacted: list[ImpactedFile]) -> TokenReport:
    baseline_chars = 0
    for item in impacted:
        path = repo_root / item.file_path
        if path.exists() and path.is_file():
            baseline_chars += len(path.read_text(encoding="utf-8", errors="replace"))
    optimized_payload = [
        {
            "file": item.file_path,
            "risk": item.risk,
            "owners": [owner.developer for owner in item.owners],
            "related": [link.related_file_path for link in item.related_files],
            "reasons": item.reasons,
        }
        for item in impacted
    ]
    optimized_tokens = estimate_tokens(json.dumps(optimized_payload, sort_keys=True))
    baseline_tokens = estimate_tokens("x" * baseline_chars)
    return TokenReport(
        baseline_tokens=max(baseline_tokens, optimized_tokens),
        optimized_tokens=optimized_tokens,
    )


def aggregate_risk(risks: tuple[str, ...]) -> str:
    if "high" in risks:
        return "high"
    if "medium" in risks:
        return "medium"
    if risks:
        return "low"
    return "none"


def impact_summary(impacted: list[ImpactedFile], token_report: TokenReport) -> str:
    if not impacted:
        return "No local changes were detected."
    risk_counts = defaultdict(int)
    for item in impacted:
        risk_counts[item.risk] += 1
    return (
        f"{len(impacted)} changed files analyzed: "
        f"{risk_counts['high']} high, {risk_counts['medium']} medium, "
        f"{risk_counts['low']} low risk. Estimated context savings: "
        f"{token_report.savings_percent:.0f}%."
    )


def collect_evidence(
    history: tuple[HistoryEvent, ...],
    decisions: tuple[DecisionAnswer, ...],
    ownership: tuple[OwnershipEntry, ...],
    architecture: tuple[ArchitectureFinding, ...],
) -> tuple[EvidenceRef, ...]:
    refs: list[EvidenceRef] = []
    for event in history:
        refs.extend(event.evidence)
    for decision in decisions:
        refs.extend(decision.evidence)
    for entry in ownership:
        refs.extend(entry.evidence)
    for finding in architecture:
        refs.extend(finding.evidence)
    unique: dict[tuple[str, str, str | None], EvidenceRef] = {}
    for ref in refs:
        unique[(ref.source_type, ref.source_id, ref.path)] = ref
    return tuple(unique.values())


def slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text or "unknown"
