# Corpus — Transferable Kernels

The generic engineering and methodology lessons carried forward from prior projects, with all project-specific content removed. These are the durable, reusable lessons; a new project starts with them instead of re-learning them.

The generic **process and governance rules** live in the registry seed (`templates/RULES_REGISTRY.seed.md`, §A–§M) and the generic **working-agreement memories** live in `memory-seed/`. This folder holds the two kernel references that complement them:

| File | Contents |
|---|---|
| `distilled-kernels.md` | The methodology layer: the core cross-project engineering discipline (the detailed reference for the seed's §M rules, with "transfers to" guidance) plus a few higher-level patterns (adversarial verification, escalate to a tool's own source, isolated-agent economics). |
| `engineering-kernels.md` | The concrete coding layer: transferable techniques and gotchas grouped by area (general engineering, C#/.NET, data and analytics, long-running work and agents). |

Every entry is domain-neutral. Nothing here is tied to a specific project, stack, or product.

## How the installer uses it

At constitution generation or migration (SETUP.md §8), the assistant consults these kernels and installs or adapts the ones that fit the project:

1. **Match by stack.** Grep for the project's language or framework and pull the matching entries (the C#/.NET group is the richest).
2. **Match by problem shape.** Scan the kernel titles against what the project will do: simulations, data and analytics, batch or agent work, config-heavy systems, anything with a determinism or parity contract.
3. **Generalize and record.** Carry a kernel's takeaway, re-domained to the project, into the project's registry §E, and note the origin with a short `adapted from corpus` tag.

## Growing it

When a Project Architect project matures, fold its genuinely transferable, project-agnostic lessons back into these two files (never project-specific content), and keep this file's table current.
