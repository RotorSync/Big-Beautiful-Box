#!/usr/bin/env python3
"""A FAKE dashboard command socket for safe end-to-end testing of RotorLink's
command path — it speaks the :9999 line protocol (accept, recv one line(s),
reply, close) but actuates NOTHING. Every received line is appended to a file so
a test can assert exactly which dashboard commands RotorLink forwarded.

Usage:  python3 fake_dashboard.py <port> <linelog_path>
Reply rule: STATE_JSON -> "STATE_JSON:{}", anything else -> "OK".
"""
import socket
import sys

port = int(sys.argv[1])
linelog = sys.argv[2]

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port))
srv.listen(5)
print(f"fake dashboard on {port}", flush=True)

while True:
    try:
        client, _ = srv.accept()
        data = client.recv(4096).decode("utf-8").strip()
        for line in data.split("\n"):
            line = line.strip()
            if not line:
                continue
            with open(linelog, "a") as f:
                f.write(line + "\n")
            if line == "STATE_JSON":
                client.send(b"STATE_JSON:{}")
            else:
                client.send(b"OK\n")
        client.close()
    except Exception as e:
        print(f"fake dashboard error: {e}", flush=True)
