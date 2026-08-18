# Judge Testing Instructions

This walkthrough exercises the normal CareerTrace workflow with an isolated,
empty Judge workspace. The supplied documents and prompts contain synthetic
information only. Allow up to three minutes on a warm deployment; external
model and search-provider latency can make a cold run longer.

## 1. Start Judge mode

1. Open the [CareerTrace AI demo](https://ca-52d4a73a56df47e78e9405283c5d3daa.ecs.us-east-1.on.aws/).
2. Select **Try Judge Demo**.
3. Enter the private Demo Access Code supplied with the submission and select
   **Start New Workspace**. Google allowlisting is not required.
4. Copy the one-time recovery code. Only its hash is stored; the plaintext code
   cannot be shown again.

Each new Judge workspace receives a separate UUID user identity. It starts
empty and uses the same S3, Bedrock, SQL, retrieval, and approval paths as a
normal account.

## 2. Upload and confirm the demo resume

1. Open **Documents** → **Upload & Analyze**.
2. Download **Demo resume** and upload `Demo_Resume.pdf`.
3. Leave its document type as `resume` and select **Analyze documents**.
4. Review the extracted fields. Correct a field if necessary, then confirm the
   profile.

For the three-minute path, upload only the resume. `Demo_Portfolio.pdf` is an
optional second-document test.

## 3. Create three review candidates

Open **Career Assistant**, create a conversation, and send this single prompt:

> I also know Rust. I prefer mission-driven education technology companies. I recently completed a retrieval evaluation project yesterday.

The three explicit statements exercise separate destinations:

| Statement | Expected review item |
|---|---|
| `I also know Rust.` | Profile revision candidate for `skills` |
| `I prefer mission-driven education technology companies.` | Semantic memory candidate in a preference group |
| `I recently completed a retrieval evaluation project yesterday.` | Episodic career-event candidate with grounded temporal evidence |

CareerTrace does not save these suggestions immediately.

## 4. Trigger the conversation boundary

Select **New conversation** once. Starting the new conversation closes the
previous conversation segment and runs memory extraction. Switching away from
the prior conversation or logging out also creates a boundary, but **New
conversation** is the shortest demo path.

If extraction is temporarily unavailable, the pending boundary remains in SQL
and is retried after login.

## 5. Review and approve

Open **Memory Universe** → **Memory review**.

1. Approve the semantic preference candidate.
2. Approve the episodic career-event candidate.
3. Under **Pending profile update suggestions**, select **Accept field change**
   for Rust and then **Apply accepted changes**.

Candidate wording can vary because the model performs semantic classification.
Approve only items supported by the displayed user evidence. A rejected item
does not enter durable memory.

## 6. Verify approved-memory retrieval

Return to the new Career Assistant conversation and ask:

> Using my preference for mission-driven education technology companies, my recently completed retrieval evaluation project, and my Rust skill, what should I focus on next?

The response should use the confirmed Profile and relevant approved memories.
Open **Personalization references** to inspect the Profile and memory references
selected for the answer.

## 7. Exercise bounded search plus guidance

In the same conversation, ask:

> Find me ML internships in California and tell me how to improve my chances.

CareerTrace completes one job-search action and the guidance portion before the
combined response. Live results remain structured. When live providers are slow
or insufficient in Judge mode, CareerTrace may show clearly labeled historical
demo snapshot suggestions; snapshots are not claims that a job is currently
open.

Optional people-search test:

1. Download `Example_Alumni_Connections.csv` from **Documents**.
2. Under **Career Assistant** → **People Search connections**, import the CSV.
3. Ask: `Find alumni from Northstar Institute of Technology who work in AI.`

Imported rows are user-provided examples, not verified public alumni records.
Public identity claims still require independent public-source verification.

## Reset or resume

- To reset, log out and start a new Judge workspace. It receives a new UUID and
  empty SQL state.
- To resume, choose **Resume Existing Workspace** and enter both the private
  Demo Access Code and the one-time recovery code.
