# CareerTrace Demo Assets

Every file in this directory is synthetic or a dated demonstration fixture. No
file contains real personal information, and no example connection is presented
as a verified public alumni record.

## `Demo_Resume.pdf`

- Synthetic resume for Maya Chen, a fictional computer science student.
- Contains education, graduation year, skills, projects, and experience needed
  by the Profile onboarding workflow.
- Recommended document for the three-minute Judge walkthrough.

## `Demo_Portfolio.pdf`

- Synthetic supplemental portfolio for the same fictional student.
- Exercises multi-document extraction and source linking.
- Optional for the short walkthrough because processing a second document adds
  model and upload latency.

## `Example_Alumni_Connections.csv`

- Three synthetic, user-provided connection examples.
- Uses fields accepted by the current importer: `name`, `education`,
  `organization`, `role`, and `public_profile_url`.
- `role` is normalized to the application's `current_role` field during import.
- Example URLs use `example.com`; they do not establish public identity or
  authoritative alumni status.

Imported rows can help shortlist a user's private connections. CareerTrace
still treats them as user-provided and unverified unless an independent,
permitted public source supports the claim.

## `search_fixtures/`

These are dated, explicitly labeled public-source snapshots used only as a
Judge-mode fallback when live search returns too few usable results or times
out. Job snapshots are not claimed to be currently open, and people snapshots
are not claims about current roles or affiliations.
