import os
import sys
import unittest
from types import SimpleNamespace

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.client_ip import client_ip  # noqa: E402


def req(xff=None, host="10.0.0.1"):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


class ClientIpTests(unittest.TestCase):
    def test_uses_the_last_forwarded_entry_the_proxy_appended(self):
        self.assertEqual(client_ip(req("1.2.3.4, 203.0.113.9")), "203.0.113.9")

    def test_a_forged_first_entry_is_ignored(self):
        self.assertEqual(client_ip(req("6.6.6.6,203.0.113.9")), client_ip(req("7.7.7.7, 203.0.113.9")))

    def test_falls_back_to_the_socket_address(self):
        self.assertEqual(client_ip(req()), "10.0.0.1")
        self.assertEqual(client_ip(req(" ")), "10.0.0.1")
        self.assertEqual(client_ip(SimpleNamespace(headers={}, client=None)), "unknown")


if __name__ == "__main__":
    unittest.main()
