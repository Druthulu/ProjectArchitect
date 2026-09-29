"""Tests for pa.install.natural_sort, phaseend_normalize, legacy_index."""

import os
import re
import sys
import tempfile
import textwrap
import unittest

# -- path bootstrap (same as the other test_install_*.py files) ----------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_PA3 = os.path.dirname(_HERE)
if _PA3 not in sys.path:
    sys.path.insert(0, _PA3)

from pa.install import natural_sort, phaseend_normalize, legacy_index  # noqa: E402


# ---------------------------------------------------------------------------
# natural_sort.key
# ---------------------------------------------------------------------------

class TestNaturalSortKey(unittest.TestCase):
    """Sort-key correctness: ordering, stability, and parity with phaseend_index."""

    def test_phase_ordering_basic(self):
        """Phase9_3_5 < Phase9_4-9_7 < Phase9_9."""
        names = [
            "PhaseEnd_Phase9_9.md",
            "PhaseEnd_Phase9_3_5.md",
            "PhaseEnd_Phase9_4-9_7.md",
        ]
        result = sorted(names, key=natural_sort.key)
        self.assertEqual(result, [
            "PhaseEnd_Phase9_3_5.md",
            "PhaseEnd_Phase9_4-9_7.md",
            "PhaseEnd_Phase9_9.md",
        ])


    def test_file_names_sort_like_sort_v(self):
        """T4 fix: suffix dropped, letter/digit runs split -- on raw file names."""
        names = ["PhaseEnd_Phase24.md", "PhaseEnd_Phase23.5.md", "PhaseEnd_Phase23.md",
                 "PhaseEnd_Phase10.md", "PhaseEnd_Phase9.md", "PhaseEnd_PhaseGen3.F.7.md"]
        self.assertEqual(sorted(names, key=natural_sort.key),
                         ["PhaseEnd_Phase9.md", "PhaseEnd_Phase10.md", "PhaseEnd_Phase23.md",
                          "PhaseEnd_Phase23.5.md", "PhaseEnd_Phase24.md",
                          "PhaseEnd_PhaseGen3.F.7.md"])

    def test_phase_ordering_major(self):
        """Phase23 < Phase23.5 < Phase24."""
        names = ["Phase24", "Phase23.5", "Phase23"]
        result = sorted(names, key=natural_sort.key)
        self.assertEqual(result, ["Phase23", "Phase23.5", "Phase24"])

    def test_numeric_before_alpha(self):
        """Numeric families before Gen3.F.7-style labels."""
        names = ["Gen3.F.7", "9.3.5", "24"]
        result = sorted(names, key=natural_sort.key)
        self.assertEqual(result, ["9.3.5", "24", "Gen3.F.7"])

    def test_stable(self):
        """Equal keys preserve insertion order."""
        a = ["foo", "foo"]
        result = sorted(a, key=natural_sort.key)
        # Just checking no crash and length preserved
        self.assertEqual(len(result), 2)

    def test_twelve_name_fixture_agrees_with_phaseend_index(self):
        """Both key functions agree on a 12-name list, and the order is sort -V's."""
        import importlib.util
        tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools",
                            "phaseend_index.py")
        spec = importlib.util.spec_from_file_location("phaseend_index_for_test", tool)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        names = [
            "PhaseEnd_Phase9_3_5.md",
            "PhaseEnd_Phase9_4-9_7.md",
            "PhaseEnd_Phase9_9.md",
            "PhaseEnd_Phase11_5_7.md",
            "PhaseEnd_Phase23.md",
            "PhaseEnd_Phase23.5.md",
            "PhaseEnd_Phase24.md",
            "PhaseEnd_PhaseGen3_F_7.md",
            "PhaseEnd_Phase1.md",
            "PhaseEnd_Phase2_1.md",
            "PhaseEnd_Chore_CC_Methodology_BFM_Update_2026_06_16.md",
            "PhaseEnd_Phase3.3.md",
        ]
        expected = [
            "PhaseEnd_Chore_CC_Methodology_BFM_Update_2026_06_16.md",
            "PhaseEnd_Phase1.md",
            "PhaseEnd_Phase2_1.md",
            "PhaseEnd_Phase3.3.md",
            "PhaseEnd_Phase9_3_5.md",
            "PhaseEnd_Phase9_4-9_7.md",
            "PhaseEnd_Phase9_9.md",
            "PhaseEnd_Phase11_5_7.md",
            "PhaseEnd_Phase23.md",
            "PhaseEnd_Phase23.5.md",
            "PhaseEnd_Phase24.md",
            "PhaseEnd_PhaseGen3_F_7.md",
        ]
        order_ns = sorted(names, key=natural_sort.key)
        order_pi = sorted(names, key=mod.natural_key)
        self.assertEqual(order_ns, expected)
        self.assertEqual(order_pi, expected,
                         "natural_sort.key and phaseend_index.natural_key disagree")


# ---------------------------------------------------------------------------
# phaseend_normalize.plan
# ---------------------------------------------------------------------------

class TestPhaseendNormalize(unittest.TestCase):
    """Rename plan: underscores to dots for phase files, misc/ for non-phase."""

    def test_phase_underscore_to_dot(self):
        names = ["PhaseEnd_Phase11_5_7.md"]
        result = phaseend_normalize.plan(names)
        self.assertEqual(result, [
            ("PhaseEnd_Phase11_5_7.md", "PhaseEnd_Phase11.5.7.md", "phase"),
        ])

    def test_gen_style(self):
        names = ["PhaseEnd_PhaseGen3_F_7.md"]
        result = phaseend_normalize.plan(names)
        self.assertEqual(result, [
            ("PhaseEnd_PhaseGen3_F_7.md", "PhaseEnd_PhaseGen3.F.7.md", "phase"),
        ])

    def test_already_dotted_unchanged(self):
        names = ["PhaseEnd_Phase11.5.7.md"]
        result = phaseend_normalize.plan(names)
        self.assertEqual(result, [])

    def test_chore_goes_to_misc(self):
        names = ["PhaseEnd_Chore_CC_Methodology_BFM_Update_2026_06_16.md"]
        result = phaseend_normalize.plan(names)
        self.assertEqual(len(result), 1)
        old, new, kind = result[0]
        self.assertEqual(old, names[0])
        self.assertTrue(new.startswith("misc/"))
        self.assertEqual(kind, "misc")

    def test_collision_raises(self):
        # Two different names normalising to the same target
        names = [
            "PhaseEnd_Phase1_2.md",
            "PhaseEnd_Phase1__2.md",
        ]
        with self.assertRaises(ValueError):
            phaseend_normalize.plan(names)

    def test_non_phaseend_files_ignored(self):
        names = ["README.md", "something.py"]
        result = phaseend_normalize.plan(names)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# legacy_index
# ---------------------------------------------------------------------------

class TestLegacyIndex(unittest.TestCase):
    """rows() and text() reproduce cmd_legacy output."""

    def _make_phase_dir(self, entries):
        """Create a temp dir with the given PhaseEnd files.

        *entries* is a dict mapping file names to content strings.
        """
        d = tempfile.mkdtemp()
        for name, content in entries.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(content)
        return d

    def test_rows_sorted(self):
        pdir = self._make_phase_dir({
            "PhaseEnd_Phase9.md": "# Phase 9\nMilestone: m9\nDate: 2025-01-01\n",
            "PhaseEnd_Phase2.md": "# Phase 2\nMilestone: m2\nDate: 2024-06-15\n",
        })
        try:
            result = legacy_index.rows(pdir)
            labels = [r[0] for r in result]
            self.assertEqual(labels, ["2", "9"])
        finally:
            for f in os.listdir(pdir):
                os.remove(os.path.join(pdir, f))
            os.rmdir(pdir)

    def test_text_has_header(self):
        pdir = self._make_phase_dir({
            "PhaseEnd_Phase1.md": "# Phase 1\nMilestone: test\nClosed: 2025-03-01\n",
        })
        try:
            result = legacy_index.rows(pdir)
            output = legacy_index.text(result)
            self.assertTrue(output.startswith("# Legacy PhaseEnds (pre-PA3)\n"))
            self.assertIn("# phase | title | milestone | date | path", output)
            self.assertIn("Phase 1", output)
        finally:
            for f in os.listdir(pdir):
                os.remove(os.path.join(pdir, f))
            os.rmdir(pdir)

    def test_header_matches_phaseend_index(self):
        """HEADER constant matches what phaseend_index.py uses."""
        self.assertEqual(
            legacy_index.HEADER,
            "# Legacy PhaseEnds (pre-PA3)\n# phase | title | milestone | date | path\n",
        )

    def test_label_extraction(self):
        """PhaseEnd_Phase11_5_7.md -> label '11.5.7'."""
        pdir = self._make_phase_dir({
            "PhaseEnd_Phase11_5_7.md": "# Phase 11.5.7\n",
        })
        try:
            result = legacy_index.rows(pdir)
            self.assertEqual(result[0][0], "11.5.7")
        finally:
            for f in os.listdir(pdir):
                os.remove(os.path.join(pdir, f))
            os.rmdir(pdir)

    def test_non_phaseend_files_skipped(self):
        pdir = self._make_phase_dir({
            "README.md": "hello",
            "PhaseEnd_Phase1.md": "# P1\n",
        })
        try:
            result = legacy_index.rows(pdir)
            self.assertEqual(len(result), 1)
        finally:
            for f in os.listdir(pdir):
                os.remove(os.path.join(pdir, f))
            os.rmdir(pdir)


if __name__ == "__main__":
    unittest.main()
