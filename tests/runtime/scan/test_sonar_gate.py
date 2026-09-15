"""Behaviour tests for runtime/scan/sonar_gate.py.

The SonarQube API is stubbed by a local HTTP server, so these tests exercise the
real request path and the real response parsing without touching
sonarqube.example.com. Every test names a way the previous template reported
success it had not earned.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "runtime" / "scan" / "sonar_gate.py"

PASS, GATE_FAILURE, EXECUTION_ERROR = 0, 1, 2


def load_module():
    spec = importlib.util.spec_from_file_location("sonar_gate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sonar_gate = load_module()


class StubSonarQube:
    """Serves one compute-engine task and one quality gate status."""

    def __init__(self, task: dict, gate: dict):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server's interface
                if self.headers.get("Authorization") != "Bearer test-token":
                    self.send_error(401, "unauthorized")
                    return
                if self.path.startswith("/api/ce/task"):
                    payload = {"task": outer.task}
                elif self.path.startswith("/api/qualitygates/project_status"):
                    payload = {"projectStatus": outer.gate}
                else:
                    self.send_error(404, "not found")
                    return
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.task = task
        self.gate = gate
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def write_report_task(tmp_path: Path, server_url: str) -> Path:
    path = tmp_path / "report-task.txt"
    path.write_text(
        "projectKey=platform:demo\n"
        f"serverUrl={server_url}\n"
        f"dashboardUrl={server_url}/dashboard?id=platform:demo\n"
        f"ceTaskUrl={server_url}/api/ce/task?id=AXY\n"
    )
    return path


def run_gate(tmp_path: Path, status: int, policy_mode: str, reports=()) -> int:
    argv = [
        "gate",
        "--report-task",
        str(tmp_path / "report-task.txt"),
        "--status",
        str(status),
        "--policy-mode",
        policy_mode,
        "--out",
        str(tmp_path / "gate-result.json"),
    ]
    for report in reports:
        argv += ["--report", report]
    return sonar_gate.main(argv)


@pytest.fixture(autouse=True)
def sonar_token(monkeypatch):
    monkeypatch.setenv("SONAR_TOKEN", "test-token")


SUCCESSFUL_TASK = {"id": "AXY", "status": "SUCCESS", "analysisId": "A1"}
RED_GATE = {
    "status": "ERROR",
    "conditions": [
        {
            "metricKey": "new_coverage",
            "comparator": "LT",
            "errorThreshold": "80",
            "actualValue": "41.2",
            "status": "ERROR",
        }
    ],
}
GREEN_GATE = {"status": "OK", "conditions": []}


def test_a_red_gate_fails_under_a_blocking_policy(tmp_path):
    """An upload is not a pass, and neither is a red gate nobody read."""
    with StubSonarQube(SUCCESSFUL_TASK, RED_GATE) as stub:
        write_report_task(tmp_path, stub.url)
        assert run_gate(tmp_path, 1, "blocking") == GATE_FAILURE
    evidence = json.loads((tmp_path / "gate-result.json").read_text())
    assert evidence["gate"]["status"] == "ERROR"
    assert evidence["gate"]["conditions"][0]["metricKey"] == "new_coverage"
    assert evidence["analysis"]["analysis_id"] == "A1"
    assert evidence["subject"]["project_key"] == "platform:demo"


def test_a_red_gate_is_recorded_under_an_advisory_policy(tmp_path):
    with StubSonarQube(SUCCESSFUL_TASK, RED_GATE) as stub:
        write_report_task(tmp_path, stub.url)
        assert run_gate(tmp_path, 1, "advisory") == PASS
    assert json.loads((tmp_path / "gate-result.json").read_text())["gate"]["status"] == "ERROR"


def test_a_green_gate_passes(tmp_path):
    with StubSonarQube(SUCCESSFUL_TASK, GREEN_GATE) as stub:
        write_report_task(tmp_path, stub.url)
        assert run_gate(tmp_path, 0, "blocking") == PASS


def test_a_green_gate_with_a_failed_scanner_is_an_execution_error(tmp_path):
    """sonar-scanner uses exit 1 for an analysis error and for a red gate.

    A green gate alongside a non-zero scanner means the failure was the analysis,
    not the gate, and advisory mode does not cover it.
    """
    with StubSonarQube(SUCCESSFUL_TASK, GREEN_GATE) as stub:
        write_report_task(tmp_path, stub.url)
        assert run_gate(tmp_path, 1, "advisory") == EXECUTION_ERROR


def test_a_failed_compute_engine_task_is_an_execution_error(tmp_path):
    with StubSonarQube({"id": "AXY", "status": "FAILED"}, GREEN_GATE) as stub:
        write_report_task(tmp_path, stub.url)
        assert run_gate(tmp_path, 1, "advisory") == EXECUTION_ERROR


def test_a_missing_report_task_is_an_execution_error(tmp_path):
    assert run_gate(tmp_path, 0, "advisory") == EXECUTION_ERROR


def test_a_missing_token_is_an_execution_error(tmp_path, monkeypatch):
    monkeypatch.delenv("SONAR_TOKEN")
    with StubSonarQube(SUCCESSFUL_TASK, GREEN_GATE) as stub:
        write_report_task(tmp_path, stub.url)
        assert run_gate(tmp_path, 0, "blocking") == EXECUTION_ERROR


def test_an_unreachable_server_is_an_execution_error(tmp_path):
    with StubSonarQube(SUCCESSFUL_TASK, GREEN_GATE) as stub:
        url = stub.url
    write_report_task(tmp_path, url)
    assert run_gate(tmp_path, 0, "advisory") == EXECUTION_ERROR


def test_a_declared_sarif_report_that_is_missing_fails(tmp_path, monkeypatch):
    """The `needs:` override trap.

    A consumer overriding `needs:` replaces the component's list and the scanner
    artifacts never arrive. The template this replaces logged a line and went
    green; a declared report that is absent must fail instead.
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(sonar_gate.ExecutionError) as error:
        sonar_gate.collect_sarif(["reports/semgrep.sarif"], ".")
    assert "not present" in str(error.value)


def test_a_declared_sarif_report_that_is_present_is_imported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "semgrep.sarif").write_text(json.dumps({"runs": []}))
    assert sonar_gate.collect_sarif(["semgrep.sarif"], ".") == ["semgrep.sarif"]


def test_unreadable_sarif_is_an_execution_error_not_an_empty_import(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "semgrep.sarif").write_text("<html>not sarif</html>")
    with pytest.raises(sonar_gate.ExecutionError):
        sonar_gate.collect_sarif(["semgrep.sarif"], ".")


def test_findings_with_a_space_in_their_path_are_reported_as_dropped(tmp_path, monkeypatch, capsys):
    """SonarQube's importer cannot resolve such a path. Verified on CB 26.4."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "semgrep.sarif").write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "results": [
                            {
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "my dir/app.py"
                                            }
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        )
    )
    sonar_gate.collect_sarif(["semgrep.sarif"], ".")
    stderr = capsys.readouterr().err
    assert "unimportable path: my dir/app.py" in stderr
    assert "1 finding(s) will NOT reach SonarQube" in stderr


def test_discovery_is_used_only_when_nothing_is_declared(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "trivy-fs.sarif").write_text(json.dumps({"runs": []}))
    assert sonar_gate.collect_sarif([], ".") == ["trivy-fs.sarif"]
