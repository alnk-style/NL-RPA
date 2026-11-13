from __future__ import annotations

import socketserver
import threading


class _SmtpHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(b"220 fake-smtp ready\r\n")
        data_mode = False
        message: list[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line:
                break
            if data_mode:
                if line in {b".\r\n", b".\n"}:
                    self.server.messages.append(b"".join(message))
                    message = []
                    data_mode = False
                    self.wfile.write(b"250 queued\r\n")
                else:
                    message.append(line)
                continue
            command = line.decode("utf-8", errors="replace").strip()
            upper = command.upper()
            if upper.startswith("EHLO"):
                self.wfile.write(b"250-fake-smtp\r\n250 OK\r\n")
            elif upper.startswith("HELO"):
                self.wfile.write(b"250 OK\r\n")
            elif upper.startswith("MAIL FROM") or upper.startswith("RCPT TO"):
                self.server.envelopes.append(command)
                self.wfile.write(b"250 OK\r\n")
            elif upper == "DATA":
                data_mode = True
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
            elif upper == "QUIT":
                self.wfile.write(b"221 Bye\r\n")
                break
            else:
                self.wfile.write(b"250 OK\r\n")


class FakeSmtpServer:
    def __init__(self) -> None:
        self.server = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0),
            _SmtpHandler,
        )
        self.server.daemon_threads = True
        self.server.messages = []
        self.server.envelopes = []
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def messages(self) -> list[bytes]:
        return list(self.server.messages)

    @property
    def envelopes(self) -> list[str]:
        return list(self.server.envelopes)

    def __enter__(self) -> FakeSmtpServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
