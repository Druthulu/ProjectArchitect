import unittest
from datetime import date

from shelf.holds import HoldQueue

D = date(2024, 3, 1)


def day(n):
    return date(2024, 3, n)


class HiddenC08(unittest.TestCase):
    def setUp(self):
        self.q = HoldQueue()
        self.q.place("B", "M1", day(1))
        self.q.place("B", "M2", day(6))
        self.q.place("A", "M3", day(2))
        self.q.place("B", "M4", day(21))
        self.q.place("C", "M1", day(2))

    def test_position(self):
        self.assertEqual(self.q.position("B", "M1"), 1)
        self.assertEqual(self.q.position("B", "M4"), 3)
        self.assertIsNone(self.q.position("B", "M9"))
        self.assertIsNone(self.q.position("Z", "M1"))

    def test_expire(self):
        removed = self.q.expire(day(15), 10)
        self.assertEqual([(h.isbn, h.member_id) for h in removed], [("B", "M1"), ("A", "M3"), ("C", "M1")])
        self.assertEqual(self.q.isbns(), ["B"])
        self.assertEqual([h.member_id for h in self.q.queue("B")], ["M2", "M4"])
        self.assertEqual(self.q.position("B", "M2"), 1)

    def test_expire_boundary(self):
        self.assertEqual(self.q.expire(day(12), 11), [])
        self.assertEqual(len(self.q.expire(day(13), 11)), 1)

    def test_isbns_after_cancel_and_pop(self):
        self.assertEqual(self.q.isbns(), ["A", "B", "C"])
        self.q.cancel("A", "M3")
        self.q.pop("C")
        self.assertEqual(self.q.isbns(), ["B"])


if __name__ == "__main__":
    unittest.main()
