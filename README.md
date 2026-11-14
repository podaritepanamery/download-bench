# download-bench

Small console script. Sends N GET requests to one URL, one after another, waits for the full response each time, then prints average request time, total downloaded data and download speed.

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

Example output:

```
request  1:     15770 bytes in 0.416 s
request  2:     15770 bytes in 0.243 s
...
request 10:     15770 bytes in 0.241 s

requests:      10
average time:  0.256 s
total data:    157700 bytes (0.158 MB)
speed:         0.062 MB/s (0.494 Mbit/s)
```

If any request fails (HTTP error, DNS, timeout, bad URL), script prints the reason and exits with code 1.

## Units

- Time: seconds (`s`).
- Data: bytes. Also shown as MB, where 1 MB = 1 000 000 bytes (decimal, not MiB).
- Speed: **MB/s** (megabytes per second, 1 MB = 1 000 000 bytes). Same value also shown in **Mbit/s** (megabits per second, 1 byte = 8 bits), the unit internet providers use.

## How it is measured

- Timer per request starts before the connection is opened and stops after the last byte of the body is read. So it includes DNS, TCP connect, TLS handshake and waiting for the server, not only raw transfer.
- Average time = sum of all request times / number of requests.
- Total data = sum of body bytes received (headers not counted).
- Speed = total data / sum of all request times. Not an average of per-request speeds, so big and small responses weigh correctly.
- Requests are strictly sequential. No keep-alive reuse between them: each request opens a new connection.
- Request sends `Cache-Control: no-cache` and no `Accept-Encoding`, so server sends the raw body, not compressed.

### Small files give low speed

Connection setup (DNS, TCP, TLS) is counted in the time. For a small file it is most of the time, so the speed looks low. The 15 KB logo in the example gives about 0.06 MB/s, which says little about the connection's real capacity. To measure bandwidth, request a file of several MB.

## Test with local file

```bash
python3 -m http.server 8000 --directory /path/to/folder
python3 bench.py http://127.0.0.1:8000/photo.jpg
```

Local loopback speed is very high and says nothing about the real network. Data never leaves the computer, so the number only shows how fast this machine copies memory.

Measured here: a 5 MB file, 10 requests, gave 1040 to 1520 MB/s (about 8 300 to 12 200 Mbit/s) across runs and Python versions. No real network gives that. Use local mode only to check that the script works.
