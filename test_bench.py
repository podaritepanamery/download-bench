"""Tests for bench.py. Run with:  python3 -m pytest

Most tests run bench.run() against a local HTTP server that can misbehave on
purpose (404, a request that fails once, a stalled reply, a body cut off).
A few run bench.py as a command to check exit codes and usage errors.
"""

import io
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import bench

HERE = os.path.dirname(os.path.abspath(__file__))
BIG = 100_000    # more than one 16 KB chunk, so a transfer speed exists
SMALL = 1_000    # less than one chunk, so it does not
STALL = 1.5      # how long the server sits silent, in seconds
START_DELAY = 0.5  # how long /slow-start waits before it answers
SHORT_TIMEOUT = 0.3


# --- local test server -------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def head(self, length):
        self.send_response(200)
        self.send_header("Content-Length", str(length))
        self.end_headers()

    def chunked_head(self):
        self.send_response(200)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def do_GET(self):
        srv = self.server
        with srv.lock:  # count how many requests are being served at the same time
            srv.in_flight += 1
            srv.max_in_flight = max(srv.max_in_flight, srv.in_flight)
        try:
            self.route()
        finally:
            with srv.lock:
                srv.in_flight -= 1

    def route(self):
        path = self.path
        hits = self.server.hits
        hits[path] = hits.get(path, 0) + 1
        w = self.wfile

        if path == "/ok":
            self.head(BIG)
            w.write(b"x" * BIG)
        elif path == "/small":
            self.head(SMALL)
            w.write(b"x" * SMALL)
        elif path == "/flaky":  # second request gets a 500, the rest are fine
            if hits[path] == 2:
                self.send_error(500)
            else:
                self.head(BIG)
                w.write(b"x" * BIG)
        elif path == "/slow-start":  # waits, then sends the whole body at once
            time.sleep(START_DELAY)
            self.head(BIG)
            w.write(b"x" * BIG)
        elif path == "/stall-headers":  # no reply at all for a while
            time.sleep(STALL)
        elif path == "/stall-body":  # headers and a bit of body, then silence
            self.head(BIG)
            w.write(b"x" * 20_000)
            time.sleep(STALL)
        elif path == "/cut":  # promises BIG bytes, sends 30 000, hangs up
            self.head(BIG)
            w.write(b"x" * 30_000)
        elif path == "/cut-chunked":  # two chunks, then hangs up, no end marker
            self.chunked_head()
            for _ in range(2):
                w.write(b"%x\r\n" % 5000 + b"x" * 5000 + b"\r\n")
        elif path == "/chunked-ok":  # three chunks and a proper end marker
            self.chunked_head()
            for _ in range(3):
                w.write(b"%x\r\n" % 20_000 + b"x" * 20_000 + b"\r\n")
            w.write(b"0\r\n\r\n")
        elif path == "/no-length":  # no Content-Length, ends when the server closes
            self.send_response(200)
            self.send_header("Connection", "close")
            self.end_headers()
            w.write(b"x" * BIG)
            self.close_connection = True
        elif path.startswith("/size/"):  # a body of exactly this many bytes
            n = int(path[len("/size/"):])
            self.head(n)
            w.write(b"x" * n)
        elif path == "/pause":  # a short pause, so overlapping requests would show
            time.sleep(0.05)
            self.head(BIG)
            w.write(b"x" * BIG)
        elif path == "/drip":  # 8 pieces of 10 bytes, 0.1 s apart
            self.head(80)
            for _ in range(8):
                w.write(b"x" * 10)
                w.flush()
                time.sleep(0.1)
        elif path in ("/redirect", "/redirect-loop"):
            self.send_response(302)
            self.send_header("Location", "/ok" if path == "/redirect" else path)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(404)


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # the client hanging up on a stalled reply is expected


class QuietServer6(QuietServer):
    address_family = socket.AF_INET6


class Server:
    def __init__(self, ipv6=False):
        if ipv6:
            self.httpd = QuietServer6(("::1", 0), Handler)
        else:
            self.httpd = QuietServer(("127.0.0.1", 0), Handler)
        self.host = "[::1]" if ipv6 else "127.0.0.1"
        self.httpd.hits = {}
        self.httpd.lock = threading.Lock()
        self.httpd.in_flight = 0
        self.httpd.max_in_flight = 0
        self.hits = self.httpd.hits
        self.port = self.httpd.server_address[1]
        # a short poll interval makes shutdown() return at once, not after 0.5 s
        threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()

    @property
    def max_in_flight(self):
        return self.httpd.max_in_flight

    def url(self, path, host=None):
        return "http://%s:%d%s" % (host or self.host, self.port, path)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def server():
    srv = Server()
    yield srv
    srv.stop()


def run(url, count=3, timeout=5.0):
    out = io.StringIO()
    code = bench.run(url, count, timeout, out=out)
    return code, out.getvalue()


def request_lines(text):
    return [line for line in text.splitlines() if line.startswith("request ")]


# --- fake fetch for exact arithmetic -----------------------------------------


def result(total, headers=0.1, received=100_000, xfer_bytes=80_000, xfer_secs=0.05):
    return bench.Result(received, total, headers, xfer_bytes, xfer_secs)


def fake_fetch(monkeypatch, outcomes):
    """Make bench.fetch return or raise the given outcomes, one per call."""
    queue = list(outcomes)

    def fetch(url, timeout):
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(bench, "fetch", fetch)


def test_spread_shows_outliers_the_average_hides(monkeypatch):
    fake_fetch(monkeypatch, [result(0.15), result(0.42), result(0.20)])
    code, text = run("http://x", count=3)
    assert code == 0
    assert "average time:     0.257 s" in text
    assert "median time:      0.200 s" in text
    assert "min / max time:   0.150 s / 0.420 s" in text


def test_median_of_even_count_is_mean_of_middle_two(monkeypatch):
    fake_fetch(monkeypatch, [result(t) for t in (0.4, 0.1, 0.3, 0.2)])
    _, text = run("http://x", count=4)
    assert "median time:      0.250 s" in text


def test_single_request_has_equal_min_median_max(monkeypatch):
    fake_fetch(monkeypatch, [result(0.5)])
    _, text = run("http://x", count=1)
    assert "average time:     0.500 s" in text
    assert "median time:      0.500 s" in text
    assert "min / max time:   0.500 s / 0.500 s" in text


def test_failed_request_does_not_stop_the_run(monkeypatch):
    fake_fetch(monkeypatch, [result(0.1), OSError("boom"), result(0.3)])
    code, text = run("http://x", count=3)
    assert code == 1
    assert len(request_lines(text)) == 3
    assert "request  2: FAILED after" in text
    assert "boom" in text
    assert "requests:         2 ok, 1 failed" in text


def test_stats_cover_only_successful_requests(monkeypatch):
    fake_fetch(monkeypatch, [result(0.1), OSError("boom"), result(0.3)])
    _, text = run("http://x", count=3)
    assert "average time:     0.200 s" in text
    assert "min / max time:   0.100 s / 0.300 s" in text
    assert "total data:       200000 bytes" in text


def test_all_failed_prints_no_stats(monkeypatch):
    fake_fetch(monkeypatch, [OSError("a"), OSError("b")])
    code, text = run("http://x", count=2)
    assert code == 1
    assert "requests:         0 ok, 2 failed" in text
    assert "nothing to measure" in text
    assert "average time" not in text
    assert "speed" not in text


def test_all_ok_returns_zero(monkeypatch):
    fake_fetch(monkeypatch, [result(0.1), result(0.2)])
    code, text = run("http://x", count=2)
    assert code == 0
    assert "requests:         2 ok, 0 failed" in text


def test_transfer_speed_is_na_without_transfer_bytes(monkeypatch):
    fake_fetch(monkeypatch, [result(0.1, received=1000, xfer_bytes=0, xfer_secs=0.0)])
    _, text = run("http://x", count=1)
    assert "speed, transfer:  n/a" in text
    assert "speed, overall:   0.010 MB/s" in text


def test_format_speed():
    assert bench.format_speed(1_000_000, 1.0) == "1.000 MB/s (8.000 Mbit/s)"
    assert bench.format_speed(0, 1.0).startswith("n/a")
    assert bench.format_speed(100, 0.0).startswith("n/a")


def test_describe_http_error():
    err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
    assert bench.describe(err) == "HTTP 404 Not Found"


@pytest.mark.parametrize(
    "url, ok",
    [
        ("http://example.com/a.png", True),
        ("https://example.com", True),
        ("http://127.0.0.1:8000/x", True),
        ("not-a-url", False),
        ("example.com/a.png", False),
        ("http://", False),
        ("file:///etc/hosts", False),
        ("file://localhost/etc/hosts", False),
        ("ftp://example.com/a", False),
        ("http://[::1", False),
        ("", False),
    ],
)
def test_is_http_url(url, ok):
    assert bench.is_http_url(url) is ok


# --- real HTTP against the local server --------------------------------------


def test_normal_response(server):
    code, text = run(server.url("/ok"), count=3)
    assert code == 0
    assert len(request_lines(text)) == 3
    assert "requests:         3 ok, 0 failed" in text
    assert "total data:       300000 bytes (0.300 MB)" in text
    assert "speed, overall:   n/a" not in text
    assert "speed, transfer:  n/a" not in text
    assert server.hits["/ok"] == 3


def test_small_body_has_no_transfer_speed(server):
    code, text = run(server.url("/small"), count=2)
    assert code == 0
    assert "total data:       2000 bytes" in text
    assert "speed, transfer:  n/a" in text
    assert "speed, overall:   n/a" not in text


def test_transfer_time_leaves_out_the_wait_for_headers(server):
    r = bench.fetch(server.url("/slow-start"), 5.0)
    assert r.headers >= START_DELAY * 0.9  # the wait shows up in "headers"
    assert r.total >= r.headers
    assert r.xfer_secs < START_DELAY / 2  # and not in the transfer time


def test_transfer_bytes_leave_out_the_first_chunk(server):
    r = bench.fetch(server.url("/ok"), 5.0)
    assert r.received == BIG
    assert r.xfer_bytes == BIG - bench.CHUNK


def test_404_fails_every_request_and_goes_to_the_end(server):
    code, text = run(server.url("/missing"), count=3)
    assert code == 1
    assert text.count("FAILED") == 3
    assert "HTTP 404" in text
    assert "requests:         0 ok, 3 failed" in text
    assert server.hits["/missing"] == 3


def test_one_bad_response_in_the_middle(server):
    code, text = run(server.url("/flaky"), count=3)
    assert code == 1
    assert server.hits["/flaky"] == 3
    lines = request_lines(text)
    assert "FAILED" not in lines[0]
    assert "FAILED" in lines[1] and "HTTP 500" in lines[1]
    assert "FAILED" not in lines[2]
    assert "requests:         2 ok, 1 failed" in text
    assert "total data:       200000 bytes" in text


def test_timeout_while_waiting_for_headers(server):
    code, text = run(server.url("/stall-headers"), count=2, timeout=SHORT_TIMEOUT)
    assert code == 1
    assert text.count("FAILED") == 2
    assert "timed out" in text
    assert "requests:         0 ok, 2 failed" in text


def test_timeout_in_the_middle_of_the_body(server):
    code, text = run(server.url("/stall-body"), count=2, timeout=SHORT_TIMEOUT)
    assert code == 1
    assert text.count("FAILED") == 2
    assert "timed out" in text


def test_connection_dropped_mid_body_is_a_failure(server):
    code, text = run(server.url("/cut"), count=2)
    assert code == 1
    assert text.count("FAILED") == 2
    assert "got 30000 of 100000 bytes" in text
    assert "requests:         0 ok, 2 failed" in text


def test_chunked_body_cut_off_is_a_failure(server):
    code, text = run(server.url("/cut-chunked"), count=2)
    assert code == 1
    assert text.count("FAILED") == 2
    assert "requests:         0 ok, 2 failed" in text


def test_chunked_body_that_ends_properly_is_fine(server):
    code, text = run(server.url("/chunked-ok"), count=2)
    assert code == 0
    assert "total data:       120000 bytes" in text


def test_body_without_content_length_is_fine(server):
    code, text = run(server.url("/no-length"), count=2)
    assert code == 0
    assert "total data:       200000 bytes" in text


def test_connection_refused():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens on this port any more
    code, text = run("http://127.0.0.1:%d/" % port, count=2)
    assert code == 1
    assert text.count("FAILED") == 2
    assert "requests:         0 ok, 2 failed" in text


def test_requests_go_one_at_a_time(server):
    code, text = run(server.url("/pause"), count=4)
    assert code == 0
    assert server.hits["/pause"] == 4
    assert server.max_in_flight == 1  # never two requests open together


def test_waits_for_the_whole_body(server):
    r = bench.fetch(server.url("/drip"), 5.0)  # headers at once, body takes ~0.7 s
    assert r.received == 80
    assert r.headers < 0.3
    assert r.total >= 0.6


def test_timeout_limits_each_wait_not_the_whole_request(server):
    # whole download takes ~0.8 s, longer than the 0.5 s timeout, but the
    # server never stays silent for more than 0.1 s, so it must succeed
    code, text = run(server.url("/drip"), count=1, timeout=0.5)
    assert code == 0
    assert "80 bytes" in text


@pytest.mark.parametrize(
    "n",
    [0, 1, bench.CHUNK - 1, bench.CHUNK, bench.CHUNK + 1, 2 * bench.CHUNK, 3 * bench.CHUNK + 5],
)
def test_body_sizes_around_the_chunk_size(server, n):
    r = bench.fetch(server.url("/size/%d" % n), 5.0)
    assert r.received == n
    assert r.xfer_bytes == max(0, n - bench.CHUNK)


def test_redirect_is_followed(server):
    code, text = run(server.url("/redirect"), count=2)
    assert code == 0
    assert "total data:       200000 bytes" in text


def test_redirect_loop_is_a_failure(server):
    code, text = run(server.url("/redirect-loop"), count=1)
    assert code == 1
    assert "FAILED" in text


def test_many_requests(server):
    code, text = run(server.url("/small"), count=200)
    assert code == 0
    assert "requests:         200 ok, 0 failed" in text
    assert server.hits["/small"] == 200


def test_host_name_localhost(server):
    code, _ = run(server.url("/ok", host="localhost"), count=2)
    assert code == 0


def test_ipv6_address():
    try:
        srv = Server(ipv6=True)
    except OSError:
        pytest.skip("no IPv6 loopback on this machine")
    try:
        code, text = run(srv.url("/ok"), count=2)
    finally:
        srv.stop()
    assert code == 0
    assert "total data:       200000 bytes" in text


# --- the command line --------------------------------------------------------


def cli(*args):
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "bench.py")] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


def test_cli_success(server):
    p = cli("-n", "2", server.url("/ok"))
    assert p.returncode == 0
    assert "requests:         2 ok, 0 failed" in p.stdout
    assert "speed, transfer:" in p.stdout
    assert p.stderr == ""


def test_cli_default_is_ten_requests(server):
    p = cli(server.url("/ok"))  # no -n
    assert p.returncode == 0
    assert "requests:         10 ok, 0 failed" in p.stdout
    assert server.hits["/ok"] == 10


def test_cli_writes_no_files(server, tmp_path):
    p = subprocess.run(
        [sys.executable, os.path.join(HERE, "bench.py"), "-n", "2", server.url("/ok")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        cwd=str(tmp_path),
    )
    assert p.returncode == 0
    assert list(tmp_path.iterdir()) == []


def test_cli_exit_code_1_when_a_request_fails(server):
    p = cli("-n", "2", server.url("/missing"))
    assert p.returncode == 1
    assert "FAILED" in p.stdout


@pytest.mark.parametrize(
    "args, message",
    [
        (["not-a-url"], "http:// or https://"),
        (["file:///etc/hosts"], "http:// or https://"),
        (["-n", "0", "http://127.0.0.1/"], "--count"),
        (["-t", "0", "http://127.0.0.1/"], "--timeout"),
    ],
)
def test_cli_usage_errors_exit_2_and_make_no_request(args, message):
    p = cli(*args)
    assert p.returncode == 2
    assert message in p.stderr
    assert p.stdout == ""
