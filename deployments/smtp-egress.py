"""TCP relay for hosts where only Docker's default bridge has internet egress.

Same idea as pg-egress.py: Plane's api/worker containers sit on a private bridge
with no outbound route, so SMTP is relayed through this container, which is
attached to the default bridge (egress) and to the Plane network (reachable).

TLS is passed straight through, so EMAIL_USE_SSL=1 keeps working.
"""
import contextlib
import os
import socket
import socketserver
import threading

# Bound to the relay's address on the private Plane bridge, so nothing is exposed
# on the egress side. Passed in at start-up (see deployments/README note in compose).
UPSTREAM_HOST = os.environ.get("SMTP_RELAY_HOST", "smtp.qiye.163.com")
UPSTREAM_PORT = int(os.environ.get("SMTP_RELAY_PORT", "465"))
LISTEN_IFACE = os.environ["SMTP_RELAY_LISTEN_HOST"]
LISTEN_PORT = int(os.environ.get("SMTP_RELAY_LISTEN_PORT", "465"))


def pipe(src: socket.socket, dst: socket.socket) -> None:
    with contextlib.suppress(OSError):
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    for sock in (src, dst):
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)


class Relay(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            upstream = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=15)
        except (OSError, TimeoutError) as error:
            print(f"Relay connection failed: {error}", flush=True)
            return
        upstream.settimeout(None)
        worker = threading.Thread(target=pipe, args=(upstream, self.request), daemon=True)
        worker.start()
        pipe(self.request, upstream)
        worker.join()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


with Server((LISTEN_IFACE, LISTEN_PORT), Relay) as server:
    server.serve_forever()
