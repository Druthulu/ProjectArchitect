import unittest
from datetime import date

from shelf.dates import parse_date, parse_date_range

D = date(2024, 3, 4)


class HiddenC09(unittest.TestCase):
    def test_forms(self):
        for text in ("2024-03-04", " 2024-3-4 ", "2024/03/04", "2024/3/4", "04.03.2024", "4.3.2024", "20240304"):
            self.assertEqual(parse_date(text), D, text)

    def test_rejects(self):
        for text in ("2024-02-30", "2023-02-29", "24-03-04", "2024.03.04", "04/03/2024", "2024-03-04T00:00",
                     "", "2024-003-04", "202403041", "2024034", "4.3.24", "yesterday"):
            with self.assertRaises(ValueError, msg=text):
                parse_date(text)

    def test_leap_day(self):
        self.assertEqual(parse_date("29.02.2024"), date(2024, 2, 29))

    def test_range(self):
        self.assertEqual(parse_date_range("2024-03-04..2024/03/10"), (D, date(2024, 3, 10)))
        self.assertEqual(parse_date_range(" 04.03.2024 .. 20240304 "), (D, D))

    def test_range_rejects(self):
        for text in ("2024-03-10..2024-03-04", "2024-03-04", "2024-03-04..", "..2024-03-04", "2024-03-04 - 2024-03-05"):
            with self.assertRaises(ValueError, msg=text):
                parse_date_range(text)


if __name__ == "__main__":
    unittest.main()
