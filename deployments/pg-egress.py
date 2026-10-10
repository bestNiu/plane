"""TCP relay for hosts where only Docker's default bridge has RDS egress.

No credentials are handled here. Bind the published port only to the private
Plane bridge gateway. Remove this relay when direct bridge egress is available.
"""
import os
import selectors
import socket
import socketserver


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            upstream = socket.create_connection(
                (os.environ["PG_RELAY_HOST"], 5432), timeout=10
            )
            with upstream, selectors.DefaultSelector() as selector:
                upstream.settimeout(None)
                selector.register(self.request, selectors.EVENT_READ, upstream)
                selector.register(upstream, selectors.EVENT_READ, self.request)
                while True:
                    for key, _ in selector.select():
                        data = key.fileobj.recv(65536)
                        if not data:
                            return
                        key.data.sendall(data)
        except (OSError, TimeoutError) as error:
            print(f"Relay connection failed: {error}", flush=True)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


with Server(("0.0.0.0", 15432), Relay) as server:
    server.serve_forever()
