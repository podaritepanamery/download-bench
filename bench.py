#!/usr/bin/env python3
"""Download a URL N times in a row and report average time, total size and speed."""

import argparse
import sys
import time
import urllib.error
import urllib.request

CHUNK = 64 * 1024
USER_AGENT = "download-bench/1.0"


def fetch(url, timeout):
    """Do one GET, read the whole body. Return (bytes_received, seconds)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    start = time.perf_counter()
    received = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            received += len(chunk)
    return received, time.perf_counter() - start


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

    for i in range(1, args.count + 1):
        try:
            size, secs = fetch(args.url, args.timeout)
        except urllib.error.HTTPError as e:
            sys.exit("request %d failed: HTTP %d %s" % (i, e.code, e.reason))
        except (urllib.error.URLError, OSError, ValueError) as e:
            sys.exit("request %d failed: %s" % (i, e))
        total_bytes += size
        total_time += secs
        print("request %2d: %9d bytes in %.3f s" % (i, size, secs))

    avg_time = total_time / args.count
    bytes_per_sec = total_bytes / total_time if total_time > 0 else 0.0
    mb_per_sec = bytes_per_sec / 1_000_000
    mbit_per_sec = bytes_per_sec * 8 / 1_000_000

    print()
    print("requests:      %d" % args.count)
    print("average time:  %.3f s" % avg_time)
    print("total data:    %d bytes (%.3f MB)" % (total_bytes, total_bytes / 1_000_000))
    print("speed:         %.3f MB/s (%.3f Mbit/s)" % (mb_per_sec, mbit_per_sec))


if __name__ == "__main__":
    main()
