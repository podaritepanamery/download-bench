# download-bench

Small console script. Sends N GET requests to one URL, one after another, waits for the full response each time, then prints average request time, total downloaded data and download speed (two ways: overall and transfer only).

Pure Python standard library. No packages to install.

## Requirements

Python 3.8 or newer.

Checked by running the script on Python 3.8.20, 3.9.6, 3.12.15 and 3.13.16 (macOS, arm64): normal run, HTTP 404 and bad URL all behave the same. Older than 3.8 not tried.

## Run

```bash
python3 bench.py https://www.python.org/static/img/python-logo.png
```

Options:

| Option | Default | Meaning |
| --- | --- | --- |
| `url` | required | address to request (image, file, any page) |
| `-n`, `--count` | `10` | number of requests |
| `-t`, `--timeout` | `30` | socket timeout per request, seconds |

Example output (15 KB image):

```
request  1:     15770 bytes in 0.163 s (headers after 0.162 s)
request  2:     15770 bytes in 0.147 s (headers after 0.145 s)
...
request 10:     15770 bytes in 0.150 s (headers after 0.148 s)

requests:         10
average time:     0.281 s
avg to headers:   0.275 s
total data:       157700 bytes (0.158 MB)
speed, overall:   0.056 MB/s (0.449 Mbit/s)
speed, transfer:  n/a (body too small to measure)
```

Example output (87 KB file):

```
python3 bench.py https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js

request  1:     87533 bytes in 0.190 s (headers after 0.156 s)
request  2:     87533 bytes in 0.220 s (headers after 0.183 s)
...
request 10:     87533 bytes in 0.242 s (headers after 0.121 s)

requests:         10
average time:     0.198 s
avg to headers:   0.153 s
total data:       875330 bytes (0.875 MB)
speed, overall:   0.442 MB/s (3.535 Mbit/s)
speed, transfer:  1.590 MB/s (12.721 Mbit/s)
```

If any request fails (HTTP error, DNS, timeout, bad URL), script prints the reason and exits with code 1.

## Units

- Time: seconds (`s`).
- Data: bytes. Also shown as MB, where 1 MB = 1 000 000 bytes (decimal, not MiB).
- Speed: **MB/s** (megabytes per second, 1 MB = 1 000 000 bytes). Same value also shown in **Mbit/s** (megabits per second, 1 byte = 8 bits), the unit internet providers use.

## Two speeds

The script prints two download speeds, because one number cannot answer both questions.

| Line | Formula | What it shows |
| --- | --- | --- |
| `speed, overall` | total data / total time of all requests | what you wait for: connection setup, server wait and download together |
| `speed, transfer` | data / time counted after the first 16 KB of each response | the download itself, without connection setup and server wait |

In the 87 KB example above, overall is 0.442 MB/s but transfer is 1.590 MB/s. Most of the time went to connecting and waiting for the server (`avg to headers` is 0.153 s of 0.198 s), not to downloading.

Why the first 16 KB are skipped in `transfer`: when the headers arrive, the first part of the body may already be in the socket buffer. Counting those bytes with no time attached would make the speed too high. Skipping the first chunk avoids that. Because of it:

- A body of 16 KB or less has no transfer speed. Script prints `n/a`. The 15 KB image in the first example is such a case.
- A body of a few tens of KB gives a rough value, because only a few milliseconds are measured. To measure bandwidth, request a file of several MB.

Check against curl: on a local test server (0.3 s delay before headers, then 2 MB throttled to about 1.7 MB/s), curl body speed was 1.65 to 1.69 MB/s and `speed, transfer` was 1.63 to 1.67 MB/s over several runs and Python versions, while `speed, overall` was about 1.3 MB/s.

## How it is measured

- Timer per request starts before the connection is opened. `headers after` is the time until the response headers arrive: DNS, TCP connect, TLS handshake and server wait. Total time stops after the last byte of the body is read.
- Average time = sum of all request times / number of requests. `avg to headers` is the same for the time until headers.
- Total data = sum of body bytes received (headers not counted).
- Requests are strictly sequential. No keep-alive reuse between them: each request opens a new connection.
- Request sends `Cache-Control: no-cache` and no `Accept-Encoding`, so server sends the raw body, not compressed.

## Test with local file

```bash
python3 -m http.server 8000 --directory /path/to/folder
python3 bench.py http://127.0.0.1:8000/photo.jpg
```

Local loopback speed is very high and says nothing about the real network. Data never leaves the computer, so the number only shows how fast this machine copies memory.

Measured here: a 5 MB file, 10 requests, gave 910 to 1510 MB/s overall and 1700 to 2350 MB/s transfer across runs and Python versions. No real network gives that. Use local mode only to check that the script works.
