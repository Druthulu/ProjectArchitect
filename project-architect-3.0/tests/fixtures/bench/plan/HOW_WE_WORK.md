# How we work — shelf

- Interpreter: `python` (3.12). Tests: `python -m unittest discover -s tests` from the project root; a task's own tests run as
  `python -m unittest tests.<module>`.
- One phase plan at a time; task lines follow the grammar in `PHASE_SEED.md`; agents `expert-opus55` (default) and
  `expert-fable` (`effort: high`, rare); coders `opus55` or `none`.
- Every task names its files and a runnable `verify:`; new tests go in `tests/test_<topic>.py`.
- No new dependencies. Commits name the task id; nothing is pushed.
