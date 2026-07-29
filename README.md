# careertrace-ai
A memory-powered AI career agent that helps students discover opportunities, match with jobs and alumni, personalize applications, and manage networking workflows. Built with persistent memory, semantic retrieval, and agentic workflows to provide personalized career guidance over time.

---

## structure

careertrace-ai/
│
├── app/
│   ├── main.py
│   ├── graph/
│   │   └── career_agent.py
│   ├── nodes/
│   │   ├── profile.py
│   │   ├── job_match.py
│   │   ├── networking.py
│   │   └── memory.py
│   ├── tools/
│   │   ├── database.py
│   │   ├── vector_search.py
│   │   └── bedrock.py
│   └── models/
│       └── schemas.py
│
├── requirements.txt
├── README.md
└── .gitignore

                    LangGraph

                         |
          +--------------+--------------+
          |                             |
          v                             v

      Nova Lite                    Claude Sonnet

Low-cost reasoning              High-value reasoning

- Extract profile               - Job ranking
- Intent classification         - Career advice
- Query parsing                 - Alumni matching
- Metadata extraction           - Outreach writing
- Summarization                 - Resume tailoring


                              User
                               |
                               v
                    +---------------------+
                    |   LangGraph Router  |
                    | Intent + Task State |
                    +---------------------+
                               |
                               |
          +--------------------+--------------------+
          |                                         |
          v                                         v

  CONTROLLED WORKFLOW (80%)              AUTONOMOUS REASONING (20%)
  Predictable business logic             Flexible AI decisions


+--------------------------+             +----------------------------+
| Profile Management       |             | Planning & Reasoning       |
+--------------------------+             +----------------------------+
| Upload resume            |             | Expand job concepts        |
| Parse documents          |             | Generate search queries    |
| Extract profile facts    |<----------->| Decide relevant memories   |
| Validate information     |             | Identify missing info      |
| Ask confirmation         |             | Learn user preferences     |
| Save confirmed facts     |             +----------------------------+
+--------------------------+


+--------------------------+             +----------------------------+
| Job Search Workflow      |             | Job Intelligence Agent     |
+--------------------------+             +----------------------------+
| Retrieve profile         |             | Semantic job matching      |
| Apply hard filters       |<----------->| Explain job fit            |
| Calculate match metrics  |             | Identify transferable      |
| Save job candidates      |             | skills                     |
| Request user selection   |             | Rank recommendations       |
+--------------------------+             +----------------------------+


+--------------------------+             +----------------------------+
| Networking Workflow      |             | Alumni Reasoning Agent     |
+--------------------------+             +----------------------------+
| Retrieve alumni records  |<----------->| Find meaningful overlap    |
| Track contacted people   |             | Analyze career paths       |
| Save outreach status     |             | Generate outreach strategy |
| Schedule follow-ups      |             | Draft personalized message |
+--------------------------+             +----------------------------+


                               |
                               v

                 +-------------------------------+
                 |      Human Approval Gates     |
                 +-------------------------------+
                 | Confirm profile updates       |
                 | Approve resume modifications  |
                 | Approve outreach messages     |
                 | Select jobs/alumni            |
                 +-------------------------------+

                               |
                               v

                 +-------------------------------+
                 |      CockroachDB Memory       |
                 +-------------------------------+

                 Structured Memory (SQL)
                 --------------------------------
                 User profile
                 Skills
                 Projects
                 Job history
                 Applications
                 Alumni contacts
                 Outreach status
                 Follow-up schedule


                 Semantic Memory (Vector)
                 --------------------------------
                 Resume chunks
                 Project descriptions
                 Job descriptions
                 Alumni profiles
                 Past recommendations
                 User feedback/rejections

---

### 1. Profile onboarding workflow

START
 |
 v
Upload Resume
 |
 v
Extract Resume Text
 |
 v
LLM: Extract Facts
 |
 v
Human Confirmation
 |
 +---- rejected ----> Edit Profile
 |
 v
Save Confirmed Facts
 |
 v
Generate Initial Career Profile
 |
END

---

### 2. Job search workflow

User request
 |
 v
Retrieve user profile
 |
 v
Apply hard constraints
 |
 v
Calculate structured scores
 |
 v
Save candidates
 |
 v
User selects jobs

hard constraints:
- wrong graduation year
- unavailable location
- wrong work authorization
- not internship/full-time mismatch

Stored:
jobs
job_requirements
applications

---

### 3. Alumni networking workflow

Select company/job
 |
 v
Retrieve alumni
 |
 v
Check previous contacts
 |
 v
Save selected alumni
 |
 v
Track outreach

---

### 4. Memory architecture

Used by deterministic workflow:
users
profiles
skills
projects
jobs
applications
contacts
followups
preferences

Used by autonomous reasoning:
Vector embeddings:
resume section
project description
previous rejected job
