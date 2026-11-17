"""X-ray accuracy check: how often does the imaging agent flag TB correctly?

Runs the REAL imaging agent (same prompt, same model, same tool as the live system) on
labelled, de-identified public chest X-rays and reports sensitivity, specificity and
accuracy. Needs GOOGLE_API_KEY. Nothing is uploaded anywhere except to Gemini.

Dataset (free, public, de-identified): "Tuberculosis (TB) Chest X-ray Database" on Kaggle
(Rahman et al., 2020). Download and unzip it, then from the project root:

    python tests/eval/run_xray_eval.py ^
        --tb-dir "C:/data/TB_Chest_Radiography_Database/Tuberculosis" ^
        --normal-dir "C:/data/TB_Chest_Radiography_Database/Normal" --n 50

(On Mac/Linux use \\ instead of ^ for line breaks.)

--n is images per class (default 25, so 50 images). Each image takes 2 model calls; the
script pauses between images to stay inside the free tier. Results go to a CSV after
every image, so you can stop (Ctrl+C) and run the same command again to continue.

How a prediction is scored:
    "TB" = suspicion_level high or moderate   (the agent would flag it for the doctor)
    "not TB" = low
    "unable_to_assess" and errors are counted separately, not as right or wrong.
The imaging agent is decision support for a doctor, not a diagnostic test: these numbers
show how useful its flag is, they do not make it a medical device.
"""

import argparse
import asyncio
import csv
import math
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents"))
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "agents" / "care_pathway" / ".env")

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402

from care_pathway.agent import IMAGING_MODEL_NAME, imaging_agent  # noqa: E402
from care_pathway.state_schema import CareStep, initial_state  # noqa: E402

IMAGE_TYPES = {".png", ".jpg", ".jpeg"}
FIELDS = ["file", "truth", "suspicion", "predicted", "image_quality", "impression", "error", "seconds"]
RATE_LIMIT_WAIT = 65


def pick(folder: Path, n: int, seed: int) -> list[Path]:
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_TYPES)
    if not files:
        sys.exit(f"No .png/.jpg images found in {folder}")
    random.Random(seed).shuffle(files)
    return files[:n]


def predicted(level: str) -> str:
    return {"high": "TB", "moderate": "TB", "low": "normal"}.get(level, "unassessed")


async def read_one(path: Path) -> dict:
    sessions = InMemorySessionService()
    runner = Runner(app_name="care_pathway", agent=imaging_agent, session_service=sessions)
    state = initial_state()
    state.update(pathway_id="tb", current_step=CareStep.RESULTS_RECEIVED, xray_path=str(path))
    s = await sessions.create_session(app_name="care_pathway", user_id="eval", state=state)
    msg = types.Content(role="user", parts=[types.Part.from_text(text="Analyse the attached chest X-ray.")])
    async for _ in runner.run_async(user_id="eval", session_id=s.id, new_message=msg):
        pass
    final = await sessions.get_session(app_name="care_pathway", user_id="eval", session_id=s.id)
    return final.state.get("imaging_findings") or {}


def wilson(k: int, n: int) -> str:
    """95% confidence interval for a proportion (Wilson score)."""
    if n == 0:
        return "n/a"
    z, p = 1.96, k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return f"{max(0, centre - half):.0%}-{min(1, centre + half):.0%}"


def report(rows: list[dict], out_md: Path) -> None:
    scored = [r for r in rows if r["predicted"] in ("TB", "normal") and not r["error"]]
    tp = sum(r["truth"] == "TB" and r["predicted"] == "TB" for r in scored)
    fn = sum(r["truth"] == "TB" and r["predicted"] == "normal" for r in scored)
    tn = sum(r["truth"] == "normal" and r["predicted"] == "normal" for r in scored)
    fp = sum(r["truth"] == "normal" and r["predicted"] == "TB" for r in scored)
    unassessed = sum(r["predicted"] == "unassessed" and not r["error"] for r in rows)
    errors = sum(bool(r["error"]) for r in rows)
    pct = lambda a, b: f"{a / b:.0%}" if b else "n/a"  # noqa: E731
    high_only = [r for r in scored if r["truth"] == "TB"]
    high_sens = sum(r["suspicion"] == "high" for r in high_only)

    lines = [
        "# X-ray check: imaging agent vs labelled public X-rays",
        "",
        f"Model: {IMAGING_MODEL_NAME}. Images: {len(rows)} "
        f"({sum(r['truth'] == 'TB' for r in rows)} TB, {sum(r['truth'] == 'normal' for r in rows)} normal), "
        "Kaggle 'Tuberculosis (TB) Chest X-ray Database' (Rahman et al., 2020).",
        "Flagged as TB = suspicion high or moderate.",
        "",
        "| | Agent: TB | Agent: not TB |",
        "|---|---|---|",
        f"| **Actually TB** | {tp} (true positive) | {fn} (missed) |",
        f"| **Actually normal** | {fp} (false alarm) | {tn} (true negative) |",
        "",
        "| Measure | Value | 95% CI |",
        "|---|---|---|",
        f"| Sensitivity (TB cases flagged) | {pct(tp, tp + fn)} | {wilson(tp, tp + fn)} |",
        f"| Specificity (normal cases not flagged) | {pct(tn, tn + fp)} | {wilson(tn, tn + fp)} |",
        f"| Accuracy | {pct(tp + tn, len(scored))} | {wilson(tp + tn, len(scored))} |",
        f"| Sensitivity counting only 'high' as TB | {pct(high_sens, len(high_only))} | |",
        "",
        f"Could not assess: {unassessed}. Errors (busy model, quota): {errors}. "
        "These are left out of the measures above.",
        "",
        "Decision support for a doctor, not a diagnostic test. Small sample: read the confidence intervals.",
    ]
    text = "\n".join(lines)
    out_md.write_text(text + "\n", encoding="utf-8")
    print("\n" + text + f"\n\nSaved: {out_md}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tb-dir", required=True, type=Path, help="folder of TB X-rays")
    ap.add_argument("--normal-dir", required=True, type=Path, help="folder of normal X-rays")
    ap.add_argument("--n", type=int, default=25, help="images per class (default 25)")
    ap.add_argument("--seed", type=int, default=7, help="random seed for which images are picked")
    ap.add_argument("--pause", type=float, default=9, help="seconds between images (free tier: keep >= 8)")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "xray_eval.csv")
    args = ap.parse_args()

    todo = [(p, "TB") for p in pick(args.tb_dir, args.n, args.seed)] + \
           [(p, "normal") for p in pick(args.normal_dir, args.n, args.seed)]
    random.Random(args.seed).shuffle(todo)          # mix the classes so a quota stop doesn't skew them

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = {}
    if args.out.exists():
        with args.out.open(newline="", encoding="utf-8") as f:
            done = {r["file"]: r for r in csv.DictReader(f) if not r["error"]}
        print(f"Continuing: {len(done)} images already scored in {args.out}")

    remaining = [(p, t) for p, t in todo if str(p) not in done]
    print(f"{len(remaining)} images to read with {IMAGING_MODEL_NAME} "
          f"(about {len(remaining) * (args.pause + 6) / 60:.0f} min).", flush=True)

    rows = list(done.values())
    try:
        for i, (path, truth) in enumerate(remaining, 1):
            start, row = time.time(), {"file": str(path), "truth": truth, "error": ""}
            for attempt in (1, 2):
                try:
                    f = await read_one(path)
                    level = f.get("suspicion_level", "")
                    row.update(suspicion=level, predicted=predicted(level), image_quality=f.get("image_quality", ""),
                               impression=f.get("impression", ""), error="" if level else "no findings recorded")
                    break
                except Exception as exc:  # noqa: BLE001
                    text = f"{type(exc).__name__}: {exc}"
                    if attempt == 1 and ("429" in text or "RESOURCE_EXHAUSTED" in text):
                        print(f"  free-tier limit hit, waiting {RATE_LIMIT_WAIT}s ...", flush=True)
                        await asyncio.sleep(RATE_LIMIT_WAIT)
                        continue
                    row.update(suspicion="", predicted="unassessed", image_quality="", impression="", error=text[:200])
            row["seconds"] = f"{time.time() - start:.1f}"
            rows = [r for r in rows if r["file"] != row["file"]] + [row]
            with args.out.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            mark = "ok " if row["predicted"] == truth else "-- " if row["predicted"] in ("TB", "normal") else "?? "
            print(f"{mark}{i:3}/{len(remaining)}  truth={truth:6}  agent={row.get('suspicion') or row['error'][:60]}  {path.name}",
                  flush=True)
            if i < len(remaining):
                await asyncio.sleep(args.pause)
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to continue.")
    report(rows, args.out.with_suffix(".md"))


if __name__ == "__main__":
    asyncio.run(main())
