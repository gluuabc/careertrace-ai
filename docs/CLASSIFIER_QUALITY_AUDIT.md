# Classifier quality audit — Phase 8

Date: 2026-08-15

Corpus: synthetic, deterministic, and free of production data

Cheap role: `BEDROCK_MODEL_CHEAP=amazon.nova-lite-v1:0`

Reasoning role tested: `BEDROCK_MODEL_REASONING=global.anthropic.claude-sonnet-4-6`

## Decision surfaces

Routing uses the cheap role with `IntentDecision` structured output. The exact
production prompt is `ROUTING_SYSTEM_PROMPT`; its identifiers are
`intent-decision-v2` and `routing-2026-08-15-v2`. The schema contains one
`CareerIntent`, a goal, clarification fields, and bounded `MemorySignal`
proposals. Routing context contains the persisted summary when present,
conversation messages after that summary boundary, optional active workflow and
selected entity IDs, and the latest request. Profile data, approved memories,
and Skill files are intentionally excluded.

Conversation memory extraction also uses the cheap role with
`MemoryExtractionOutput`, a list of typed profile-or-memory proposals. Its input
is a bounded conversation segment plus deterministic, source-message-linked
signals, relevant profile fields, and at most five relevant approved memories.
An LLM proposal is never sufficient evidence: only deterministic explicit-user
signals may now produce a profile revision draft or memory candidate.

Requirement gating is not model-backed. `apply_hard_filters` operates on
`JobSearchRequest` and extracted `JobCandidate` fields and preserves the
`MATCH` / `CONFLICT` / `UNKNOWN` enum. Unknown evidence remains non-passing.

## Controlled live comparison

The routing corpus contains 10 cases and the memory corpus contains 11 cases.
One follow-up expectation was corrected during the audit: “Which of those should
I apply to first?” is guidance over existing candidates, not a request for a new
provider search.

| Variant | Raw routing | Final routing | Wrong action after validation | Durable-memory false positives | Structured errors |
|---|---:|---:|---:|---:|---:|
| A. Nova Lite + old prompt/current context | 6/10 | 8/10 | 1/10 | 0 after validation; 2/3 negative cases before validation | 1/21 |
| Context-only improvement + old prompt | 6/10 | 8/10 | 1/10 | not re-measured | 0/10 routing |
| Prompt-only improvement + current context | 9/10 | 9/10 | 1/10 | not re-measured | 0/10 routing |
| B. Nova Lite + improved prompt/context | 9/10 | 9/10 | 1/10 | 0/3 after validation | 1/21 |
| C. Reasoning + old prompt/current context | 10/10 | 10/10 | 0/10 | 0/3 | 1/21 (memory schema validation) |
| D. Reasoning + improved prompt/context | 10/10 | 10/10 | 0/10 | 0/3 | 0/21 |
| Production prompt + final validation | 8/10 raw, 1 provider error | 10/10 for all parsed routes | 0/10 | 0/3 | 1/21 |

The final 10/10 routing result includes deterministic clarification for vague
requests and incompatible simultaneous workflows. A provider structured-output
error on the assistant-recommendation echo case is retried in production and
then enters the existing safe deterministic fallback; it does not execute an
unvalidated action.

## Root causes and controls

1. The role-comparison failure was primarily prompt ambiguity. The old prompt
   did not distinguish opportunity retrieval from role advice. The deterministic
   retrieval boundary prevented the wrong source workflow, and the explicit
   prompt definition raised Nova Lite raw agreement.
2. The durable `memory.goal` proposal for an advisory comparison was a model
   false positive enabled by incomplete post-model validation. Goal validation
   already prevented persistence. This audit extended the same rule to every
   profile and memory type because the baseline also proposed unsupported school,
   skill, and experience facts in non-declarative requests.
3. Stale workflow context was not the primary routing cause. Context-only
   truncation remained at 8/10 final while the prompt-only variant reached 9/10.
   Current context construction is therefore unchanged.
4. The observed requirement error was in deterministic normalization/filtering,
   not model capability. Broad keyword logic treated any sponsorship language as
   conflict, and broad locations as explicit mismatches. Explicit polarity now
   controls sponsorship decisions; ambiguous sponsorship and national/multiple
   locations remain `UNKNOWN`. Explicit seniority mismatches are `CONFLICT`.
5. The structured schemas successfully constrain enum shape but cannot prove
   semantic correctness. Deterministic validation remains authoritative.

## Gate corpus results

The offline corpus covers explicit match, explicit conflict, absent evidence,
ambiguous sponsorship, ambiguous location, internship versus full-time,
seniority mismatch, and skill preference versus hard requirement.

- `UNKNOWN -> MATCH` false promotions: 0/3 ambiguous or missing cases.
- Explicit `CONFLICT` misses: 0/7 asserted conflict fields/cases.
- Desired skills remain ranking inputs and are not promoted to hard constraints.

## Escalation and model decision

No stronger-model escalation was required in the 10 routing cases: 0/10 (0%).
The uncertain cases were safer and cheaper to clarify deterministically. A
reasoning model improved raw agreement to 10/10, but Nova Lite plus the bounded
prompt and deterministic controls produced zero wrong actions and zero durable
memory false positives. `BEDROCK_MODEL_CHEAP` should therefore remain Nova Lite.

Remaining error classes are unusual explicit-memory phrasings that the
deterministic detector may conservatively miss, transient structured-output
provider errors, and semantic requirements not present in extracted source
fields. These should remain missed/unknown rather than be inferred into durable
facts or hard-gate matches.

Offline validation completed with 71/71 focused classifier, boundary, memory,
job-gate, and integration-correction tests and 124/124 broader Career Agent,
memory extraction, persistence, retrieval, and search tests. The combined run
passed 195/195 tests.

## Privacy-safe observability

Each routing result now records classifier version, prompt version, model role,
validation result (`accepted`, `overridden`, or `deterministic_fallback`), whether
escalation occurred, and the final intent enum. The same safe fields are included
in the trajectory and final run state. No prompt, user text, credentials, hidden
reasoning, or chain-of-thought is stored in these diagnostics.
