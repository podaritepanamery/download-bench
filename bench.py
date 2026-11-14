#!/usr/bin/env python3
"""Download a URL N times in a row and report average time, total size and speed."""

import argparse
import collections
import sys
import time
import urllib.error
import urllib.request

# Body is read in chunks of this size. The first chunk of every response is
# not counted in the transfer speed: part of it may already sit in the
# socket buffer when the headers arrive, which would make the speed look
# too high. Bodies of this size or smaller have no transfer speed.
CHUNK = 16 * 1024
USER_AGENT = "download-bench/1.1"

# received:    all body bytes
# total:       seconds from the start of the request to the last body byte
# headers:     seconds from the start of the request to the response headers
# xfer_bytes:  body bytes counted for the transfer speed (all but the first chunk)
# xfer_secs:   seconds spent receiving those bytes
Result = collections.namedtuple(
    "Result", "received total headers xfer_bytes xfer_secs"
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
    return Result(
        received=received + xfer_bytes,
        total=end - start,
        headers=headers_at - start,
        xfer_bytes=xfer_bytes,
        xfer_secs=end - mark,
    )


def format_speed(nbytes, secs):
    """Speed as 'X MB/s (Y Mbit/s)', or 'n/a' when it cannot be measured."""
    if nbytes <= 0 or secs <= 0:
        return "n/a (body too small to measure)"
    bytes_per_sec = nbytes / secs
    return "%.3f MB/s (%.3f Mbit/s)" % (
        bytes_per_sec / 1_000_000,
        bytes_per_sec * 8 / 1_000_000,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Send N sequential GET requests to a URL and print download stats."
    )
    parser.add_argument("url", help="address to request, e.g. an image URL")
    parser.add_argument(
        "-n", "--count", type=int, default=10, help="number of requests (default: 10)"
    )
    parser.add_argument(
        "-t", "--timeout", type=float, default=30.0,
        help="per-request socket timeout in seconds (default: 30)",
    )
    args = parser.parse_args()

    if args.count < 1:
        parser.error("--count must be >= 1")

    total_bytes = 0
    total_time = 0.0
    headers_time = 0.0
    xfer_bytes = 0
    xfer_time = 0.0

    for i in range(1, args.count + 1):
        try:
            r = fetch(args.url, args.timeout)
        except urllib.error.HTTPError as e:
            sys.exit("request %d failed: HTTP %d %s" % (i, e.code, e.reason))
        except (urllib.error.URLError, OSError, ValueError) as e:
            sys.exit("request %d failed: %s" % (i, e))
        total_bytes += r.received
        total_time += r.total
        headers_time += r.headers
        xfer_bytes += r.xfer_bytes
        xfer_time += r.xfer_secs
        print(
            "request %2d: %9d bytes in %.3f s (headers after %.3f s)"
            % (i, r.received, r.total, r.headers)
        )

    print()
    print("requests:         %d" % args.count)
    print("average time:     %.3f s" % (total_time / args.count))
    print("avg to headers:   %.3f s" % (headers_time / args.count))
    print("total data:       %d bytes (%.3f MB)" % (total_bytes, total_bytes / 1_000_000))
    print("speed, overall:   %s" % format_speed(total_bytes, total_time))
    print("speed, transfer:  %s" % format_speed(xfer_bytes, xfer_time))


if __name__ == "__main__":
    main()
