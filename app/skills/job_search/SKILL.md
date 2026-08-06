---
name: job_search
description: Search public job sources, enforce hard constraints, preserve evidence, and rank verified results.
---

# Job Search

Collect missing requirements once, then search permitted public sources within
the configured source and iteration budgets. Treat employment type, location,
remote requirement, student level, graduation year, eligibility, and explicitly
relevant work authorization as hard constraints. Never silently relax them.

Normalize and deduplicate results before ranking. A candidate with unknown
eligibility remains in the unverified pool and does not count toward the requested
verified total. Material claims must cite stored evidence IDs.

Read `source_policy.md` for source restrictions and `ranking_rules.md` when a fit
comparison is needed.
