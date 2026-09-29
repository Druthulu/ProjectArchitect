## Decomp fail-safes (decomp-architect, Phase 0.5)

- **Never commit game-derived bytes.** ROM dumps, extracted payloads, build output stay out of git.
- **Never `git clean -x`.** Regenerable assets take hours to rebuild.
- **The byte gate is the only definition of done.** A function is matched when `make verify` says so.
