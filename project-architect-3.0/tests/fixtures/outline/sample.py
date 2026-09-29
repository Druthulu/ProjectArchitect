"""Sample Python file for outline tests.

Header block: the outline prints these lines.
"""


class Widget:
    """A sample class."""

    def render(self, ctx):
        """Render the widget."""
        return ctx

    # A three-line comment block:
    # the outline prints its range
    # and its first line.
    def update(self, value):
        """Update the widget."""
        self.value = value


async def fetch_data(url, timeout=30):
    """Module-level async function."""
    return url


def helper(x):
    """Documented helper: its first line is the intent."""
    return x
