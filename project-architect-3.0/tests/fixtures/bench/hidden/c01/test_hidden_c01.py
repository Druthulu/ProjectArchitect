import unittest

from shelf.isbn import is_valid_isbn10, is_valid_isbn13, isbn10_to_isbn13, isbn13_to_isbn10


def with_check13(body):
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


class HiddenC01(unittest.TestCase):
    def test_10_to_13(self):
        self.assertEqual(isbn10_to_isbn13("0-306-40615-2"), "9780306406157")
        self.assertEqual(isbn10_to_isbn13("0131103628"), "9780131103627")
        self.assertEqual(isbn10_to_isbn13(" 097522980x "), "9780975229804")
        self.assertTrue(is_valid_isbn13(isbn10_to_isbn13("0201633612")))

    def test_10_to_13_rejects(self):
        for bad in ("0306406153", "9780306406157", "", "03064O6152", "X306406152"):
            with self.assertRaises(ValueError, msg=bad):
                isbn10_to_isbn13(bad)

    def test_13_to_10(self):
        self.assertEqual(isbn13_to_isbn10("978-0-306-40615-7"), "0306406152")
        self.assertEqual(isbn13_to_isbn10("9780975229804"), "097522980X")
        self.assertTrue(is_valid_isbn10(isbn13_to_isbn10("9780547928227")))

    def test_13_to_10_rejects(self):
        for bad in ("9780306406158", with_check13("979123456789"), "0306406152", "97803064061"):
            with self.assertRaises(ValueError, msg=bad):
                isbn13_to_isbn10(bad)

    def test_round_trip(self):
        for isbn10 in ("0306406152", "097522980X", "0131103628", "0201633612"):
            self.assertEqual(isbn13_to_isbn10(isbn10_to_isbn13(isbn10)), isbn10)


if __name__ == "__main__":
    unittest.main()
