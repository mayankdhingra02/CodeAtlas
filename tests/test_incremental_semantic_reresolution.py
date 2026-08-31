from __future__ import annotations

import hashlib
import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

from codeatlas.config import CodeAtlasPaths
from codeatlas.external_index import import_external_index
from codeatlas.flow_trace import trace_flow
from codeatlas.indexer import RepositoryIndexer
from codeatlas.models import EdgeType, IndexReport, NodeType
from codeatlas.storage import (
    MAX_EDGE_EXAMPLES,
    GraphStore,
    merge_edge_metadata,
    retain_precise_edge_metadata,
    symbol_node_key,
)


class IncrementalSemanticReresolutionTests(unittest.TestCase):
    def make_repo(self, files: dict[str, str]) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for relative_path, source in files.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        return root

    def write_source(self, root: Path, relative_path: str, source: str) -> None:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")

    def source_sha(self, root: Path, relative_path: str) -> str:
        return hashlib.sha256((root / relative_path).read_bytes()).hexdigest()

    def assert_source_unchanged(
        self,
        root: Path,
        relative_path: str,
        expected_sha: str,
    ) -> None:
        self.assertEqual(self.source_sha(root, relative_path), expected_sha)

    def relationship_edges(
        self,
        root: Path,
        source: str,
        edge_type: str,
    ) -> list[dict[str, Any]]:
        store = GraphStore(CodeAtlasPaths(root).database_path)
        try:
            store.initialize()
            rows = store.connection.execute(
                """
                SELECT id, source_key, target_key, metadata_json
                FROM edges
                WHERE source_key = ? AND edge_type = ?
                ORDER BY id
                """,
                (symbol_node_key(source), edge_type),
            ).fetchall()
        finally:
            store.close()
        return [
            {
                "id": int(row["id"]),
                "source_key": str(row["source_key"]),
                "target_key": str(row["target_key"]),
                "metadata": json.loads(str(row["metadata_json"] or "{}")),
            }
            for row in rows
        ]

    def call_edges(self, root: Path, caller: str) -> list[dict[str, Any]]:
        return self.relationship_edges(root, caller, "CALLS")

    def assert_only_call_target(
        self,
        root: Path,
        caller: str,
        target_key: str,
        *,
        resolution_tier: str,
    ) -> dict[str, Any]:
        edges = self.call_edges(root, caller)
        self.assertEqual(
            [edge["target_key"] for edge in edges],
            [target_key],
            "incremental indexing must replace the stale edge instead of accumulating targets",
        )
        self.assertEqual(edges[0]["metadata"]["resolution_tier"], resolution_tier)
        return edges[0]

    def assert_only_relationship_target(
        self,
        root: Path,
        source: str,
        edge_type: str,
        target_key: str,
        *,
        resolution_tier: str,
    ) -> dict[str, Any]:
        edges = self.relationship_edges(root, source, edge_type)
        self.assertEqual(
            [edge["target_key"] for edge in edges],
            [target_key],
            f"incremental indexing must replace the stale {edge_type} edge",
        )
        self.assertEqual(edges[0]["metadata"]["resolution_tier"], resolution_tier)
        return edges[0]

    def assert_report_contract(
        self,
        root: Path,
        report: IndexReport,
        *,
        minimum_reresolved: int = 0,
        minimum_removed: int = 0,
        minimum_replaced: int = 0,
        expected_fallback: bool = True,
    ) -> None:
        self.assertEqual(report.files_content_parsed, report.files_indexed)
        self.assertGreaterEqual(report.files_semantically_reresolved, minimum_reresolved)
        self.assertGreaterEqual(report.relationships_removed, minimum_removed)
        self.assertGreaterEqual(report.relationships_replaced, minimum_replaced)
        self.assertEqual(report.conservative_fallback_used, expected_fallback)
        if expected_fallback:
            self.assertTrue(report.conservative_fallback_reason)
        else:
            self.assertIsNone(report.conservative_fallback_reason)

        store = GraphStore(CodeAtlasPaths(root).database_path)
        try:
            store.initialize()
            persisted = store.get_metadata("last_index_report", {})
        finally:
            store.close()
        for field in (
            "files_content_parsed",
            "files_semantically_reresolved",
            "relationships_removed",
            "relationships_replaced",
            "conservative_fallback_used",
            "conservative_fallback_reason",
        ):
            self.assertIn(field, persisted)
            self.assertEqual(persisted[field], getattr(report, field))

    def test_added_definition_reresolves_unchanged_route_caller_and_flow_trace(self) -> None:
        root = self.make_repo(
            {
                "app/__init__.py": "",
                "app/caller.py": """
                    from fastapi import FastAPI

                    app = FastAPI()

                    @app.post("/run")
                    def entry():
                        return process()
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol_ref:process",
            resolution_tier="unresolved",
        )
        before_trace = trace_flow(root, "POST /run")
        self.assertFalse(before_trace.complete)
        self.assertTrue(any(step.node_key == "symbol_ref:process" for step in before_trace.steps))

        self.write_source(
            root,
            "app/service.py",
            """
            def process():
                return "done"
            """,
        )
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)

        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.service.process",
            resolution_tier="unique_name",
        )
        self.assertEqual(report.files_indexed, 1)
        self.assert_report_contract(
            root,
            report,
            minimum_reresolved=1,
            minimum_removed=1,
            minimum_replaced=1,
        )

        after_trace = trace_flow(root, "POST /run")
        steps_by_id = {step.id: step for step in after_trace.steps}
        self.assertTrue(after_trace.complete)
        self.assertEqual(
            tuple(steps_by_id[step_id].node_key for step_id in after_trace.primary_path),
            (
                "route:app.caller.entry",
                "symbol:app.caller.entry",
                "symbol:app.service.process",
            ),
        )
        self.assertEqual(after_trace.links[-1].resolution_tier, "unique_name")
        self.assertFalse(any(step.status == "unresolved" for step in after_trace.steps))

    def test_deleted_definition_makes_unchanged_importing_caller_unresolved(self) -> None:
        root = self.make_repo(
            {
                "app/__init__.py": "",
                "app/caller.py": """
                    from app.service import process

                    def entry():
                        return process()
                """,
                "app/service.py": """
                    def process():
                        return 1
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.service.process",
            resolution_tier="import_scoped",
        )

        (root / "app" / "service.py").unlink()
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)

        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol_ref:process",
            resolution_tier="unresolved",
        )
        self.assertEqual(report.files_deleted, 1)
        self.assertEqual(report.files_indexed, 0)
        self.assert_report_contract(root, report, minimum_reresolved=1)

    def test_renamed_definition_invalidates_unchanged_caller_edge(self) -> None:
        root = self.make_repo(
            {
                "app/caller.py": """
                    def entry():
                        return process()
                """,
                "app/service.py": """
                    def process():
                        return 1
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.service.process",
            resolution_tier="unique_name",
        )

        self.write_source(
            root,
            "app/service.py",
            """
            def execute():
                return 1
            """,
        )
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)

        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol_ref:process",
            resolution_tier="unresolved",
        )
        self.assertEqual(report.files_indexed, 1)
        self.assert_report_contract(root, report, minimum_reresolved=1)

    def test_moved_definition_retargets_unchanged_caller_without_old_module_edge(self) -> None:
        root = self.make_repo(
            {
                "app/caller.py": """
                    from app.alpha import process

                    def entry():
                        return process()
                """,
                "app/alpha.py": """
                    def process():
                        return 1
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.alpha.process",
            resolution_tier="import_scoped",
        )

        (root / "app" / "alpha.py").rename(root / "app" / "beta.py")
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)

        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.beta.process",
            resolution_tier="unique_name",
        )
        self.assertEqual(report.files_deleted, 1)
        self.assertEqual(report.files_indexed, 1)
        self.assert_report_contract(root, report, minimum_reresolved=1)

    def test_reexport_alias_change_retargets_unchanged_caller(self) -> None:
        """A changed facade must retarget its unchanged transitive-alias caller."""
        root = self.make_repo(
            {
                "app/caller.py": """
                    from app.facade import run

                    def entry():
                        return run()
                """,
                "app/facade.py": """
                    from app.alpha import process as run
                """,
                "app/alpha.py": """
                    def process():
                        return "alpha"
                """,
                "app/beta.py": """
                    def process():
                        return "beta"
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        initial_edges = self.call_edges(root, "app.caller.entry")
        self.assertEqual(
            [edge["target_key"] for edge in initial_edges],
            ["symbol:app.alpha.process"],
        )

        self.write_source(
            root,
            "app/facade.py",
            """
            from app.beta import process as run
            """,
        )
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)

        updated_edges = self.call_edges(root, "app.caller.entry")
        self.assertEqual(
            [edge["target_key"] for edge in updated_edges],
            ["symbol:app.beta.process"],
        )
        self.assertEqual(report.files_indexed, 1)
        self.assert_report_contract(
            root,
            report,
            minimum_reresolved=1,
            minimum_removed=1,
            minimum_replaced=1,
            expected_fallback=False,
        )

    def test_package_init_relative_reexport_retargets_direct_and_imported_calls(self) -> None:
        root = self.make_repo(
            {
                "app/__init__.py": """
                    from .alpha import process as run

                    def local_entry():
                        return run()
                """,
                "app/caller.py": """
                    from app import run

                    def entry():
                        return run()
                """,
                "app/alpha.py": """
                    def process():
                        return "alpha"
                """,
                "app/beta.py": """
                    def process():
                        return "beta"
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        self.assert_only_call_target(
            root,
            "app.local_entry",
            "symbol:app.alpha.process",
            resolution_tier="import_scoped",
        )
        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.alpha.process",
            resolution_tier="import_scoped",
        )

        self.write_source(
            root,
            "app/__init__.py",
            """
            from .beta import process as run

            def local_entry():
                return run()
            """,
        )
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)
        self.assert_only_call_target(
            root,
            "app.local_entry",
            "symbol:app.beta.process",
            resolution_tier="import_scoped",
        )
        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.beta.process",
            resolution_tier="import_scoped",
        )
        self.assertEqual(report.files_indexed, 1)
        self.assert_report_contract(
            root,
            report,
            minimum_reresolved=1,
            minimum_removed=1,
            minimum_replaced=1,
            expected_fallback=False,
        )

    def test_ambiguous_call_becomes_unique_after_definition_is_deleted(self) -> None:
        root = self.make_repo(
            {
                "app/caller.py": """
                    def entry():
                        return process()
                """,
                "app/alpha.py": """
                    def process():
                        return "alpha"
                """,
                "app/beta.py": """
                    def process():
                        return "beta"
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol_ref:process",
            resolution_tier="unresolved",
        )

        (root / "app" / "beta.py").unlink()
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)

        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.alpha.process",
            resolution_tier="unique_name",
        )
        self.assertEqual(report.files_deleted, 1)
        self.assert_report_contract(root, report, minimum_reresolved=1)

    def test_unique_call_becomes_ambiguous_after_definition_is_added(self) -> None:
        root = self.make_repo(
            {
                "app/caller.py": """
                    def entry():
                        return process()
                """,
                "app/alpha.py": """
                    def process():
                        return "alpha"
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.alpha.process",
            resolution_tier="unique_name",
        )

        self.write_source(
            root,
            "app/beta.py",
            """
            def process():
                return "beta"
            """,
        )
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)

        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol_ref:process",
            resolution_tier="unresolved",
        )
        self.assertEqual(report.files_indexed, 1)
        self.assert_report_contract(root, report, minimum_reresolved=1)

    def test_references_and_inheritance_retarget_from_unchanged_python_files(self) -> None:
        root = self.make_repo(
            {
                "app/reference_user.py": """
                    def read_process():
                        return process
                """,
                "app/processes.py": """
                    def process():
                        return 1
                """,
                "app/child.py": """
                    class Child(Base):
                        pass
                """,
                "app/bases.py": """
                    class Base:
                        pass
                """,
            }
        )
        RepositoryIndexer().index(root)
        reference_sha = self.source_sha(root, "app/reference_user.py")
        child_sha = self.source_sha(root, "app/child.py")
        self.assert_only_relationship_target(
            root,
            "app.reference_user.read_process",
            "REFERENCES",
            "symbol:app.processes.process",
            resolution_tier="unique_name",
        )
        self.assert_only_relationship_target(
            root,
            "app.child.Child",
            "INHERITS",
            "symbol:app.bases.Base",
            resolution_tier="unique_name",
        )

        (root / "app" / "processes.py").rename(root / "app" / "moved_processes.py")
        (root / "app" / "bases.py").rename(root / "app" / "moved_bases.py")
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/reference_user.py", reference_sha)
        self.assert_source_unchanged(root, "app/child.py", child_sha)

        self.assert_only_relationship_target(
            root,
            "app.reference_user.read_process",
            "REFERENCES",
            "symbol:app.moved_processes.process",
            resolution_tier="unique_name",
        )
        self.assert_only_relationship_target(
            root,
            "app.child.Child",
            "INHERITS",
            "symbol:app.moved_bases.Base",
            resolution_tier="unique_name",
        )
        self.assertEqual(report.files_deleted, 2)
        self.assertEqual(report.files_indexed, 2)
        self.assert_report_contract(root, report, minimum_reresolved=2)

    def test_noop_and_body_only_incremental_runs_avoid_conservative_fallback(self) -> None:
        root = self.make_repo(
            {
                "app/caller.py": """
                    def entry():
                        return process()
                """,
                "app/service.py": """
                    def process():
                        return 1
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")

        noop_report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)
        self.assertEqual(noop_report.files_indexed, 0)
        self.assertEqual(noop_report.files_semantically_reresolved, 0)
        self.assertEqual(noop_report.relationships_removed, 0)
        self.assertEqual(noop_report.relationships_replaced, 0)
        self.assert_report_contract(root, noop_report, expected_fallback=False)

        self.write_source(
            root,
            "app/service.py",
            """
            def process():
                return 2
            """,
        )
        body_report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)
        self.assert_only_call_target(
            root,
            "app.caller.entry",
            "symbol:app.service.process",
            resolution_tier="unique_name",
        )
        self.assertEqual(body_report.files_indexed, 1)
        self.assertEqual(body_report.files_semantically_reresolved, 0)
        self.assert_report_contract(root, body_report, expected_fallback=False)

    def test_delete_file_invalidates_the_live_symbol_resolution_cache(self) -> None:
        root = self.make_repo(
            {
                "app/caller.py": """
                    def entry():
                        return process()
                """,
                "app/service.py": """
                    def process():
                        return 1
                """,
            }
        )
        RepositoryIndexer().index(root)
        store = GraphStore(CodeAtlasPaths(root).database_path)
        try:
            store.initialize()
            before = store.resolve_symbol("process", "app.caller")
            if before is None:
                self.fail("expected process to populate the live symbol resolution cache")
            self.assertEqual(before.node_key, "symbol:app.service.process")

            self.assertTrue(store.delete_file("app/service.py"))
            after = store.resolve_symbol("process", "app.caller")
            self.assertIsNone(after)
        finally:
            store.close()

    def test_conservative_cleanup_preserves_external_scip_relationships(self) -> None:
        root = self.make_repo(
            {
                "app/caller.py": """
                    def entry():
                        return process()
                """,
                "app/service.py": """
                    def process():
                        return 1
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        store = GraphStore(CodeAtlasPaths(root).database_path)
        try:
            store.initialize()
            store.insert_node(
                "symbol:external.precise",
                NodeType.SYMBOL,
                "external.precise",
                metadata={"external": True},
            )
            store.insert_edge(
                "symbol:app.caller.entry",
                NodeType.FUNCTION,
                "symbol:external.precise",
                NodeType.SYMBOL,
                EdgeType.CALLS,
                metadata={
                    "line": 2,
                    "resolution_tier": "scip",
                    "confidence": 1.0,
                },
            )
            store.commit()
        finally:
            store.close()

        self.write_source(
            root,
            "app/service.py",
            """
            def execute():
                return 1
            """,
        )
        report = RepositoryIndexer().index(root, incremental=True)
        self.assert_source_unchanged(root, "app/caller.py", caller_sha)

        edges = self.call_edges(root, "app.caller.entry")
        by_target = {edge["target_key"]: edge for edge in edges}
        self.assertEqual(
            by_target["symbol:external.precise"]["metadata"]["resolution_tier"],
            "scip",
        )
        self.assertEqual(
            by_target["symbol_ref:process"]["metadata"]["resolution_tier"],
            "unresolved",
        )
        self.assert_report_contract(root, report, minimum_reresolved=1)

    def test_fallback_preserves_file_backed_opaque_scip_symbol_and_edge(self) -> None:
        root = self.make_repo(
            {
                "app/caller.py": """
                    def entry():
                        return process()
                """,
                "app/service.py": """
                    def process():
                        return 1
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        opaque_symbol = "scip-python pkg app/caller.py/source()."
        external_target = "scip-python dependency external/target()."
        external_index = root / "scip-export.json"
        external_index.write_text(
            json.dumps(
                {
                    "symbols": [
                        {
                            "qualified_name": opaque_symbol,
                            "name": "source",
                            "kind": "FUNCTION",
                            "file_path": "app/caller.py",
                            "line_start": 1,
                        }
                    ],
                    "edges": [
                        {
                            "source": opaque_symbol,
                            "target": external_target,
                            "type": "CALLS",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        import_external_index(root, external_index, index_format="scip-json")

        def precise_state() -> dict[str, Any]:
            store = GraphStore(CodeAtlasPaths(root).database_path)
            try:
                store.initialize()
                symbol = store.connection.execute(
                    """
                    SELECT s.qualified_name, n.file_path, n.symbol_id, n.metadata_json
                    FROM symbols s
                    JOIN nodes n ON n.key = 'symbol:' || s.qualified_name
                    WHERE s.qualified_name = ?
                    """,
                    (opaque_symbol,),
                ).fetchone()
                edge = store.connection.execute(
                    """
                    SELECT source_key, target_key, metadata_json
                    FROM edges
                    WHERE source_key = ? AND target_key = ? AND edge_type = 'CALLS'
                    """,
                    (symbol_node_key(opaque_symbol), symbol_node_key(external_target)),
                ).fetchone()
            finally:
                store.close()
            if symbol is None or edge is None:
                self.fail("opaque SCIP symbol and edge must survive semantic cleanup")
            return {
                "qualified_name": str(symbol["qualified_name"]),
                "file_path": str(symbol["file_path"]),
                "symbol_id": int(symbol["symbol_id"]),
                "node_metadata": json.loads(str(symbol["metadata_json"])),
                "source_key": str(edge["source_key"]),
                "target_key": str(edge["target_key"]),
                "edge_metadata": json.loads(str(edge["metadata_json"])),
            }

        initial = precise_state()
        for extra_definition in ("helper", "another_helper"):
            self.write_source(
                root,
                "app/service.py",
                f"""
                def process():
                    return 1

                def {extra_definition}():
                    return 2
                """,
            )
            report = RepositoryIndexer().index(root, incremental=True)
            self.assert_source_unchanged(root, "app/caller.py", caller_sha)
            self.assertEqual(precise_state(), initial)
            self.assertEqual(report.files_content_parsed, 1)
            self.assertEqual(report.files_semantically_reresolved, 1)
            self.assert_report_contract(
                root,
                report,
                minimum_reresolved=1,
                minimum_removed=1,
                minimum_replaced=1,
            )

    def test_mixed_parser_and_scip_edge_is_stable_across_fallback_passes(self) -> None:
        root = self.make_repo(
            {
                "app/caller.py": """
                    def entry():
                        return process()
                """,
                "app/service.py": """
                    def process():
                        return 1
                """,
            }
        )
        RepositoryIndexer().index(root)
        caller_sha = self.source_sha(root, "app/caller.py")
        external_index = root / "scip-mixed-edge.json"
        external_index.write_text(
            json.dumps(
                {
                    "edges": [
                        {
                            "source": "app.caller.entry",
                            "target": "app.service.process",
                            "type": "CALLS",
                            "metadata": {
                                # Real external payloads need not tag each example.
                                "examples": [{"line": 2, "display": "process"}],
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        import_external_index(root, external_index, index_format="scip-json")

        evidence_generations: list[dict[str, Any]] = []
        for extra_definition in ("helper", "another_helper"):
            self.write_source(
                root,
                "app/service.py",
                f"""
                def process():
                    return 1

                def {extra_definition}():
                    return 2
                """,
            )
            report = RepositoryIndexer().index(root, incremental=True)
            self.assert_source_unchanged(root, "app/caller.py", caller_sha)
            evidence = self.assert_only_call_target(
                root,
                "app.caller.entry",
                "symbol:app.service.process",
                resolution_tier="scip",
            )["metadata"]
            evidence_generations.append(evidence)
            self.assertEqual(report.files_content_parsed, 1)
            self.assertEqual(report.files_semantically_reresolved, 1)
            self.assert_report_contract(
                root,
                report,
                minimum_reresolved=1,
                minimum_removed=1,
                minimum_replaced=1,
            )

        for evidence in evidence_generations:
            self.assertEqual(evidence["count"], 2)
            self.assertEqual(
                evidence["resolution_tier_counts"],
                {"scip": 1, "unique_name": 1},
            )
            self.assertEqual(len(evidence["examples"]), 2)
            self.assertEqual(
                {example["resolution_tier"] for example in evidence["examples"]},
                {"scip", "unique_name"},
            )
        self.assertEqual(evidence_generations[0], evidence_generations[1])

    def test_capped_parser_examples_synthesize_minimal_precise_evidence(self) -> None:
        parser_metadata: dict[str, Any] = {
            "line": 1,
            "display": "process",
            "arguments": ["parser-1"],
            "resolution_tier": "unique_name",
            "confidence": 0.68,
        }
        for line in range(2, MAX_EDGE_EXAMPLES + 6):
            parser_metadata = merge_edge_metadata(
                parser_metadata,
                {
                    "line": line,
                    "display": "process",
                    "arguments": [f"parser-{line}"],
                    "resolution_tier": "unique_name",
                    "confidence": 0.68,
                },
            )

        mixed = merge_edge_metadata(
            parser_metadata,
            {
                "line": 100,
                "arguments": ["precise"],
                "relationship": {"symbol": "external/process", "is_call": True},
                "resolution_tier": "scip",
                "confidence": 0.98,
                "source": "scip-json",
            },
        )
        self.assertEqual(len(mixed["examples"]), MAX_EDGE_EXAMPLES)
        self.assertNotIn(
            "scip",
            {example.get("resolution_tier") for example in mixed["examples"]},
        )

        precise = retain_precise_edge_metadata(mixed)
        self.assertEqual(precise["count"], 1)
        self.assertEqual(precise["resolution_tier_counts"], {"scip": 1})
        self.assertEqual(
            precise["relationship"],
            {"symbol": "external/process", "is_call": True},
        )
        self.assertNotIn("line", precise)
        self.assertNotIn("lines", precise)
        self.assertEqual(
            precise["examples"],
            [
                {
                    "resolution_tier": "scip",
                    "confidence": 0.98,
                    "source": "scip-json",
                }
            ],
        )
        self.assertNotIn("arguments", precise["examples"][0])

        parser_occurrence = {
            "line": 30,
            "display": "process",
            "arguments": ["current-parser"],
            "resolution_tier": "unique_name",
            "confidence": 0.68,
        }
        regenerated = merge_edge_metadata(precise, parser_occurrence)
        next_generation = merge_edge_metadata(
            retain_precise_edge_metadata(regenerated),
            parser_occurrence,
        )
        self.assertEqual(regenerated, next_generation)
        self.assertEqual(regenerated["count"], 2)
        self.assertEqual(
            regenerated["resolution_tier_counts"],
            {"scip": 1, "unique_name": 1},
        )

    def test_javascript_and_typescript_unchanged_callers_are_reresolved(self) -> None:
        for suffix in ("js", "ts"):
            with self.subTest(suffix=suffix):
                return_annotation = ": number" if suffix == "ts" else ""
                root = self.make_repo(
                    {
                        f"src/caller.{suffix}": f"""
                            export function entry(){return_annotation} {{
                                return process();
                            }}
                        """,
                    }
                )
                RepositoryIndexer().index(root)
                caller_path = f"src/caller.{suffix}"
                caller_sha = self.source_sha(root, caller_path)
                self.assert_only_call_target(
                    root,
                    "src.caller.entry",
                    "symbol_ref:process",
                    resolution_tier="unresolved",
                )

                self.write_source(
                    root,
                    f"src/service.{suffix}",
                    f"""
                    export function process(){return_annotation} {{
                        return 1;
                    }}
                    """,
                )
                report = RepositoryIndexer().index(root, incremental=True)
                self.assert_source_unchanged(root, caller_path, caller_sha)

                self.assert_only_call_target(
                    root,
                    "src.caller.entry",
                    "symbol:src.service.process",
                    resolution_tier="unique_name",
                )
                self.assertEqual(report.files_indexed, 1)
                self.assert_report_contract(root, report, minimum_reresolved=1)


if __name__ == "__main__":
    unittest.main()
