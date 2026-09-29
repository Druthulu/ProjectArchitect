import json
import unittest
from datetime import date

from helpers import FELLOWSHIP, HOBBIT, ORWELL, library
from shelf import storage


class HiddenC15(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()
        self.desk.checkout(ORWELL, "M0001")
        self.desk.place_hold(ORWELL, "M0003")
        self.clock.advance(1)
        self.desk.place_hold(ORWELL, "M0002")
        self.clock.advance(1)
        self.desk.place_hold(FELLOWSHIP, "M0001")
        # same day, queue order is not member-id order
        self.desk.place_hold(HOBBIT, "M0003")
        self.desk.place_hold(HOBBIT, "M0001")
        self.text = storage.dumps(self.catalog, self.members, self.desk)

    def queue(self, desk, isbn):
        return [(h.member_id, h.placed) for h in desk.holds.queue(isbn)]

    def test_round_trip(self):
        catalog, members, desk = storage.loads(self.text)
        self.assertEqual(self.queue(desk, ORWELL), [("M0003", date(2024, 3, 4)), ("M0002", date(2024, 3, 5))])
        self.assertEqual(self.queue(desk, FELLOWSHIP), [("M0001", date(2024, 3, 6))])
        self.assertEqual(self.queue(desk, HOBBIT), [("M0003", date(2024, 3, 6)), ("M0001", date(2024, 3, 6))])
        self.assertEqual(storage.dumps(catalog, members, desk), self.text)
        self.assertEqual(json.loads(self.text)["format"], 2)

    def test_loaded_queue_works(self):
        _, _, desk = storage.loads(self.text)
        self.assertEqual(desk.holds.pop(ORWELL).member_id, "M0003")
        self.assertEqual(desk.holds.pop(ORWELL).member_id, "M0002")
        self.assertIsNone(desk.holds.pop(ORWELL))
        with self.assertRaises(ValueError):
            desk.place_hold(HOBBIT, "M0001")

    def test_old_document(self):
        catalog, members, desk = library()[:3]
        desk.checkout(ORWELL, "M0001")
        old = json.loads(storage.dumps(catalog, members, desk))
        old.pop("holds", None)
        _, _, loaded = storage.loads(json.dumps(old))
        self.assertEqual(loaded.holds.all(), [])
        self.assertEqual(len(loaded.loans), 1)


if __name__ == "__main__":
    unittest.main()
