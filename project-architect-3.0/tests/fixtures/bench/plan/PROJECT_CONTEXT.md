# Project context — shelf

shelf is a small library-desk package: a catalog of books keyed by ISBN, a member registry, a loan desk with
renewals, holds and late fees, JSON storage and text reports. Pure Python 3.12, stdlib only, no I/O beyond
`storage.save/load`.

Layout: `shelf/` (models, isbn, dates, policy, fees, catalog, members, holds, loans, storage, reports), `tests/`
(unittest; `tests/helpers.py` builds a desk with six books and a settable clock).

Users are library staff scripts; correctness over speed. Money is integer cents everywhere. Member kinds are
`standard`, `student`, `staff`; per-kind numbers live in `shelf/policy.py`.

Non-goals: a UI, a database, networking, third-party packages.
