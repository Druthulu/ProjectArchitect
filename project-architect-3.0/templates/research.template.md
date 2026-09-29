# R<N>-<nnn> — <question as title>

<!-- Written by the retriever that answered the question, always, before it returns. Its return
     to the caller is the `## Answer` section verbatim plus the first line `REPORT: R<N>-<nnn>`.
     Nobody upstream reads this file: experts cite the id, the router never sees it, a later
     retriever greps research/INDEX.md and the cumulative RESEARCH_INDEX.md before re-deriving.

     Pending shape (written to research/pending/<agent>-<slug>.md by retrievers):
       # <title>
       task: <T> · agent: <name> · tags: <a,b>
       ## Answer ...
     `research_add.py adopt` rewrites the header into the R<N>-<nnn> format above and
     indexes the report; the pending file is deleted. -->

task: T<n> | plan · agent: retriever-digest · model: <model/effort> · date: <date> · tags: <tags>
sources:
- <path>:<lines>
- <url> (fetched <date>)

## Answer (returned verbatim, ≤40 lines)
<the answer to the question asked, and nothing else>

## Findings
<everything else worth keeping: what was read, what it means, contradictions, versions>

## Dead ends
<where the answer is not, so the next retriever does not look there>
