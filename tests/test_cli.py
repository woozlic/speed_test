import threading
import urllib.error
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from speed_test import cli

PAYLOAD = b"x" * 300_000


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/file":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_fetch_downloads_whole_body(base_url: str) -> None:
    measurement = cli.fetch(f"{base_url}/file", 5, cli.make_ssl_context(False))

    assert measurement.size == len(PAYLOAD)
    assert measurement.elapsed > 0


def test_fetch_raises_on_http_error(base_url: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        cli.fetch(f"{base_url}/missing", 5, cli.make_ssl_context(False))

    assert cli.describe_error(exc_info.value) == "HTTP 404 Not Found"


def test_summarize() -> None:
    summary = cli.summarize(
        [
            cli.Measurement(size=2_000_000, elapsed=1.0),
            cli.Measurement(size=6_000_000, elapsed=3.0),
        ]
    )

    assert summary.requests == 2
    assert summary.total_size == 8_000_000
    assert summary.average_time == pytest.approx(2.0)
    assert summary.speed_mb_per_s == pytest.approx(2.0)


def test_main_reports_summary(base_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([f"{base_url}/file", "-n", "3"]) == 0

    out = capsys.readouterr().out
    assert "Успешных запросов:    3/3" in out
    assert "Скачано всего:        0.90 МБ" in out


def test_main_fails_when_all_requests_fail(
    base_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([f"{base_url}/missing", "-n", "2"]) == 1

    err = capsys.readouterr().err
    assert err.count("HTTP 404") == 2
    assert "Ни один запрос не выполнен успешно." in err


def test_main_handles_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    results = iter([cli.Measurement(size=1_000_000, elapsed=0.5)])

    def fake_fetch(*args: object) -> cli.Measurement:
        try:
            return next(results)
        except StopIteration:
            raise KeyboardInterrupt from None

    monkeypatch.setattr(cli, "fetch", fake_fetch)

    assert cli.main(["http://example.com/file", "-n", "5"]) == 130

    captured = capsys.readouterr()
    assert "Прервано." in captured.err
    assert "Успешных запросов:    1/5" in captured.out
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("url", ["ftp://example.com/file", "example.com/file"])
def test_rejects_non_http_url(url: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main([url])

    assert exc_info.value.code == 2
