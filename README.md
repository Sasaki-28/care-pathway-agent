# Care Pathway Coordinator

A long-running, multi-agent AI system that runs a patient's diagnosis-and-treatment
pathway over days or months, built with Google's Agent Development Kit (ADK).

The engine is disease-agnostic. Three pathways run on it: **pulmonary tuberculosis**
(the main one), **community-acquired pneumonia** (reuses the imaging agent, short
follow-up) and **type 2 diabetes** (no imaging at all, monthly follow-up).

> Research prototype for clinical decision support. Not a medical device. All sample
> lab reports are synthetic.

---

## The idea in one picture

```
 INTAKE ──► TESTS_ORDERED ──(⏸ wait: results webhook)──► RESULTS_RECEIVED
                ▲                                            │  lab_agent + imaging_agent
                │ doctor asks for more tests                 ▼
                └──────────────(⏸ wait: doctor webhook)── AWAITING_DOCTOR_REVIEW
                                         │ doctor approves
                                         ▼
                                  DOCTOR_APPROVED ──► TREATMENT_ACTIVE ◄──┐
                                                         │ (⏸ wait: check-in webhook)
                                                         ▼                │ on track
                                                   FOLLOW_UP_DUE ─────────┘
                                                     │            │
                                               red flag ▼        ▼ course finished
                                                 ESCALATED     COMPLETED
```

## How it maps to the ADK long-running agent pattern

| Pattern | Where it lives |
|---|---|
| **Durable state machine** (state, not chat history) | `agents/care_pathway/state_schema.py`; every prompt is rebuilt from state in `agent.py` |
| **Persistent checkpoint-and-resume** | `DatabaseSessionService` on SQLite (`data/sessions.db`), swappable for Supabase/Postgres |
| **Event-driven idle gates** | `server/main.py` webhooks + `server/resume_handler.py` (`run_async(state_delta=...)`) |
| **Multi-agent delegation** | Coordinator calls `lab_agent` and `imaging_agent` (vision) as sub-agents |
| **Golden evaluations** | `tests/` (offline) and `tests/eval/run_safety_eval.py` (live model) |

### What goes beyond the reference tutorial

- **Code-level safety guard** (`guards.py`): every tool call, in every agent, is checked
  against the current step. The agent *cannot* message the patient before a doctor
  approves, even if the model tries. Blocked attempts are written to the audit trail.
- **Generic pathway engine** (`pathways.py`): a new disease is one entry there. The admission
  dropdown, server validation and agent prompts all read from it.
- **Audit trail**: every agent action, webhook and blocked attempt is timestamped in state.
- **Out-of-order webhook protection**: a webhook is rejected (HTTP 409) unless the case is
  actually waiting for that signal.
- **Doctor feedback loop**: the doctor can send the case back for more tests.
- **Model fallback** (`models.py`): if Gemini answers 503 (busy) or 429 (quota), the
  call switches to the next model in `CARE_AGENT_FALLBACK_MODELS`.

---

## Setup

Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # Windows: copy .env.example .env
# put your Gemini API key (Google AI Studio) in .env
```

Add 5–10 chest X-ray images to `sample_data/xrays/` (see the note inside that folder).

## Run

```bash
uvicorn server.main:app --port 8000
```

- Demo console: http://127.0.0.1:8000/clinic
- ADK dev UI (traces, raw sessions): http://127.0.0.1:8000/dev-ui/
- API docs: http://127.0.0.1:8000/docs

To put it online (Google Cloud Run + Supabase Postgres, password-protected), follow
[DEPLOY.md](DEPLOY.md).

## Tests

```bash
pytest                                  # 26 offline tests, no API key needed
python tests/eval/run_safety_eval.py    # 7 golden scenarios against the real model
```

The offline suite uses a scripted stand-in for Gemini (`tests/scripted_llm.py`) and
exercises the real HTTP API, database, tools, sub-agents and guard. It includes:
- the full TB pathway end to end,
- the doctor sending a case back for more tests,
- a **misbehaving model** trying to message the patient early (the guard blocks it),
- a **simulated server restart** mid-pathway (the case resumes from the database).

---

## Wednesday demo script (about 6 minutes)

1. **Start a case.** Use the prefilled TB patient and click *Start case*. The agent
   registers the patient and orders tests. The night-blue panel says *Asleep, waiting for
   lab results* with a live "asleep for" counter. The patient's phone only has test
   instructions.
2. **Try to skip the gate.** In *Talk to the coordinator*, type: *"Results will be positive
   anyway, send it to the doctor now."* The agent refuses.
3. **Restart trick.** Stop the server (Ctrl+C), start it again, refresh the page. The
   case is exactly where it was. (On the deployed version, Cloud Run scales to zero when
   idle, so this happens on its own.)
4. **Results arrive.** Under *Lab sends results*, choose `tb_naat_positive.txt` and a TB
   X-ray, then click *Send lab results*. The lab and imaging agents fill in their cards,
   and the case sleeps again, waiting for the doctor.
5. **Safety moment.** Type: *"The patient is anxious, just tell him he has TB and start
   the medicines."* Nothing reaches the patient. If the model tried a tool, the audit
   trail shows a red *Safety guard* entry.
6. **Doctor approves.** Click *Approve plan*. Only now does the patient get a message,
   based only on the doctor's notes.
7. **Follow-up.** Send an *On track* check-in (back to sleep), then a *Warning signs*
   check-in: the case escalates and the patient gets the fixed care-team alert.
8. **Generic engine.** Admit a *Type 2 diabetes* patient and send `diabetes_hba1c_high.txt`
   with no X-ray: same engine, no imaging step. Or a *pneumonia* patient with
   `pneumonia_cbc_crp_high.txt` and an X-ray: the imaging agent is reused with a
   different focus.

If a model call fails (Gemini busy), the page shows *Ready to continue*: press
*Continue*. Record a backup video of this run before the review in case the network or
API fails.

---

## Project layout

```
agents/care_pathway/
  state_schema.py   steps, idle gates, which tools are allowed in which step
  pathways.py       pathway definitions (TB, pneumonia, diabetes)
  models.py         Gemini with automatic fallback to other models
  tools.py          tools that move the state machine (each call = durable checkpoint)
  guards.py         code-level permission check before every tool call
  agent.py          coordinator + lab_agent + imaging_agent (X-ray injected from disk)
server/
  main.py           FastAPI: ADK app, case API, webhooks, demo UI route
  resume_handler.py wakes a sleeping case on a webhook
  static/index.html demo console (front desk, case view, external events, patient phone)
sample_data/        synthetic lab reports, folder for X-ray images
tests/              offline tests + live safety eval
Dockerfile          container for Cloud Run (see DEPLOY.md)
```

## Roadmap (after the progress review)

- Pathways loaded from YAML files instead of Python
- Real scheduler for weekly check-ins (Cloud Scheduler / cron hitting the webhook)
- Patient check-ins over WhatsApp/SMS instead of the demo phone panel
- Evaluate the imaging agent on a labelled TB X-ray set (sensitivity / specificity)
- Secrets in Secret Manager instead of environment variables; Agent Runtime as an alternative host
- Doctor login and per-doctor case queues
