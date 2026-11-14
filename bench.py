#!/usr/bin/env python3
"""Download a URL N times in a row and report average time, total size and speed."""

import argparse
import collections
import http.client
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Body is read in chunks of this size. The first chunk of every response is
# not counted in the transfer speed: part of it may already sit in the
# socket buffer when the headers arrive, which would make the speed look
# too high. Bodies of this size or smaller have no transfer speed.
CHUNK = 16 * 1024

# A request counts toward the transfer speed only if the part after the first
# chunk took at least this long. Timer and read-call overhead is some tens of
# microseconds, so a shorter interval gives a wrong speed (a 16 385 byte body
# once printed 0.056 MB/s on a link that did 2000 MB/s). At 1 ms that overhead
# is about 1% of the measurement.
MIN_TRANSFER_SECS = 0.001

USER_AGENT = "download-bench/1.3"

EXIT_OK = 0
EXIT_FAILED = 1       # at least one request failed
EXIT_USAGE = 2        # wrong arguments (argparse uses 2 as well)
EXIT_INTERRUPTED = 130  # Ctrl-C, the usual 128 + SIGINT
EXIT_PIPE_CLOSED = 141  # stdout was closed by the reader, the usual 128 + SIGPIPE

# received:    all body bytes
# total:       seconds from the start of the request to the last body byte
# headers:     seconds from the start of the request to the response headers
# xfer_bytes:  body bytes counted for the transfer speed (all but the first
#              chunk), 0 if that part was too short to time
# xfer_secs:   seconds spent receiving those bytes
Result = collections.namedtuple(
    "Result", "received total headers xfer_bytes xfer_secs"
)


class BodyTruncated(Exception):
    """The server closed the connection before the whole body arrived."""


# Everything a request can fail with. HTTPError is a URLError, a timeout is an
# OSError, a body cut off in chunked mode is an IncompleteRead (HTTPException).
FETCH_ERRORS = (
    urllib.error.URLError,
    http.client.HTTPException,
    BodyTruncated,
    OSError,
    ValueError,
)


def fetch(url, timeout):
    """Do one GET and read the whole body."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        headers_at = time.perf_counter()
        received = len(resp.read(CHUNK))
        mark = time.perf_counter()
        xfer_bytes = 0
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            xfer_bytes += len(chunk)
        end = time.perf_counter()
        declared = resp.headers.get("Content-Length")
        chunked = "chunked" in (resp.headers.get("Transfer-Encoding") or "").lower()
    received += xfer_bytes
    # http.client does not complain when a body with a Content-Length is cut
    # short: read() just returns less. Without this check a dropped connection
    # would be counted as a good, smaller download.
    if declared and declared.strip().isdigit() and not chunked:
        if received != int(declared):
            raise BodyTruncated(
                "connection closed early: got %d of %s bytes" % (received, declared.strip())
            )
    xfer_secs = end - mark
    if xfer_secs < MIN_TRANSFER_SECS:
        xfer_bytes, xfer_secs = 0, 0.0  # too short to time, see MIN_TRANSFER_SECS
    return Result(
        received=received,
        total=end - start,
        headers=headers_at - start,
        xfer_bytes=xfer_bytes,
        xfer_secs=xfer_secs,
    )


def describe(exc):
    """Short human text for a failed request."""
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP %d %s" % (exc.code, exc.reason)
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    return str(exc) or exc.__class__.__name__


def format_speed(nbytes, secs):
    """Speed as 'X MB/s (Y Mbit/s)', or 'n/a' when it cannot be measured."""
    if nbytes <= 0 or secs <= 0:
        return "n/a (body too small or too fast to measure)"
    bytes_per_sec = nbytes / secs
    return "%.3f MB/s (%.3f Mbit/s)" % (
        bytes_per_sec / 1_000_000,
        bytes_per_sec * 8 / 1_000_000,
    )


def to_ascii_url(url):
    """Encode non-ASCII characters the way a browser does.

    Non-ASCII characters in the path, query and fragment become %XX (UTF-8),
    a space becomes %20, a non-ASCII host name becomes its xn-- form (IDNA).
    An address without such characters is returned unchanged. Raises
    UnicodeError if the host name cannot be converted.
    """
    if url.isascii() and " " not in url:
        return url
    head, slashes, rest = url.partition("//")  # "http:", "//", "host/path?query"
    ends = [i for i in (rest.find(c) for c in "/?#") if i >= 0]
    cut = min(ends) if ends else len(rest)
    host_part, tail = rest[:cut], rest[cut:]
    if not host_part.isascii():
        host, colon, port = host_part.partition(":")
        host_part = host.encode("idna").decode("ascii") + colon + port
    tail = "".join(
        urllib.parse.quote(c) if c == " " or not c.isascii() else c for c in tail
    )
    return head + slashes + host_part + tail


def url_problem(url):
    """Why the address cannot be used, or None if it is fine."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "url is not a valid address"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "url must be an http:// or https:// address with a host"
    if "@" in parts.netloc:
        return "credentials in the address (user:password@) are not supported"
    try:
        port = parts.port
    except ValueError:
        port = -1
    if port is not None and not 1 <= port <= 65535:
        return "port must be a number from 1 to 65535"
    try:
        to_ascii_url(url)
    except UnicodeError:
        return "host name cannot be converted to ASCII (internationalized name is not valid)"
    return None


def is_http_url(url):
    """True for an address the script can use."""
    return url_problem(url) is None


def run(url, count, timeout, out=None):
    """Make `count` requests, print every one and a summary.

    A failed request does not stop the run. The summary covers the requests
    that succeeded. Ctrl-C stops the run and still prints the summary of what
    finished. Returns the exit code: 0 if all succeeded, 1 if any failed,
    130 if interrupted.
    """
    out = out if out is not None else sys.stdout
    url = to_ascii_url(url)
    results = []
    failed = 0
    interrupted = False

    try:
        for i in range(1, count + 1):
            started = time.perf_counter()
            try:
                r = fetch(url, timeout)
            except FETCH_ERRORS as e:
                failed += 1
                print(
                    "request %2d: FAILED after %.3f s: %s"
                    % (i, time.perf_counter() - started, describe(e)),
                    file=out,
                    flush=True,
                )
                continue
            results.append(r)
            print(
                "request %2d: %9d bytes in %.3f s (headers after %.3f s)"
                % (i, r.received, r.total, r.headers),
                file=out,
                flush=True,
            )
    except KeyboardInterrupt:
        # Ctrl-C can arrive anywhere in the loop, not only inside fetch(), so
        # the whole loop is covered. The request that was running is dropped.
        interrupted = True

    if interrupted:
        code = EXIT_INTERRUPTED
    else:
        code = EXIT_FAILED if failed else EXIT_OK

    print(file=out)
    print("requests:         %d ok, %d failed" % (len(results), failed), file=out)
    if interrupted:
        print(
            "interrupted:      stopped by Ctrl-C after %d of %d requests"
            % (len(results) + failed, count),
            file=out,
        )
    if not results:
        print("no successful requests, nothing to measure", file=out)
        return code

    times = [r.total for r in results]
    total_bytes = sum(r.received for r in results)
    total_time = sum(times)
    print("average time:     %.3f s" % (total_time / len(results)), file=out)
    print("median time:      %.3f s" % statistics.median(times), file=out)
    print("min / max time:   %.3f s / %.3f s" % (min(times), max(times)), file=out)
    print(
        "avg to headers:   %.3f s" % (sum(r.headers for r in results) / len(results)),
        file=out,
    )
    print(
        "total data:       %d bytes (%.3f MB)" % (total_bytes, total_bytes / 1_000_000),
        file=out,
    )
    print("speed, overall:   %s" % format_speed(total_bytes, total_time), file=out)
    print(
        "speed, transfer:  %s"
        % format_speed(
            sum(r.xfer_bytes for r in results), sum(r.xfer_secs for r in results)
        ),
        file=out,
    )
    return code


def main():
    parser = argparse.ArgumentParser(
        description="Send N sequential GET requests to a URL and print download stats."
    )
    parser.add_argument("url", help="http:// or https:// address, e.g. an image URL")
    parser.add_argument(
        "-n", "--count", type=int, default=10, help="number of requests (default: 10)"
    )
    parser.add_argument(
        "-t", "--timeout", type=float, default=30.0,
        help="per-request socket timeout in seconds (default: 30)",
    )
    args = parser.parse_args()

    problem = url_problem(args.url)
    if problem:
        parser.error(problem)
    if args.count < 1:
        parser.error("--count must be >= 1")
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")

    try:
        code = run(args.url, args.count, args.timeout)
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader closed the pipe, for example `bench.py ... | head -1`.
        # Point stdout at /dev/null so the flush at exit does not complain.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        code = EXIT_PIPE_CLOSED
    sys.exit(code)


if __name__ == "__main__":
    main()
