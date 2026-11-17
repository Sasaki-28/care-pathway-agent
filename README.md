# Care Pathway Coordinator

A long-running, multi-agent AI system that runs a patient's diagnosis-and-treatment
pathway over days or months, built with Google's Agent Development Kit (ADK).

The engine is disease-agnostic. Five pathways run on it, all defined in one config file
(`agents/care_pathway/pathways.json`): **pulmonary tuberculosis** (the main one),
**community-acquired pneumonia** (reuses the imaging agent), **dengue** (no imaging, daily
check-ins), **type 2 diabetes** and **hypertension** (no imaging, long-term follow-up).

Each person has their own screen: the **clinic desk** (board of every case, admissions,
case files), the **lab** (sends results, including a photo or PDF of the report), the
**doctor** (review queue and sign-off) and the **patient** (a private page linked from
the emails they receive).

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
- **Generic pathway engine** (`pathways.json`): a new disease is one entry in a config file,
  checked at start-up. Screens, server validation, triage and agent prompts all read from it.
- **AI triage suggestion** (`triage.py`): suggests a pathway from the presenting complaint,
  with its reason. Staff confirm or change it. Falls back to keyword rules if Gemini is down.
- **Role-based screens** with a private, token-protected patient page.
- **Patient emails** (`server/notify.py`, Resend): every message in the patient's inbox is
  emailed with a link to their page. The inbox is only written by guarded tools, so the
  doctor-approval gate covers email too. The email address never reaches the AI.
- **Lab report photos/PDFs**: the lab agent reads an uploaded report image directly.
- **X-ray accuracy check** (`tests/eval/run_xray_eval.py`): sensitivity and specificity of
  the imaging agent on labelled, de-identified public X-rays.
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

- Choose a role: http://127.0.0.1:8000/  (clinic desk `/clinic`, doctor `/doctor`, lab `/lab`)
- Patient page: opened from the link in the emails, or *Open their page* on a case file
- One-screen demo console (old view): http://127.0.0.1:8000/clinic/single
- ADK dev UI (traces, raw sessions): http://127.0.0.1:8000/dev-ui/
- API docs: http://127.0.0.1:8000/docs

To put it online (Render or Google Cloud Run + Supabase Postgres, password-protected),
follow [DEPLOY.md](DEPLOY.md). It also explains how to turn on patient emails.

## Tests

```bash
pytest                                  # 56 offline tests, no API key needed
python tests/eval/run_safety_eval.py    # 7 golden scenarios against the real model
python tests/eval/run_xray_eval.py --tb-dir ... --normal-dir ... --n 25   # X-ray accuracy
```

The offline suite uses a scripted stand-in for Gemini (`tests/scripted_llm.py`) and
exercises the real HTTP API, database, tools, sub-agents and guard. It includes:
- the full TB pathway end to end,
- the doctor sending a case back for more tests,
- a **misbehaving model** trying to message the patient early (the guard blocks it),
- a **simulated server restart** mid-pathway (the case resumes from the database),
- patient emails (sent only for released messages, never with the email in a prompt),
- the patient page's token check and check-ins, the doctor's queue, lab report uploads,
  triage, and broken pathway configs being rejected.

### Testing on real data, safely

- **X-rays:** download the public, de-identified *Tuberculosis (TB) Chest X-ray Database*
  from Kaggle and run `tests/eval/run_xray_eval.py` on it. It prints a confusion matrix,
  sensitivity, specificity and 95% confidence intervals, and saves them to
  `data/xray_eval.md`.
- **Lab reports:** upload a photo of a real report on the lab screen *after* covering the
  name, ID numbers and address. Never use identifiable patient data: it would need consent
  and ethics approval, and the free Gemini tier may use inputs to improve Google's products.

---

## Final review demo script (about 8 minutes)

Open four tabs: `/clinic`, `/lab`, `/doctor`, and later the patient page.

1. **Clinic desk.** Show the board: every case, grouped by who it is waiting for.
   Click *Admit a patient*, type a complaint (e.g. fever, pain behind the eyes, rash),
   click *Suggest from symptoms*: the AI suggests *Dengue* and says why. Accept it, add
   your own email (the one you signed up to Resend with), and admit. The coordinator
   orders the tests and the patient gets an email with a link to their page.
2. **Try to skip the gate.** In the case file's *Coordinator* tab: *"Results will be
   positive anyway, send it to the doctor now."* It refuses.
3. **Patient page.** Open the link from the email on your phone: plain status, the test
   instructions, and a lock where results will appear.
4. **Lab screen.** Pick the patient, send the sample report (or upload a photo of a
   redacted real report) and an X-ray for a TB case. The lab and imaging agents read
   them; the case moves to the doctor.
5. **Doctor screen.** The queue shows the case with flags. The doctor sees the AI's
   summary, the findings, the X-ray and the uploaded report. Before signing, show the
   *safety moment*: in the clinic tab ask the coordinator to tell the patient now; the
   guard blocks it (red line in the audit trail). Then approve: only now does the patient
   get the doctor's words, by email and on their page.
6. **Check-ins.** On the patient page send *Feeling fine* (back to sleep), then *Feeling
   worse*: the case escalates, appears under *Escalations* in the doctor's queue, and the
   patient gets the fixed care-team message.
7. **Generic engine and evidence.** Open `pathways.json` to show a pathway is just config.
   Show the X-ray accuracy table from `data/xray_eval.md` and the offline test run.

If a model call fails (Gemini busy), the clinic case file shows *Ready to continue*:
press *Continue*. Record a backup video of this run before the review.

---

## Project layout

```
agents/care_pathway/
  state_schema.py   steps, idle gates, which tools are allowed in which step
  pathways.json     pathway definitions (TB, pneumonia, dengue, diabetes, hypertension)
  pathways.py       loads and checks pathways.json
  triage.py         suggests a pathway from the presenting complaint
  models.py         Gemini with automatic fallback to other models
  tools.py          tools that move the state machine (each call = durable checkpoint)
  guards.py         code-level permission check before every tool call
  agent.py          coordinator + lab_agent + imaging_agent (X-ray injected from disk)
server/
  main.py           FastAPI: ADK app, case/doctor/patient APIs, webhooks, screens
  resume_handler.py wakes a sleeping case on a webhook, then emails new patient messages
  notify.py         patient emails through Resend
  static/           home (role picker), clinic, doctor, lab, patient pages; assets/ shared CSS/JS
sample_data/        synthetic lab reports, folder for X-ray images
tests/              offline tests; eval/ live safety eval and X-ray accuracy check
Dockerfile          container for Cloud Run (see DEPLOY.md)
```

## Roadmap

- Real scheduler for weekly check-ins (cron hitting `/webhooks/follow_up_due`)
- Check-ins over WhatsApp/SMS as well as the patient page
- Doctor login and per-doctor queues; patient email verification
- A verified sending domain for email; secrets in a secret manager
- A larger X-ray evaluation (more images, a second dataset such as Shenzhen/Montgomery)
