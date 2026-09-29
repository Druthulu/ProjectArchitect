# Generation 1 — shelf 0.4

Goal: the shelf library package gains ISBN-13 keys, open-day due dates, money handling, working holds, durable storage and reports.
Done-criteria: every phase milestone below is green; `python -m unittest discover -s tests` passes.

## Phases

<!-- Line grammar: - <G>.<n> <name> | milestone: <clause>; <clause> | scope: … | depends: — | status: open|closed | phase-end: …
     Each milestone clause names its own `verified by:` check. -->

- 1.1 ISBN-13 keys | milestone: `isbn10_to_isbn13` and `isbn13_to_isbn10` convert both ways and refuse 979 prefixes (verified by: `python -m unittest tests.test_isbn_convert`); the catalog stores every book under `to_isbn13`, so an ISBN-10 and its ISBN-13 are one book whose copies merge (verified by: `python -m unittest tests.test_catalog_isbn13`); the whole suite stays green (verified by: `python -m unittest discover -s tests`) | scope: isbn conversions and the catalog keyed by ISBN-13 | depends: — | status: open | phase-end: phase-ends/PhaseEnd_Phase1.1.md
- 1.2 Open days | milestone: `business_days_between` and `add_business_days` skip weekends and holidays (verified by: `python -m unittest tests.test_business_days`); `LoanDesk` takes `holidays` and every due date from checkout or renew falls on `next_open_day` (verified by: `python -m unittest tests.test_due_open_days`); the whole suite stays green (verified by: `python -m unittest discover -s tests`) | scope: business-day helpers and due dates on open days | depends: — | status: open | phase-end: phase-ends/PhaseEnd_Phase1.2.md
- 1.3 Money | milestone: `format_cents` handles negatives and thousands and `parse_cents` is its inverse (verified by: `python -m unittest tests.test_money`); fee caps come from `fee_cap` per member kind and `outstanding_cents` and `fee_summary` report what members owe (verified by: `python -m unittest tests.test_fee_caps`); the whole suite stays green (verified by: `python -m unittest discover -s tests`) | scope: money formatting and per-kind fee caps | depends: 1.1 | status: open | phase-end: phase-ends/PhaseEnd_Phase1.3.md
- 1.4 Holds that hold | milestone: `HoldQueue` answers `position`, `expire` and `isbns` (verified by: `python -m unittest tests.test_hold_queue`); checkout honours the hold queue and checkin records `notices` for waiting members (verified by: `python -m unittest tests.test_checkout_holds`); holds survive `dumps` and `loads` through `Hold.to_dict` and `HoldQueue.restore` (verified by: `python -m unittest tests.test_holds_storage`); the whole suite stays green (verified by: `python -m unittest discover -s tests`) | scope: hold queue queries, checkout honouring holds, holds persisted | depends: 1.2 | status: open | phase-end: phase-ends/PhaseEnd_Phase1.4.md
- 1.5 Durable storage | milestone: `loads` reads format 1 and still refuses others with `unsupported format` (verified by: `python -m unittest tests.test_format1`); after a load `LoanDesk.restore` makes the next loan id continue and `get_loan` finds loans (verified by: `python -m unittest tests.test_loan_ids`); members carry `balance_cents` that blocks checkout at `FEE_BLOCK_CENTS` and survives a save (verified by: `python -m unittest tests.test_balances`); the whole suite stays green (verified by: `python -m unittest discover -s tests`) | scope: old formats, loan ids and balances across save and load | depends: 1.3, 1.4 | status: open | phase-end: phase-ends/PhaseEnd_Phase1.5.md
- 1.6 Reports | milestone: `aging_report` and `aging_lines` bucket overdue loans into 1-7, 8-30 and 31+ (verified by: `python -m unittest tests.test_aging`); `member_statement` lists a member's loans with status and fees and a total (verified by: `python -m unittest tests.test_statement`); `author_ranking` groups loans by `author_key` (verified by: `python -m unittest tests.test_author_ranking`); the whole suite stays green (verified by: `python -m unittest discover -s tests`) | scope: aging, member statements and author ranking | depends: 1.3, 1.5 | status: open | phase-end: phase-ends/PhaseEnd_Phase1.6.md

## Ordering rationale

- ISBN-13 keys first: every later phase stores or reports books by ISBN.
- Open days before holds: due dates must be right before the desk starts honouring queues.
- Money before storage and reports: balances and statements format and cap fees.

## Standing constraints

- Python 3.12, stdlib only; `shelf/` stays importable without side effects.
- Every behaviour change ships with unit tests under `tests/`; the whole suite stays green after every task.
- `storage.FORMAT` stays 2; old documents keep loading.

## Changes

- 2026-09-24 planner: generation drafted — bench fixture
