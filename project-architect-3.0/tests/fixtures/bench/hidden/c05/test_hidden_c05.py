import unittest

from shelf.members import MemberRegistry


class HiddenC05(unittest.TestCase):
    def setUp(self):
        self.reg = MemberRegistry()
        self.reg.register("Old", "old@x.org")

    def test_import(self):
        text = ("email, name ,kind\n"
                "ann@x.org, Ann ,student\n"
                "\n"
                "OLD@x.org,Dup,\n"
                "bob@x.org,Bob,\n"
                "ANN@x.org,Ann again,staff\n")
        new = self.reg.import_csv(text)
        self.assertEqual([(m.member_id, m.name, m.email, m.kind) for m in new],
                         [("M0002", "Ann", "ann@x.org", "student"),
                          ("M0003", "Bob", "bob@x.org", "standard")])
        self.assertEqual(len(self.reg), 3)

    def test_no_kind_column(self):
        new = self.reg.import_csv("name,email\nZed,zed@x.org\n")
        self.assertEqual(new[0].kind, "standard")

    def test_bad_kind_is_atomic(self):
        text = "name,email,kind\na,a@x.org,standard\nb,b@x.org,visitor\n"
        with self.assertRaises(ValueError) as cm:
            self.reg.import_csv(text)
        self.assertIn("line 3", str(cm.exception))
        self.assertEqual(len(self.reg), 1)

    def test_empty_name(self):
        with self.assertRaises(ValueError) as cm:
            self.reg.import_csv("name,email\n\n ,z@x.org\n")
        self.assertIn("line 3", str(cm.exception))
        self.assertEqual(len(self.reg), 1)

    def test_failed_call_leaves_registry_as_it_was(self):
        # ids are issued in order: a refused import must not use any up
        with self.assertRaises(ValueError):
            self.reg.import_csv("name,email\nAnn,ann@x.org\nBob,bob@x.org\n ,c@x.org\n")
        self.assertEqual(self.reg.register("Next").member_id, "M0002")
        self.assertIsNone(self.reg.find_by_email("ann@x.org"))

    def test_short_rows(self):
        # a row that stops before the kind column has an empty kind cell
        new = self.reg.import_csv("name,email,kind\nZed,zed@x.org\nYan,yan@x.org,student\n")
        self.assertEqual([(m.name, m.kind) for m in new], [("Zed", "standard"), ("Yan", "student")])
        new = self.reg.import_csv("email,name,kind\nwu@x.org,Wu\n")
        self.assertEqual((new[0].name, new[0].kind), ("Wu", "standard"))

    def test_bad_header(self):
        with self.assertRaises(ValueError):
            self.reg.import_csv("name,mail\nx,y\n")

    def test_quoted_cells(self):
        new = self.reg.import_csv('name,email\n"Lovelace, Ada",ada@x.org\n')
        self.assertEqual(new[0].name, "Lovelace, Ada")


if __name__ == "__main__":
    unittest.main()
