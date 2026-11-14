# download-bench

Small console script. Sends N GET requests to one URL, one after another, waits for the full response each time, then prints average, median, minimum and maximum request time, total downloaded data and download speed (two ways: overall and transfer only).

A request that fails does not stop the run. The script goes to the end and tells how many requests passed.

Pure Python standard library. No packages to install.

## Requirements

Python 3.8 or newer.

Script and test suite were run on Python 3.8.20, 3.9.6, 3.12.15 and 3.13.16 (macOS, arm64). Older than 3.8 not tried.

## Run

```bash
python3 bench.py https://www.python.org/static/img/python-logo.png
```

Options:

| Option | Default | Meaning |
| --- | --- | --- |
| `url` | required | `http://` or `https://` address (image, file, any page). Cyrillic and other non-ASCII letters are fine |
| `-n`, `--count` | `10` | number of requests |
| `-t`, `--timeout` | `30` | seconds to wait for the server on each network step (connect, next piece of data), not a limit for the whole request |

Exit code:

| Code | Meaning |
| --- | --- |
| `0` | every request succeeded |
| `1` | at least one request failed |
| `2` | wrong arguments: address is not `http://` or `https://`, port outside 1 to 65535, `user:password@` in the address, `--count` below 1, `--timeout` not above 0 |
| `130` | stopped with Ctrl-C |
| `141` | the reader of the output closed the pipe (`... \| head -1`) |

Example output (15 KB image):

```
request  1:     15770 bytes in 0.224 s (headers after 0.221 s)
request  2:     15770 bytes in 0.285 s (headers after 0.275 s)
...
request 10:     15770 bytes in 0.234 s (headers after 0.231 s)

requests:         10 ok, 0 failed
average time:     0.232 s
median time:      0.229 s
min / max time:   0.152 s / 0.302 s
avg to headers:   0.219 s
total data:       157700 bytes (0.158 MB)
speed, overall:   0.068 MB/s (0.545 Mbit/s)
speed, transfer:  n/a (body too small or too fast to measure)
```

Example output (87 KB file):

```
python3 bench.py https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js

request  1:     87533 bytes in 0.350 s (headers after 0.250 s)
request  2:     87533 bytes in 0.165 s (headers after 0.126 s)
...
request 10:     87533 bytes in 0.231 s (headers after 0.195 s)

requests:         10 ok, 0 failed
average time:     0.235 s
median time:      0.238 s
min / max time:   0.158 s / 0.350 s
avg to headers:   0.185 s
total data:       875330 bytes (0.875 MB)
speed, overall:   0.373 MB/s (2.984 Mbit/s)
speed, transfer:  1.440 MB/s (11.519 Mbit/s)
```

## When a request fails

A failed request is printed as `FAILED` with the time it took and the reason. The run goes on with the next request. These count as failures:

- HTTP error status (404, 500 and so on)
- address not found, connection refused, TLS error
- timeout, also when the server goes silent in the middle of the body
- connection closed before the whole body arrived (body shorter than `Content-Length`, or a chunked body cut off)

Time and data of failed requests are not counted. All numbers in the summary come from the requests that succeeded, and the exit code is `1`.

Example, local test server that answers the 2nd request with an error (4 requests):

```
request  1:    100000 bytes in 0.012 s (headers after 0.012 s)
request  2: FAILED after 0.001 s: HTTP 500 Internal Server Error
request  3:    100000 bytes in 0.001 s (headers after 0.000 s)
request  4:    100000 bytes in 0.000 s (headers after 0.000 s)

requests:         3 ok, 1 failed
average time:     0.004 s
...
```

If every request fails there is nothing to measure, so no statistics are printed:

```
python3 bench.py -n 3 https://www.python.org/static/img/nope.png

request  1: FAILED after 0.482 s: HTTP 404 Not Found
request  2: FAILED after 0.629 s: HTTP 404 Not Found
request  3: FAILED after 0.597 s: HTTP 404 Not Found

requests:         0 ok, 3 failed
no successful requests, nothing to measure
```

## Ctrl-C and closed pipe

- **Ctrl-C** stops the run. The request that was running is dropped. The summary of the requests that had finished is still printed, with a line `interrupted: stopped by Ctrl-C after N of M requests`. Exit code `130`.
- **Closed output pipe**, for example `python3 bench.py URL | head -1`: the script stops quietly, without a traceback. Exit code `141`.

Each request line is printed as soon as that request ends, so a pipe or a file shows progress at once.

## Units

- Time: seconds (`s`).
- Data: bytes. Also shown as MB, where 1 MB = 1 000 000 bytes (decimal, not MiB).
- Speed: **MB/s** (megabytes per second, 1 MB = 1 000 000 bytes). Same value also shown in **Mbit/s** (megabits per second, 1 byte = 8 bits), the unit internet providers use.

## Time spread

The average alone hides slow outliers, so the summary also shows the median (the middle value: half of the requests were faster, half slower) and the fastest and slowest request. In the 15 KB example the average is 0.232 s, but single requests took from 0.152 s to 0.302 s.

## Two speeds

The script prints two download speeds, because one number cannot answer both questions.

| Line | Formula | What it shows |
| --- | --- | --- |
| `speed, overall` | total data / total time of all requests | what you wait for: connection setup, server wait and download together |
| `speed, transfer` | data / time counted after the first 16 KB of each response, only for requests where that part took at least 1 ms | the download itself, without connection setup and server wait |

In the 87 KB example above, overall is 0.373 MB/s but transfer is 1.440 MB/s. Most of the time went to connecting and waiting for the server (`avg to headers` is 0.185 s of 0.235 s), not to downloading.

Why the first 16 KB are skipped in `transfer`: when the headers arrive, the first part of the body may already be in the socket buffer. Counting those bytes with no time attached would make the speed too high. Skipping the first chunk avoids that. Because of it:

- A body of 16 KB or less has no transfer speed, because nothing is left after the first chunk. The 15 KB image in the first example is such a case.
- A request also counts only if the part after the first 16 KB took at least 1 ms. A shorter interval is mostly timer and Python overhead, and it gave wrong numbers: on a local test server that delivers about 2000 MB/s, a 16 385-byte body printed 0.056 MB/s and a 20 000-byte body printed 327 MB/s. Such requests are left out of `speed, transfer` (they still count in `speed, overall`). If no request qualifies, the line says `n/a`.
- The 87 KB example qualifies: about 71 KB are measured, which takes some 50 ms per request. On the local test server the same file would not, because it downloads in well under 1 ms. To measure bandwidth, request a file of several MB.

Check against curl: on a local test server (0.3 s delay before headers, then 2 MB throttled to about 1.7 MB/s), curl body speed was 1.65 to 1.69 MB/s and `speed, transfer` was 1.63 to 1.67 MB/s over several runs and Python versions, while `speed, overall` was about 1.3 MB/s.

## How it is measured

- Timer per request starts before the connection is opened. `headers after` is the time until the response headers arrive: DNS, TCP connect, TLS handshake and server wait. Total time stops after the last byte of the body is read.
- Average time = sum of request times / number of successful requests. Median, minimum and maximum are taken over the same requests. `avg to headers` is the average of the time until headers.
- Total data = sum of body bytes received (headers not counted).
- Address: non-ASCII letters in the path and query are sent percent-encoded (UTF-8), a space becomes `%20`, and a non-ASCII host name is converted to its `xn--` form, the way a browser does it. `user:password@` in the address is not supported. The port must be 1 to 65535.
- Requests are strictly sequential. No keep-alive reuse between them: each request opens a new connection.
- Request sends `Cache-Control: no-cache` and no `Accept-Encoding`, so server sends the raw body, not compressed.
- Python's `http.client` does not report a body that ends before `Content-Length`. The script compares the received size with `Content-Length` itself, so a dropped connection is a failure and not a smaller download.

## Tests

Tests use [pytest](https://pytest.org). They start a local HTTP server that fails on purpose, so no internet is needed. Run time is about 7 seconds.

```bash
python3 -m pip install pytest
python3 -m pytest
```

What is covered:

- normal response, small body without transfer speed, chunked body, body without `Content-Length`
- default of 10 requests, requests sent strictly one at a time, script waits for the whole body
- body sizes at and around the 16 KB chunk edge (0, 1, 16 383, 16 384, 16 385 bytes and more)
- redirect followed, redirect loop, `localhost`, IPv6 address, 200 requests in a row, no files written
- 404, a request that fails once in the middle, connection refused
- timeout while waiting for headers and in the middle of the body, and that the timeout limits each wait, not the whole request
- connection dropped mid-body, chunked body cut off
- transfer time leaves out the wait for headers, first 16 KB are not counted in transfer data, the 1 ms limit (tested with a fake clock)
- Ctrl-C (summary of what finished, exit code 130, no traceback) and a closed output pipe (exit code 141, no traceback)
- address checks and encoding: bad port, `user:password@`, Cyrillic path and host name, spaces
- median, minimum, maximum, average over successful requests only, exit codes
- bad arguments (not an `http://` or `https://` address, `--count 0`, `--timeout 0`)

## Test with local file

```bash
python3 -m http.server 8000 --directory /path/to/folder
python3 bench.py http://127.0.0.1:8000/photo.jpg
```

Local loopback speed is very high and says nothing about the real network. Data never leaves the computer, so the number only shows how fast this machine copies memory.

Measured here: a 5 MB file, 10 requests, gave 910 to 1510 MB/s overall and 1700 to 2350 MB/s transfer across runs and Python versions. No real network gives that. Use local mode only to check that the script works. A 1 MB or 2 MB file downloads locally in under 1 ms after the first chunk, so `speed, transfer` shows `n/a` there; a 5 MB file shows it.
