"""3.15 T4: each expert's -5m twin has its parent's body byte for byte; parents 1h, twins 5m."""

import os
import unittest

AGENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents")


def split(name):
    """(frontmatter lines, body text) of ``agents/<name>.md``."""
    with open(os.path.join(AGENTS, name + ".md"), "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    head, _, body = text[4:].partition("\n---\n")
    return head.splitlines(), body


class ExpertTwinTest(unittest.TestCase):

    def test_twins(self):
        for parent in ("expert-opus55", "expert-fable"):
            twin = parent + "-5m"
            with self.subTest(twin=twin):
                p_head, p_body = split(parent)
                t_head, t_body = split(twin)
                self.assertEqual(t_body, p_body)
                self.assertIn("  cacheTtl: 1h", p_head)
                self.assertIn("  cacheTtl: 5m", t_head)
                self.assertIn("name: " + twin, t_head)


if __name__ == "__main__":
    unittest.main()
