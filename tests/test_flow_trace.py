from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from codeatlas.config import CodeAtlasPaths
from codeatlas.flow_trace import MAX_FLOW_TRACE_HOPS, trace_flow
from codeatlas.indexer import RepositoryIndexer
from codeatlas.storage import GraphStore

LINEAR_FLOW = """
import requests
from fastapi import FastAPI

app = FastAPI()


@app.post("/orders")
def post_order():
    return create_order()


def create_order():
    return charge_payment()


def charge_payment():
    return requests.post(
        "https://payments.example/charge",
        json={"amount": 100},
    )
"""


class FlowTraceTests(unittest.TestCase):
    def make_indexed_repo(self, source: str) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "app").mkdir()
        (root / "app" / "__init__.py").write_text("", encoding="utf-8")
        (root / "app" / "api.py").write_text(
            textwrap.dedent(source).lstrip(),
            encoding="utf-8",
        )
        RepositoryIndexer().index(root)
        return root

    def test_linear_route_to_http_trace_preserves_directed_evidence(self) -> None:
        root = self.make_indexed_repo(LINEAR_FLOW)

        trace = trace_flow(root, "POST /orders")

        expected_node_keys = (
            "route:app.api.post_order",
            "symbol:app.api.post_order",
            "symbol:app.api.create_order",
            "symbol:app.api.charge_payment",
            "route:external:POST:https://payments.example/charge",
        )
        steps_by_id = {step.id: step for step in trace.steps}
        self.assertEqual(
            tuple(steps_by_id[step_id].node_key for step_id in trace.primary_path),
            expected_node_keys,
        )
        self.assertEqual(
            tuple(link.edge_type for link in trace.links),
            ("HANDLES", "CALLS", "CALLS", "HTTP_CALLS"),
        )
        self.assertEqual(
            tuple(link.source_line for link in trace.links),
            (None, 9, 13, 17),
        )
        self.assertEqual(
            tuple(link.source_lines for link in trace.links),
            ((), (9,), (13,), (17,)),
        )
        self.assertEqual(
            tuple(link.display for link in trace.links),
            ("POST /orders", "create_order", "charge_payment", "requests.post"),
        )
        self.assertEqual(
            tuple(link.source_node_key for link in trace.links),
            expected_node_keys[:-1],
        )
        self.assertEqual(
            tuple(link.target_node_key for link in trace.links),
            expected_node_keys[1:],
        )
        self.assertEqual(
            tuple(link.source_file_path for link in trace.links),
            ("app/api.py", "app/api.py", "app/api.py", "app/api.py"),
        )
        self.assertEqual(
            tuple(link.target_file_path for link in trace.links),
            ("app/api.py", "app/api.py", "app/api.py", None),
        )
        self.assertEqual(
            tuple(link.source_signature for link in trace.links),
            (None, "def post_order()", "def create_order()", "def charge_payment()"),
        )
        self.assertEqual(
            tuple(link.target_signature for link in trace.links),
            ("def post_order()", "def create_order()", "def charge_payment()", None),
        )
        self.assertEqual(
            tuple(link.ordering_basis for link in trace.links),
            ("graph_path", "source_order", "source_order", "source_order"),
        )
        self.assertEqual(
            tuple(link.resolution_tier for link in trace.links),
            ("parser", "same_module", "same_module", "heuristic"),
        )
        self.assertEqual(
            tuple(round(link.confidence, 2) for link in trace.links),
            (0.95, 0.8, 0.8, 0.78),
        )
        http_link = trace.links[-1]
        self.assertEqual(http_link.http_method, "POST")
        self.assertEqual(http_link.http_target, "https://payments.example/charge")
        self.assertIn('"https://payments.example/charge"', http_link.arguments)
        self.assertTrue(any("amount" in argument for argument in http_link.arguments))
        self.assertEqual(len(http_link.occurrences), 1)
        self.assertEqual(http_link.occurrences[0].source_line, 17)
        self.assertEqual(http_link.occurrences[0].arguments, http_link.arguments)
        self.assertEqual(http_link.occurrences[0].display, "requests.post")
        self.assertEqual(http_link.source_file_path, "app/api.py")
        self.assertIsNotNone(http_link.source_signature)
        self.assertTrue(steps_by_id[trace.primary_path[-1]].is_sink)
        self.assertEqual(steps_by_id[trace.primary_path[-1]].status, "external")
        self.assertTrue(trace.complete)
        self.assertEqual(trace.gaps, ())
        self.assertNotIn("unresolved", {step.status for step in trace.steps})

        payload = trace.to_dict()
        self.assertIsInstance(payload["steps"], list)
        self.assertIsInstance(payload["links"], list)
        self.assertIsInstance(payload["primary_path"], list)
        self.assertIsInstance(payload["links"][-1]["occurrences"], list)
        self.assertIsInstance(payload["links"][-1]["occurrences"][0]["arguments"], list)
        self.assertEqual(payload["trace_kind"], "static")
        self.assertEqual(payload["ordering_basis"], "graph_path")

        store = GraphStore(CodeAtlasPaths(root).database_path)
        try:
            store.initialize()
            persisted_ids = {
                int(row["id"])
                for row in store.connection.execute("SELECT id FROM edges").fetchall()
            }
            route_outgoing = store.outgoing_edges(
                "route:app.api.post_order",
                edge_types=("HANDLES",),
            )
            handler_incoming = store.incoming_edges(
                "symbol:app.api.post_order",
                edge_types=("HANDLES",),
            )
            reverse_handles = store.outgoing_edges(
                "symbol:app.api.post_order",
                edge_types=("HANDLES",),
            )
        finally:
            store.close()

        self.assertTrue({link.edge_id for link in trace.links} <= persisted_ids)
        self.assertEqual(len(route_outgoing), 1)
        self.assertEqual(len(handler_incoming), 1)
        self.assertEqual(reverse_handles, [])

    def test_path_only_entrypoint_resolves_the_route(self) -> None:
        root = self.make_indexed_repo(LINEAR_FLOW)

        trace = trace_flow(root, "/orders")

        self.assertTrue(trace.complete)
        self.assertEqual(trace.steps[0].label, "POST /orders")

    def test_unresolved_call_is_an_explicit_step_and_gap(self) -> None:
        root = self.make_indexed_repo(
            """
            from fastapi import FastAPI

            app = FastAPI()

            @app.post("/orders")
            def post_order():
                return process_payment()
            """
        )

        trace = trace_flow(root, "POST /orders")

        unresolved = [step for step in trace.steps if step.status == "unresolved"]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].node_key, "symbol_ref:process_payment")
        self.assertFalse(unresolved[0].is_sink)
        self.assertFalse(trace.complete)
        self.assertEqual(trace.primary_path, ())
        self.assertEqual(trace.links[-1].target_step_id, unresolved[0].id)
        self.assertIn("process_payment", trace.gaps[0])
        self.assertIn("app/api.py:7", trace.gaps[0])

    def test_recursive_flow_stops_at_cycle(self) -> None:
        root = self.make_indexed_repo(
            """
            from fastapi import FastAPI

            app = FastAPI()

            @app.get("/recursive")
            def recursive():
                return recursive()
            """
        )

        trace = trace_flow(root, "GET /recursive", max_hops=12)

        self.assertFalse(trace.complete)
        self.assertEqual(len(trace.links), 2)
        self.assertTrue(any("Cycle detected" in warning for warning in trace.warnings))

    def test_max_hops_stops_before_expanding_the_next_branch(self) -> None:
        root = self.make_indexed_repo(LINEAR_FLOW)

        trace = trace_flow(root, "POST /orders", max_hops=2)

        self.assertFalse(trace.complete)
        self.assertEqual(
            tuple(link.edge_type for link in trace.links),
            ("HANDLES", "CALLS"),
        )
        self.assertTrue(any("max_hops=2" in warning for warning in trace.warnings))

    def test_max_hops_enforces_inclusive_public_bounds(self) -> None:
        root = self.make_indexed_repo(LINEAR_FLOW)

        self.assertEqual(MAX_FLOW_TRACE_HOPS, 64)
        self.assertFalse(trace_flow(root, "POST /orders", max_hops=1).complete)
        self.assertTrue(
            trace_flow(root, "POST /orders", max_hops=MAX_FLOW_TRACE_HOPS).complete
        )
        for invalid_max_hops in (0, MAX_FLOW_TRACE_HOPS + 1):
            with self.subTest(max_hops=invalid_max_hops):
                with self.assertRaisesRegex(ValueError, "between 1 and 64"):
                    trace_flow(root, "POST /orders", max_hops=invalid_max_hops)

    def test_branches_are_retained_and_primary_path_uses_source_line_clue(self) -> None:
        root = self.make_indexed_repo(
            """
            import requests
            from fastapi import FastAPI

            app = FastAPI()

            @app.get("/branch")
            def branch():
                first()
                second()

            def first():
                return requests.get("https://example.com/first")

            def second():
                return requests.get("https://example.com/second")
            """
        )

        trace = trace_flow(root, "GET /branch")
        steps_by_id = {step.id: step for step in trace.steps}

        self.assertTrue(trace.complete)
        self.assertEqual(len([link for link in trace.links if link.edge_type == "CALLS"]), 2)
        self.assertEqual(len([link for link in trace.links if link.edge_type == "HTTP_CALLS"]), 2)
        self.assertEqual(
            tuple(steps_by_id[step_id].node_key for step_id in trace.primary_path),
            (
                "route:app.api.branch",
                "symbol:app.api.branch",
                "symbol:app.api.first",
                "route:external:GET:https://example.com/first",
            ),
        )
        self.assertTrue(
            any("does not assert runtime order" in warning for warning in trace.warnings)
        )

    def test_repeated_http_calls_preserve_each_occurrence_and_dynamic_gap(self) -> None:
        root = self.make_indexed_repo(
            """
            import requests
            from fastapi import FastAPI

            app = FastAPI()

            @app.post("/payments")
            def payments(dynamic_url):
                requests.post("https://example.com/first", json={"amount": 1})
                requests.post("https://example.com/second", json={"amount": 2})
                return requests.post(dynamic_url, json={"amount": 3})
            """
        )

        trace = trace_flow(root, "POST /payments")
        http_links = [link for link in trace.links if link.edge_type == "HTTP_CALLS"]
        unresolved_links = [
            link
            for link in trace.links
            if link.edge_type == "CALLS" and link.target_node_key == "symbol_ref:post"
        ]

        self.assertEqual(
            [(link.source_line, link.http_target) for link in http_links],
            [
                (8, "https://example.com/first"),
                (9, "https://example.com/second"),
            ],
        )
        self.assertEqual(
            [link.arguments for link in http_links],
            [
                ('"https://example.com/first"', 'json={"amount": 1}'),
                ('"https://example.com/second"', 'json={"amount": 2}'),
            ],
        )
        self.assertEqual(len(unresolved_links), 1)
        self.assertEqual(unresolved_links[0].source_line, 10)
        self.assertEqual(
            unresolved_links[0].arguments,
            ("dynamic_url", 'json={"amount": 3}'),
        )
        self.assertEqual(
            [link.source_line for link in trace.links],
            [None, 8, 9, 10],
        )
        self.assertFalse(trace.complete)
        self.assertIn("app/api.py:10", trace.gaps[0])

    def test_repeated_same_http_target_does_not_create_a_false_gap(self) -> None:
        root = self.make_indexed_repo(
            """
            import requests
            from fastapi import FastAPI

            app = FastAPI()

            @app.post("/retry")
            def retry():
                requests.post("https://example.com/pay", json={"attempt": 1})
                return requests.post("https://example.com/pay", json={"attempt": 2})
            """
        )

        trace = trace_flow(root, "POST /retry")
        http_links = [link for link in trace.links if link.edge_type == "HTTP_CALLS"]

        self.assertTrue(trace.complete)
        self.assertEqual(trace.gaps, ())
        self.assertEqual(len(http_links), 1)
        self.assertEqual(http_links[0].source_lines, (8, 9))
        self.assertEqual(
            http_links[0].arguments,
            ('"https://example.com/pay"', 'json={"attempt": 1}'),
        )
        self.assertEqual(
            [occurrence.source_line for occurrence in http_links[0].occurrences],
            [8, 9],
        )
        self.assertEqual(
            [occurrence.arguments for occurrence in http_links[0].occurrences],
            [
                ('"https://example.com/pay"', 'json={"attempt": 1}'),
                ('"https://example.com/pay"', 'json={"attempt": 2}'),
            ],
        )
        self.assertEqual(
            [occurrence.display for occurrence in http_links[0].occurrences],
            ["requests.post", "requests.post"],
        )

    def test_repeated_internal_target_preserves_both_argument_sets(self) -> None:
        root = self.make_indexed_repo(
            """
            from fastapi import FastAPI

            app = FastAPI()

            @app.post("/process")
            def run():
                process("first")
                return process("second")

            def process(value):
                return value
            """
        )

        trace = trace_flow(root, "POST /process")
        process_link = next(
            link
            for link in trace.links
            if link.target_node_key == "symbol:app.api.process"
        )

        self.assertEqual(process_link.source_lines, (7, 8))
        self.assertEqual(process_link.arguments, ('"first"',))
        self.assertEqual(
            [
                (occurrence.source_line, occurrence.arguments, occurrence.display)
                for occurrence in process_link.occurrences
            ],
            [
                (7, ('"first"',), "process"),
                (8, ('"second"',), "process"),
            ],
        )

    def test_source_lines_retain_evidence_beyond_capped_occurrence_examples(self) -> None:
        source_lines = [
            "from fastapi import FastAPI",
            "",
            "app = FastAPI()",
            "",
            '@app.post("/many")',
            "def run():",
            *(f"    process({value})" for value in range(22)),
            "",
            "def process(value):",
            "    return value",
        ]
        root = self.make_indexed_repo("\n".join(source_lines))

        trace = trace_flow(root, "POST /many")
        process_link = next(
            link
            for link in trace.links
            if link.target_node_key == "symbol:app.api.process"
        )

        self.assertEqual(len(process_link.occurrences), 20)
        self.assertEqual(len(process_link.source_lines), 22)
        self.assertEqual(process_link.occurrences[0].arguments, ("0",))
        self.assertEqual(process_link.occurrences[-1].arguments, ("19",))


if __name__ == "__main__":
    unittest.main()
