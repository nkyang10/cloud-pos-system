"""Unit tests for the run.py entry point's port resolution (PRD AC1)."""

import unittest

from run import DEFAULT_PORT, _parse_port


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


if __name__ == "__main__":
    unittest.main()
