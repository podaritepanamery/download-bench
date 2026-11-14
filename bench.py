#!/usr/bin/env python3
"""Download a URL N times in a row and report average time, total size and speed."""

import argparse
import collections
import http.client
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
USER_AGENT = "download-bench/1.2"

# received:    all body bytes
# total:       seconds from the start of the request to the last body byte
# headers:     seconds from the start of the request to the response headers
# xfer_bytes:  body bytes counted for the transfer speed (all but the first chunk)
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
    return Result(
        received=received,
        total=end - start,
        headers=headers_at - start,
        xfer_bytes=xfer_bytes,
        xfer_secs=end - mark,
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
        return "n/a (body too small to measure)"
    bytes_per_sec = nbytes / secs
    return "%.3f MB/s (%.3f Mbit/s)" % (
        bytes_per_sec / 1_000_000,
        bytes_per_sec * 8 / 1_000_000,
    )


def is_http_url(url):
    """True for http:// and https:// addresses that have a host."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def run(url, count, timeout, out=None):
    """Make `count` requests, print every one and a summary.

    A failed request does not stop the run. The summary covers the requests
    that succeeded. Returns the exit code: 0 if all succeeded, 1 otherwise.
    """
    out = out if out is not None else sys.stdout
    results = []
    failed = 0

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
            )
            continue
        results.append(r)
        print(
            "request %2d: %9d bytes in %.3f s (headers after %.3f s)"
            % (i, r.received, r.total, r.headers),
            file=out,
        )

    print(file=out)
    print("requests:         %d ok, %d failed" % (len(results), failed), file=out)
    if not results:
        print("no successful requests, nothing to measure", file=out)
        return 1

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
    return 1 if failed else 0


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

    if not is_http_url(args.url):
        parser.error("url must be an http:// or https:// address with a host")
    if args.count < 1:
        parser.error("--count must be >= 1")
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")

    sys.exit(run(args.url, args.count, args.timeout))


if __name__ == "__main__":
    main()
