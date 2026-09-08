"""Unit tests for the run.py entry point (PRD AC1).

Covers port resolution (``PORT`` env override, default 8000) and the shared
cloud-station bind host: any station on the shop network must be able to
reach the server, so it binds ``0.0.0.0`` rather than localhost.
"""

import unittest

from run import DEFAULT_PORT, HOST, _parse_port, _reachable_host


class ParsePortTestCase(unittest.TestCase):
    """The app must default to port 8000 and honor a valid PORT override."""

    def test_blank_or_missing_env_falls_back_to_default(self):
        """No PORT (or blank) must yield the documented default of 8000."""
        self.assertEqual(_parse_port(None), DEFAULT_PORT)
        self.assertEqual(_parse_port(""), DEFAULT_PORT)
        self.assertEqual(_parse_port("   "), DEFAULT_PORT)

    def test_valid_custom_port_is_accepted(self):
        """A numeric PORT in range must be used as-is (whitespace trimmed)."""
        self.assertEqual(_parse_port("9000"), 9000)
        self.assertEqual(_parse_port(" 8080 "), 8080)
        self.assertEqual(_parse_port("1"), 1)
        self.assertEqual(_parse_port("65535"), 65535)

    def test_non_numeric_port_is_rejected(self):
        """A non-integer PORT must abort startup with a clear message."""
        with self.assertRaises(SystemExit):
            _parse_port("abc")
        with self.assertRaises(SystemExit):
            _parse_port("80x")

    def test_out_of_range_port_is_rejected(self):
        """PORT outside 1..65535 must abort startup with a clear message."""
        with self.assertRaises(SystemExit):
            _parse_port("0")
        with self.assertRaises(SystemExit):
            _parse_port("-1")
        with self.assertRaises(SystemExit):
            _parse_port("70000")


class BindHostTestCase(unittest.TestCase):
    """The server must be reachable from every station on the shop network."""

    def test_server_binds_all_interfaces(self):
        """AC1: the shared cloud station binds 0.0.0.0, not localhost."""
        self.assertEqual(HOST, "0.0.0.0")
        self.assertNotEqual(HOST, "127.0.0.1")

    def test_startup_message_uses_a_reachable_host(self):
        """The startup message prints a concrete host stations can reach."""
        host = _reachable_host()
        self.assertIsInstance(host, str)
        self.assertTrue(host.strip())


if __name__ == "__main__":
    unittest.main()
