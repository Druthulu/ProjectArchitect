import json
import unittest

from helpers import DUNE, GOF, HOBBIT, ORWELL, library
from shelf import storage
from shelf.models import FeesOwed, InactiveMember, ShelfError


class HiddenC14(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()
        loan = self.desk.checkout(DUNE, "M0001")
        self.clock.advance(21 + 25)
        self.fee = self.desk.checkin(loan.loan_id)

    def test_balance_and_block(self):
        self.assertEqual(self.fee, 600)
        self.assertEqual(self.members.get("M0001").balance_cents, 600)
        self.assertTrue(issubclass(FeesOwed, ShelfError))
        with self.assertRaises(FeesOwed):
            self.desk.checkout(HOBBIT, "M0001")
        self.assertEqual(self.members.pay("M0001", 100), 500)
        with self.assertRaises(FeesOwed):
            self.desk.checkout(HOBBIT, "M0001")
        self.assertEqual(self.members.pay("M0001", 1), 499)
        self.desk.checkout(HOBBIT, "M0001")

    def test_bad_payments(self):
        for cents in (0, -5, 601):
            with self.assertRaises(ValueError):
                self.members.pay("M0001", cents)
        self.assertEqual(self.members.get("M0001").balance_cents, 600)

    def test_inactive_checked_first(self):
        self.members.deactivate("M0001")
        with self.assertRaises(InactiveMember):
            self.desk.checkout(HOBBIT, "M0001")

    def test_balances_accumulate(self):
        loan = self.desk.checkout(ORWELL, "M0002")
        self.clock.advance(14 + 6)
        self.desk.checkin(loan.loan_id)
        self.assertEqual(self.members.get("M0002").balance_cents, 50)
        self.desk.checkout(ORWELL, "M0002")

    def test_fees_checked_before_limit(self):
        for isbn in (HOBBIT, ORWELL, DUNE):
            self.desk.checkout(isbn, "M0002")                # student limit 3 reached
        self.members.get("M0002").balance_cents = 500
        with self.assertRaises(FeesOwed):
            self.desk.checkout(GOF, "M0002")

    def test_paid_in_full_and_loaded_desk_blocks(self):
        _, members2, desk2 = storage.loads(storage.dumps(self.catalog, self.members, self.desk), self.clock)
        with self.assertRaises(FeesOwed):
            desk2.checkout(HOBBIT, "M0001")
        self.assertEqual(members2.pay("M0001", 600), 0)
        desk2.checkout(HOBBIT, "M0001")
        self.assertEqual(self.members.get("M0001").balance_cents, 600)

    def test_storage(self):
        text = storage.dumps(self.catalog, self.members, self.desk)
        _, members2, _ = storage.loads(text)
        self.assertEqual(members2.get("M0001").balance_cents, 600)
        data = json.loads(text)
        for m in data["members"]:
            m.pop("balance_cents", None)
        _, members3, _ = storage.loads(json.dumps(data))
        self.assertEqual(members3.get("M0001").balance_cents, 0)


if __name__ == "__main__":
    unittest.main()
