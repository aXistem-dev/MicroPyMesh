"""Serve one file to the first GET and exit once all of it has been sent.

    python3 tools/serve_once.py FILE HOST PORT TIMEOUT_S

Prints "sent N" after the last byte went out (exit 0), or "timeout" (exit 1). A plain
http.server logs a request before the body is sent, so its log cannot tell when a slow
client has the whole file.
"""
import http.server
import os
import sys


def main():
    path, host, port, timeout = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    data = open(path, "rb").read()
    done = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/zip")
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
            done.append(len(data))

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer((host, port), Handler)
    server.timeout = 1
    left = timeout
    while not done and left > 0:
        server.handle_request()
        left -= 1
    server.server_close()
    if done:
        print("sent %d" % done[0])
        return 0
    print("timeout")
    return 1


if __name__ == "__main__":
    sys.exit(main())
