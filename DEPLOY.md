# Deploying (Render or Google Cloud Run) + Supabase, and patient emails

The app runs on **Cloud Run** (scales to zero when idle, like the long-running agent
article describes) and stores cases in **Supabase Postgres**, so they survive restarts
and redeploys. Gemini is still called with your API key.

Total time: about 45 minutes the first time. Commands are for Windows `cmd`, run from
the project folder (`C:\projects\care-pathway-agent\care-pathway-agent`).

---

## Part A: Supabase database (about 10 minutes)

1. Go to **supabase.com**, click **Start your project**, sign in with GitHub.
2. Click **New project**:
   - Name: `care-pathway`
   - Database password: click **Generate a password**, then make sure it has **only letters
     and numbers** (symbols break the connection link). Save it somewhere.
   - Region: **South Asia (Mumbai)**
   - Click **Create new project** and wait about 2 minutes.
3. Click **Connect** at the top of the project page. Choose **Session pooler**
   (not "Direct connection", which doesn't work from Cloud Run). Copy the URI. It looks like:
   ```
   postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
   ```
   Replace `[YOUR-PASSWORD]` (including the brackets) with your database password.
4. **Test it locally first.** Open `.env` (`notepad .env`) and add a line:
   ```
   SESSION_DB_URI=postgresql://postgres.abcdefgh:yourpassword@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
   ```
   Restart the server (`uvicorn server.main:app --port 8000`), open
   http://127.0.0.1:8000/clinic and start a case. The case list starts empty because you
   are now on the new database.
5. In Supabase, open **Table Editor**. You should see `sessions` and `events` tables with
   your case in them. The database works.

## Part B: Google Cloud project (about 15 minutes)

1. Go to **console.cloud.google.com** and sign in.
2. Top bar, project picker, **New project**. Name it `care-pathway-sdp`. Note the
   **Project ID** shown under the name (it may have numbers added).
3. Turn on billing: left menu, **Billing**, link a billing account (needs a card). New
   accounts usually get free trial credit. This demo with scale-to-zero stays inside
   Cloud Run's free tier for normal use.
4. Optional but sensible: **Billing > Budgets & alerts > Create budget**, e.g. ₹500, so
   you get an email if anything unexpected happens.
5. Install the **Google Cloud CLI for Windows** (search "install gcloud CLI windows",
   download the installer from cloud.google.com, run it with the defaults).
6. Open a **new** cmd window and run:
   ```
   gcloud init
   ```
   Log in in the browser it opens, then pick your `care-pathway-sdp` project.
   If it asks for a default region, choose `asia-south1` (Mumbai).
7. Turn on the services Cloud Run needs:
   ```
   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
   ```

## Part C: Deploy (about 10 minutes)

1. Create your settings file from the template and fill it in:
   ```
   cd C:\projects\care-pathway-agent\care-pathway-agent
   copy cloudrun.env.example.yaml cloudrun.env.yaml
   notepad cloudrun.env.yaml
   ```
   Fill in your Gemini key, the Supabase URI from Part A, and a `DEMO_PASSWORD` (letters
   and numbers). This file is ignored by Git and is not uploaded with the code.
2. Deploy:
   ```
   gcloud run deploy care-pathway --source . --region asia-south1 --allow-unauthenticated --env-vars-file cloudrun.env.yaml --memory 1Gi --timeout 300 --min-instances 0 --max-instances 1
   ```
   - If it asks to create an Artifact Registry repository, answer **Y**.
   - The first deploy takes about 5 minutes (Google builds the container from the
     `Dockerfile`).
   - `--allow-unauthenticated` lets browsers reach the site; your `DEMO_PASSWORD` is what
     keeps strangers out.
3. At the end it prints a **Service URL** like `https://care-pathway-xxxxx.asia-south1.run.app`.
   Open it. Your browser asks for a username and password: type any username and your
   `DEMO_PASSWORD`.

### Updating after code changes

Run the same `gcloud run deploy ...` command again. Cases are in Supabase, so they survive.

### Things to know

- **Cold start:** with `--min-instances 0` the service sleeps when nobody uses it. The
  first page load after a while takes a few seconds. Open the link a minute before your
  demo.
- **Uploaded X-rays** are kept only until the service next sleeps. The X-rays in
  `sample_data/xrays` are part of the deployment and always available, so use those in demos.
- **Logs:** console.cloud.google.com, **Cloud Run**, `care-pathway`, **Logs** tab.
- **Never use real patient data.** The free Gemini tier may use requests to improve
  Google's products.

### If something goes wrong

| What you see | What to do |
|---|---|
| `PERMISSION_DENIED` or billing errors during deploy | Billing isn't linked to this project yet (Part B step 3). |
| Page loads but every case action shows a database error | Check the Supabase URI: Session pooler, password filled in, no `[ ]`, no symbols in the password. |
| `Password required.` and no prompt | Open the link in a normal (not embedded) browser tab. |
| Error page right after deploy | Open the Logs tab (above); the last red lines say what failed. |

---

## Patient emails with Resend (about 10 minutes)

The app emails each patient message (test instructions, the doctor-approved plan,
check-in replies, the care-team alert) with a link to the patient's own page. Without a
key, everything still works and messages are only shown on the patient page.

1. Go to **resend.com**, sign up (free plan) with the email address you want to receive
   the demo emails on.
2. Left menu, **API Keys**, **Create API key** (permission: *Sending access*). Copy the key
   (starts with `re_`). You only see it once.
3. **Locally:** open `.env` (`notepad .env`) and add:
   ```
   RESEND_API_KEY=re_...your key...
   PUBLIC_BASE_URL=http://127.0.0.1:8000
   ```
   Restart the server. Admit a patient with **your Resend signup email** in the *Email*
   field. Within a few seconds you get "Your tests have been requested".
4. **On Render:** open your service, **Environment**, **Add Environment Variable**, and add:
   | Key | Value |
   |---|---|
   | `RESEND_API_KEY` | your `re_...` key |
   | `PUBLIC_BASE_URL` | your site address, e.g. `https://care-pathway-agent.onrender.com` (no slash at the end) |
   Click **Save, rebuild, and deploy**. (On Cloud Run, add the same two lines to
   `cloudrun.env.yaml` and deploy again.)

Things to know:
- Resend's test sender (`onboarding@resend.dev`) **only delivers to your own signup
  email**. Other addresses show *Email not sent (… Resend refused the recipient …)* in the
  case's audit trail; the case itself carries on. To email anyone, verify a domain you own
  in Resend and set `EMAIL_FROM=Care Pathway <care@yourdomain>`.
- Render's free plan blocks normal mail ports (SMTP), which is why the app uses Resend's
  web API instead.
- The patient page (`/patient/...`) is reached by its private link and does not ask for the
  `DEMO_PASSWORD`; every other screen does.
- Use made-up patients with your own email. Never real patient data.
