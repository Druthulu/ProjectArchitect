# Transferable engineering kernels

These are concrete coding and engineering lessons drawn from prior projects that transfer to any new project. They complement the registry seed (which holds the generic process and governance rules) and `distilled-kernels.md` (which holds the top cross-project methodology kernels). Grouped by area.

## General engineering

### A simulation must model achievable execution, not idealized outcomes
- **Class:** general
- **Kernel:** A simulation must model achievable execution, not idealized outcomes.

Account for latency, gaps, and duration caps, and reject impossible actions (same-instant results, overshoot beyond tolerance). Isolate the mechanism under study from unrelated constraints — add a dedicated flag that lifts them for a pure study. Bound reporting artifacts so a single extreme edge case can't blow up the summary.

### Parameterize per-case; regression-test derived math; don't ship speculative diagnostics
- **Class:** general
- **Kernel:** Never fix one case by changing a global parameter — parameterize per-case instead.

Pin derived-data math with regression tests against real cached inputs so a later refactor can't silently drift it. Don't build diagnostic infrastructure speculatively; build it when a specific question needs it.

### Every hard-coded constant is a latent bug when generalizing a single-case pipeline
- **Class:** general
- **Kernel:** When generalizing a single-case pipeline to many cases, every hard-coded constant is a latent bug.

Sweep for constants that silently assumed the original case and make them per-case inputs. Guard console-buffer operations against redirected output, and verify a metric's units against an absolute anchor before trusting it.

### Verify profiler claims with ground truth; parity is not faithfulness; profile dependency-free
- **Class:** general
- **Kernel:** Verify profiler findings against ground-truth instrumentation before acting on them.

Byte-identical parity proves a port is correct, not that its design is faithful to intent — those are separate claims to prove separately. Run batch/CPU profiles dependency-free (no injection container, credentials, or database) so the profile reflects the code, not the harness.

### The cross-language parameterization seam is the riskiest port surface
- **Class:** general
- **Kernel:** The parameterization seam between two languages is the riskiest surface in a port.

Pre-grep for stale assumptions that survived the translation before trusting the ported side. A threshold or gate calibrated on one dataset or domain is informative, not binding, on a different one — re-derive it in the new context.

### Diagnose an apparent regression by build-invariance + git history + diff nature before blaming the environment
- **Class:** general
- **Kernel:** Diagnose an apparent regression by checking build/SDK invariance, git history, and the nature of the diff before blaming the environment.

Most "the environment broke it" reports resolve to a code change with a traceable commit. When a shared driver gains a new axis, it must thread that axis end-to-end (including any upstream rebuild) and use collision-safe output names.

### Dedup input-independent work for big speedups; give each parallel worker its own state; cache-on == cache-off parity gate
- **Class:** general
- **Kernel:** Deduplicate the input-independent portion of a batch computation — compute it once, then apply the varying parameters — for order-of-magnitude speedups.

Give each parallel worker its own mutable state (a shared clock or counter races), and precompute upfront rather than lazily (lazy init serializes the workers). Gate cache-ON vs cache-OFF on the same inputs as the definitive parity check, defer any behavior-changing tie-break so comparisons stay byte-exact, and scope every output file per unit — a speedup exposes latent output-file races.

### Verify a subsystem ever actually ran before inheriting it; the CSV BOM footgun
- **Class:** general
- **Kernel:** Verify a subsystem ever actually ran in the path you're extending before treating its behavior as inherited.

A hardcoded `null` where a value was expected can mean the subsystem never executed at all. When wiring behavior in, choose the single injection point that covers all consumers. Beware the CSV BOM footgun: a UTF-8-with-BOM export prepends bytes that corrupt the first CSV header key for downstream parsers — write cross-tool CSVs BOM-less.

### Gate a data convention in the artifact's own metadata, with cache-stamp invalidation
- **Class:** general
- **Kernel:** Gate a data-format or convention decision in the artifact's own metadata, not in a mergeable config flag.

Stamp derived caches with that convention so a mismatch drops-to-recompute rather than silently serving stale data. Never ship past a red parity/test suite (a dated skip-quarantine is the only alternative), and regenerate a parity fixture canonically from fresh inputs, never by re-exporting through the code under test.

### A change that regenerates fixtures drifts the test schema; run a cheap premise test first
- **Class:** general
- **Kernel:** A change that regenerates golden fixtures drifts the test schema, so split the parity tests (skip the old schema, add the new) rather than loosen a real gate.

A header-parse throw after such a change is fixture drift, not a logic break — the real break-check is the input-independent cache-on == cache-off byte-identity. Run a cheap read-only premise test before any costly build or run to confirm the change is even necessary.

### Never overwrite an in-flight input; never run two jobs into one output directory
- **Class:** general
- **Kernel:** Never overwrite an input/plan file that an in-flight process is still reading, and never run two jobs into the same output directory.

Both silently corrupt per-unit outputs and any appended results file. Verify no lingering process before re-running, and use distinct output directories per job.

### A file-hash baseline must store full hashes; table-rendering truncates and fakes a diff
- **Class:** general
- **Kernel:** A file-hash baseline must store full hashes — a table-renderer truncates them and fakes an "everything changed" diff.

The decisive "did anything change" check is modification-time plus version-control status, not a hash diff alone. Verify a guard's inputs are themselves current (stale metadata silently weakens a gate), and have a pre-registered gate load its thresholds from the pre-registration record and refuse to run un-pre-registered.

## C#/.NET

### Verify peer-AI diagnoses against the code; hot-path cache discipline
- **Class:** C#/.NET
- **Kernel:** Never accept an external (peer-AI) architectural diagnosis without verifying its premises against the code.

On hot paths, prefer `TryGet`/`Set` over factory-based `GetOrAdd`, avoid LINQ enumerators, and compose wrappers instead of re-invoking statics. Key caches on every discriminating dimension so distinct inputs can't collide, and make cache wrappers handle empty inputs.

### Numeric hygiene: decimal at the public API, double in hot loops, explicit rounding order
- **Class:** C#/.NET
- **Kernel:** Expose `decimal` at the public API but compute hot loops in `double`, and fix the rounding order explicitly.

Use an explicit form such as `(decimal)Math.Round(x, N)` so the rounding point is unambiguous. Define any derived-value convention once and apply it consistently everywhere, and preserve comments on rewrite.

### Stamp provenance at emission; know framework binding limits; lock shared mutable state; poll config lazily
- **Class:** C#/.NET
- **Kernel:** Stamp a record's provenance at the moment of emission, not later where the source is ambiguous.

Know your UI-framework binding limits (e.g. a nullable `bool?` can't bind to a checkbox `asp-for`). Guard shared mutable state behind an explicit lock, and poll configuration lazily rather than caching it eagerly at startup.

### A shared config key must satisfy every reader; verify a historical note's premise; close gracefully
- **Class:** C#/.NET
- **Kernel:** A new shared config key must satisfy all of its readers, not just the one that motivated it.

Never trust a historical note's premise without re-verifying it against the current code. Implement `IAsyncDisposable` for async resources and send a graceful close (e.g. a WebSocket close frame) before disposing.

### Probe a third-party API for real before building; HttpClient header/decompression quirks; never commit identifying values in config
- **Class:** C#/.NET
- **Kernel:** Empirically probe a third-party provider's actual free tier and API surface before subscribing or building against it.

Catalog presence is not API availability; distinguish soft warnings from hard refusals, and know anti-abuse fingerprints survive incognito. Never commit identifying values (email, name) in config. `HttpClient.Headers.Add` does a strict RFC parse — use `TryAddWithoutValidation`; and `HttpClient` does not auto-decompress brotli/zstd.

### dotnet run --no-build runs the stale bin; rebuild after a config edit; input coverage is first-class hygiene
- **Class:** C#/.NET
- **Kernel:** `dotnet run --no-build` executes the stale copied bin/config, so always rebuild after a config edit.

Treat data/input coverage as first-class hygiene, checked before use rather than assumed. Set binding parameters explicitly per config rather than relying on a type default.

### A new config block needs both the class property and the binding line; rebuild the host after editing config
- **Class:** C#/.NET
- **Kernel:** A new config block needs both the class property and the registration/binding line, or it silently reads defaults.

Rebuild the host after editing its config. Keep a decision-branch reason field separate from an analytics/reporting field, and note that a parity gate can be tolerance-based on named metrics rather than strictly bit-identical.

### The enum-zero sentinel trap: copy configs field-for-field
- **Class:** C#/.NET
- **Kernel:** Never merge or clone a config by treating class defaults or enum-zero values as "unset" sentinels — copy field-for-field.

A value type whose zero is a legitimate value (`default(enum)` equals its first member) silently drops a valid override under a `!= default` test. A clean reproduction of an archived run proves a cache is faithful, not that the config is correct — the archive reproduces its own latent bugs.

### Merge configs via one shared helper; a parameter tuned while a bug corrupted its input is unusable after the fix
- **Class:** C#/.NET
- **Kernel:** Merge configs by copying the full baseline through one shared helper, then overwriting only genuinely-sparse fields.

Assign enum fields unconditionally so a zero-valued override isn't dropped. A parameter tuned while a bug was corrupting the very thing it tunes is unusable after the fix — re-tune on the corrected system. Prove integrity by construction (independent per-key loading plus runtime checks), not by byte-identity to an archive that carried the bug.

### Cache only the fields consumers actually read; localize a memory hog by its concurrency-vs-memory slope; GC tweaks are a secondary lever
- **Class:** C#/.NET
- **Kernel:** Cache only the fields consumers actually read — a retained heavyweight object per key is the real memory hog.

A lean projected value can cut retained memory by an order of magnitude with byte-identical results. Localize a memory hog by its concurrency slope (per-worker demand vs shared demand). Server-GC tweaks are a secondary lever, not the fix, and committed memory far exceeding working set can be genuine demand thrashing rather than GC slack.

### Key a derived-data cache by every convention its consumers declare, and warm the declared variant
- **Class:** C#/.NET
- **Kernel:** Key a derived-data cache by every convention its consumers declare (e.g. variant × format), and warm the declared variant.

A convention-mismatched consumer silently drops to slow direct-compute — in one case running dramatically slower per item because its cache never hit. Mirror the live path exactly, and prove the change is purely additive and byte-identical.

## Data & analytics

### Near-zero raw correlation does not imply an orthogonal contribution; namespace the whole run
- **Class:** data
- **Kernel:** Near-zero raw correlation does not imply an orthogonal contribution.

A model can re-route through correlated inputs, so judge feature redundancy at the model's output/decision level, not by raw pairwise correlation. A run's tag must namespace the whole run (model directory, resume cache, and report) or cached artifacts silently load across configs.

### An extreme single-step value is usually a data break, not a real event
- **Class:** data
- **Kernel:** An extreme single-step value is usually a data break — a placeholder, identifier reuse, or a stitched panel gap — not a real event.

Filter it before ranking or aggregating, since one glitch record can dominate a top-N or an aggregate. Always check that a headline result isn't driven entirely by a handful of such records — dropping a few can collapse a "significant" effect.

### Expect feature redundancy in a saturated model; prove a null result with a positive control
- **Class:** data
- **Kernel:** A feature added to a saturated model that already routes through overlapping inputs adds almost nothing at the decision level, so treat redundancy as the base case and gate additions on incremental decision-info.

When near-total redundancy could itself be a pipeline artifact ("nothing could move the output"), run a positive control: feed a known-informative feature under the same frozen settings. If it moves the output, the pipeline is live and the null is a real property of the tested feature.

## Long-running work & agents

### Saturate available cores for long batch jobs
- **Class:** ops
- **Kernel:** Set a long batch job's concurrency to the machine's core count unless memory binds.

An under-parallelized batch leaves most of the CPU idle — matching concurrency to cores alone can lift utilization from a fraction of the machine to near-full and cut wall-clock substantially, at no correctness cost.

### Unattended long jobs run as detached OS processes that survive the agent-reap
- **Class:** ops
- **Kernel:** In an agent/sandbox environment, agent-tracked background commands are reaped shortly after the agent idles and foreground is time-capped, so run long jobs as detached OS processes that survive the reap.

Pair the detached job with short chained tracked pollers and completion markers to observe progress. Consult the ops reference's host-run recipe before launching — it is cheaper than re-deriving the mechanics the hard way.

28 kernels.
