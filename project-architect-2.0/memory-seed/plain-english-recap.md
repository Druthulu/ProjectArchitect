---
name: plain-english-recap
description: End every session/phase with a plain-English recap — a few jargon-free sentences on what was done and why — in chat AND stored durably in the PhaseEnd
metadata:
  type: feedback
---

End each session (and each phase) with a short few-sentence recap in high-level, plain English: what was done and WHY — not jargon, not a changelog dump. And the recap must ALSO be **stored in the PhaseEnd file** as its final `## Plain-English Recap` section — not just spoken in chat.

**Why:** The developer steers the project but isn't tracking every technical detail; a plain-language recap keeps them oriented on real progress and what it means. The chat message is ephemeral (gone at session end); the PhaseEnd is the durable state a fresh session reads, so the recap has to live there too. **Registry: P8.**

**How to apply:** (1) In the PhaseEnd, make `## Plain-English Recap` the final section — 2–5 sentences, define any term you must use, say what the phase WAS / WHY it was needed / WHAT it did. (2) Also make a plain-English recap the LAST thing in the session-end / phase-close chat message. See [[project-governance-system]].
