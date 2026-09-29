"""Rename plan for PhaseEnd files: ``_`` to ``.`` between version tokens.

Public:
    plan(names) -> [(old, new, kind)]
        *kind* is ``"phase"`` (a ``PhaseEnd_Phase…`` file whose underscored
        version tokens become dots) or ``"misc"`` (anything else, moved into
        a ``misc/`` subdirectory unchanged).

Rules (from the done-when spec):

- ``PhaseEnd_Phase11_5_7.md`` -> ``PhaseEnd_Phase11.5.7.md``  (phase)
- ``PhaseEnd_PhaseGen3_F_7.md`` -> ``PhaseEnd_PhaseGen3.F.7.md``  (phase)
- An already-dotted name is unchanged (no rename entry).
- ``PhaseEnd_Chore_CC_Methodology_BFM_Update_2026_06_16.md`` -> ``misc/…`` (misc)
- A target collision raises ``ValueError`` before anything is moved.

Stdlib only; nothing touches the filesystem at import time.
"""

import re


def plan(names):
    """Return ``[(old, new, kind)]`` for every name that needs renaming.

    *names* is an iterable of file-name strings (base names, no directory).
    Phase names have their version-token underscores replaced with dots;
    non-phase names are sent to ``misc/<same name>``.  Names that are already
    correct are omitted.  Raises ``ValueError`` on a target collision.
    """
    result = []
    targets = {}  # new -> old, for collision detection

    for name in names:
        if not (name.startswith("PhaseEnd_") and name.endswith(".md")):
            continue

        stem = name[len("PhaseEnd_"):-3]  # e.g. "Phase11_5_7"

        # Phase file: starts with "Phase" (possibly followed by a label like Gen3)
        m = re.match(r"^Phase([A-Za-z0-9]*[A-Za-z])?(.*)", stem)
        if m:
            prefix_word = m.group(1) or ""  # e.g. "Gen3" or ""
            version_part = m.group(2)       # e.g. "_11_5_7" or "11_5_7"

            # version_part must start with a digit (after optional _) or be
            # a continuation like "_F_7" for PhaseGen3_F_7
            # Strip leading _ from version_part
            version_part = version_part.lstrip("_")

            if not version_part:
                # Just "Phase" with no version -- treat as misc
                new = "misc/" + name
                kind = "misc"
            else:
                # Replace _ between tokens with . (hyphens denote ranges, preserved)
                new_version = re.sub(r"_+", ".", version_part)
                new_stem = "Phase" + prefix_word + new_version
                new = "PhaseEnd_" + new_stem + ".md"
                kind = "phase"
        else:
            # Non-phase: Chore, etc.
            new = "misc/" + name
            kind = "misc"

        if new == name:
            continue  # already correct

        # Collision detection
        if new in targets:
            raise ValueError(
                "target collision: both %r and %r -> %r"
                % (targets[new], name, new)
            )
        targets[new] = name
        result.append((name, new, kind))

    return result
