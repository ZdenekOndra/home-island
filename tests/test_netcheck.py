import socket
import struct
import threading
import unittest

import helpers  # noqa: F401

from homeisland import netcheck


class FakeDNS(threading.Thread):
    """Answers every A query with 192.0.2.1 (or NXDOMAIN for names starting with 'missing')."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]

    def run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:
                return
            qid = data[:2]
            question = data[12:]
            name_end = question.index(b"\x00") + 5
            q = question[:name_end]
            if question[1:8] == b"missing":
                header = qid + struct.pack(">HHHHH", 0x8183, 1, 0, 0, 0)
                self.sock.sendto(header + q, addr)
                continue
            header = qid + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0)
            answer = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton("192.0.2.1")
            self.sock.sendto(header + q + answer, addr)


class NetcheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dns = FakeDNS()
        cls.dns.start()

    def test_dns_answer(self):
        self.assertEqual(netcheck.dns_query("127.0.0.1", "wiki.home.arpa", port=self.dns.port),
                         ("NOERROR", ["192.0.2.1"]))

    def test_dns_nxdomain(self):
        self.assertEqual(netcheck.dns_query("127.0.0.1", "missing.home.arpa", port=self.dns.port),
                         ("NXDOMAIN", []))

    def test_dns_timeout(self):
        silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        silent.bind(("127.0.0.1", 0))
        try:
            rcode, _ = netcheck.dns_query("127.0.0.1", "x.home.arpa", port=silent.getsockname()[1], timeout=0.3)
        finally:
            silent.close()
        self.assertEqual(rcode, "TIMEOUT")

    def test_parse_targets(self):
        self.assertEqual(netcheck.parse_targets("1.1.1.1:53, 9.9.9.9:53 example.org"),
                         [("1.1.1.1", 53), ("9.9.9.9", 53), ("example.org", 443)])

    def test_wan_check_offline(self):
        # A closed local port refuses immediately: nothing reachable means offline.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        result = netcheck.wan_check([("127.0.0.1", port)], timeout=0.5)
        self.assertEqual(result["state"], "offline")

    def test_wan_check_online_if_any(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        try:
            closed = socket.socket()
            closed.bind(("127.0.0.1", 0))
            closed_port = closed.getsockname()[1]
            closed.close()
            result = netcheck.wan_check([("127.0.0.1", closed_port), ("127.0.0.1", server.getsockname()[1])],
                                        timeout=0.5)
        finally:
            server.close()
        self.assertEqual(result["state"], "online")
        self.assertEqual(result["reachable"], 1)


if __name__ == "__main__":
    unittest.main()
