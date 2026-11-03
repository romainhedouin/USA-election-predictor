"""A refresh where some states fail must still publish, not freeze the race.

fetch_results.py carries the failed states' rows forward from the served CSV
(only if that CSV is live data for the same cycle) and exits EXIT_PARTIAL;
server.refresh_race promotes that file but reports the race as degraded.
No browser, no network.
"""

import csv
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from requests.adapters import HTTPAdapter

import fetch_results
import nbc_api
import server
from races import CSV_HEADER


def _write_served(path, source, cycle="2024"):
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(CSV_HEADER)
        writer.writerow(["New York", "Kings", "36047", "counties", 1, 1, 50.0, 1, 1, "D", "R"])
        writer.writerow(["Ohio", "Adams", "39001", "counties", 1, 1, 50.0, 1, 1, "D", "R"])
    path.with_suffix(".meta.json").write_text(json.dumps({"source": source, "dataYear": cycle}))


def test_carries_forward_only_failed_states_from_live_same_cycle(tmp_path):
    served = tmp_path / "raw_data.csv"
    _write_served(served, "live")
    rows = fetch_results.carried_forward_rows(served, {"new-york"}, "2024")
    assert [row[0] for row in rows] == ["New York"]
    assert fetch_results.carried_forward_rows(served, {"new-york"}, "2028") == []


def test_never_carries_forward_mock_data(tmp_path):
    served = tmp_path / "raw_data.csv"
    _write_served(served, "mock")
    assert fetch_results.carried_forward_rows(served, {"new-york"}, "2024") == []


def test_missing_previous_is_not_an_error(tmp_path):
    assert fetch_results.carried_forward_rows(tmp_path / "nope.csv", {"ohio"}, "2024") == []


def test_server_promotes_partial_refresh_but_reports_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setitem(server.STATE, "president", dict(server.STATE["president"]))
    staged = tmp_path / ".staging" / "raw_data.csv"

    def fake_run(argv, timeout, label):
        assert "--previous" in argv
        staged.parent.mkdir(exist_ok=True)
        _write_served(staged, "live")
        return fetch_results.EXIT_PARTIAL, "Wrote 2 rows\nFailed: ohio (ReadTimeout)"

    monkeypatch.setattr(server, "_run", fake_run)
    assert server.refresh_race("president") is True
    assert (tmp_path / "raw_data.csv").exists()
    entry = server.STATE["president"]
    assert entry["consecutive_failures"] == 0
    assert entry["last_error"].startswith("partial: Failed: ohio")

    monkeypatch.setattr(server, "RACES", ("president",))
    for race in server.ALL_RACES:
        _write_served(tmp_path / server.csv_name(race), "live")
    code, body = server.health_payload()
    assert code == 200 and body["status"] == "degraded"


def test_nbc_session_retries_a_transient_503_once():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(1)
            code, body = (503, b"") if len(calls) == 1 else (200, b'{"ok": true}')
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        session = requests.Session()
        # Same retry policy as production, mounted on http:// for the local stub.
        session.mount("http://", HTTPAdapter(max_retries=nbc_api._SESSION.get_adapter("https://x").max_retries))
        response = session.get(f"http://127.0.0.1:{httpd.server_port}/", timeout=5)
        assert response.status_code == 200 and len(calls) == 2
    finally:
        httpd.shutdown()


def _run_main(monkeypatch, tmp_path, fetched, previous=None):
    """fetch_results.main() with fetch_race stubbed; returns the exit code (None = wrote)."""
    argv = ["fetch_results.py", "--race", "senate", "--out-dir", str(tmp_path)]
    if previous:
        argv += ["--previous", str(previous)]
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(fetch_results, "fetch_race", lambda race, cycle, skip: fetched)
    try:
        fetch_results.main()
    except SystemExit as exit_:
        return exit_.code
    return None


def test_nothing_counted_yet_publishes_an_empty_live_file(monkeypatch, tmp_path):
    """Before polls close every area is at 0%. The page must say "live, nothing
    counted" rather than keep showing whatever was served before."""
    assert _run_main(monkeypatch, tmp_path, ([], [], "2026-11-03T10:55:11Z", 3124)) is None
    csv_text = (tmp_path / "raw_data_senate.csv").read_text(encoding="utf-8").splitlines()
    assert csv_text == [";".join(CSV_HEADER)]
    meta = json.loads((tmp_path / "raw_data_senate.meta.json").read_text(encoding="utf-8"))
    assert (meta["source"], meta["dataYear"], meta["rows"]) == ("live", "2026", 0)


def test_empty_result_never_replaces_real_counts(monkeypatch, tmp_path):
    served = tmp_path / "served" / "raw_data_senate.csv"
    served.parent.mkdir()
    _write_served(served, "live", cycle="2026")
    served.with_suffix(".meta.json").write_text(json.dumps({"source": "live", "dataYear": "2026", "rows": 2}))
    assert _run_main(monkeypatch, tmp_path, ([], [], None, 3124), previous=served).startswith("No results parsed")
    assert not (tmp_path / "raw_data_senate.csv").exists()


def test_empty_result_without_any_reporting_areas_is_still_a_failure(monkeypatch, tmp_path):
    """No areas at all means NBC's payload changed or broke, not "polls not closed"."""
    assert _run_main(monkeypatch, tmp_path, ([], [], None, 0)).startswith("No results parsed")
    assert _run_main(monkeypatch, tmp_path, ([], [("ohio", "ReadTimeout")], None, 80)).startswith("No results parsed")


def test_server_publishes_an_empty_live_file_but_not_an_empty_mock(tmp_path):
    empty = tmp_path / "raw_data_senate.csv"
    empty.write_text(";".join(CSV_HEADER) + "\n", encoding="utf-8")
    assert server._looks_like_csv(empty) == (False, "header only, no data rows")
    assert server._looks_like_csv(empty, allow_empty=True) == (True, 0)

    meta = tmp_path / "raw_data_senate.meta.json"
    assert not server._is_live_meta(meta)
    meta.write_text(json.dumps({"source": "mock"}))
    assert not server._is_live_meta(meta)
    meta.write_text(json.dumps({"source": "live"}))
    assert server._is_live_meta(meta)
