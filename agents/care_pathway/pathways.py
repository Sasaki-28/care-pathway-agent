"""Pathway definitions.

This is what makes the engine generic: the state machine, tools, webhooks and UI
are shared. A new disease is a new entry in PATHWAYS, nothing else. The admission
dropdown, the server's validation and the agents' prompts all read from here.

The example texts (symptoms, doctor's notes, check-ins) only pre-fill the demo UI.
"""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Pathway:
    id: str
    name: str                      # full name shown in the case file
    short_name: str                # short label for lists
    tests: list[str]
    requires_imaging: bool
    imaging_focus: str
    lab_focus: str
    follow_up_plan: str
    escalation_rules: list[str] = field(default_factory=list)
    completion_rule: str = ""
    # demo pre-fill only
    example_symptoms: str = ""
    example_doctor_notes: str = ""
    example_check_ins: dict[str, str] = field(default_factory=dict)   # on_track / warning / finished

    def public(self) -> dict:
        return asdict(self)


PATHWAYS: dict[str, Pathway] = {
    "tb": Pathway(
        id="tb",
        name="Suspected pulmonary tuberculosis",
        short_name="TB",
        tests=["Chest X-ray (PA view)", "Sputum NAAT (CBNAAT / TrueNat)"],
        requires_imaging=True,
        imaging_focus=(
            "findings that can be associated with pulmonary TB: upper-lobe "
            "infiltrates or consolidation, cavitation, hilar/mediastinal "
            "lymphadenopathy, miliary nodules, pleural effusion, fibrotic scarring"
        ),
        lab_focus=(
            "whether M. tuberculosis was detected or not detected, the bacterial "
            "load if reported, and rifampicin resistance status"
        ),
        follow_up_plan="Weekly adherence and symptom check-in while treatment is active.",
        escalation_rules=[
            "3 or more missed doses in the past week",
            "coughing up blood",
            "yellow eyes or skin, dark urine (possible liver injury)",
            "persistent vomiting or unable to take medicines",
            "new or worsening breathlessness or chest pain",
            "new vision problems or numbness/tingling",
        ],
        completion_rule="The doctor's notes say the treatment course is finished.",
        example_symptoms="Cough for 3 weeks, evening fever, night sweats, weight loss of about 4 kg",
        example_doctor_notes=("Start first-line TB treatment as per national guidelines. Daily doses, "
                              "review in clinic in 2 weeks. Course length 6 months."),
        example_check_ins={
            "on_track": "Took all 7 doses this week. Cough is less. No other problems.",
            "warning": "Missed 4 doses this week because of travel. Feeling nauseous and my eyes look a bit yellow.",
            "finished": "Finished the full course as the doctor said. Feeling well, no cough.",
        },
    ),
    "pneumonia": Pathway(
        id="pneumonia",
        name="Suspected community-acquired pneumonia",
        short_name="Pneumonia",
        tests=["Chest X-ray (PA view)", "Complete blood count", "C-reactive protein (CRP)"],
        requires_imaging=True,
        imaging_focus=(
            "findings that can be associated with pneumonia: lobar or segmental "
            "consolidation, air bronchograms, patchy infiltrates, pleural effusion; "
            "say which lung zone is affected"
        ),
        lab_focus=(
            "the white cell count, neutrophils and CRP, and whether each is above, within "
            "or below the reference range printed in the report"
        ),
        follow_up_plan="Check-in every 2 to 3 days during the antibiotic course, then once at the end.",
        escalation_rules=[
            "breathless at rest or breathing getting worse",
            "confusion, unusual drowsiness or fainting",
            "fever still high after 3 days of treatment",
            "lips or fingertips turning bluish",
            "new or worsening chest pain",
            "unable to keep fluids or medicines down",
            "2 or more missed antibiotic doses",
        ],
        completion_rule=("The patient reports finishing the antibiotic course and feeling better, "
                         "and the doctor's notes say no further review is needed."),
        example_symptoms=("Fever and cough with yellow sputum for 4 days, pain on the right side of "
                          "the chest when breathing in, breathless climbing stairs"),
        example_doctor_notes=("Start oral antibiotics as per clinic protocol for community-acquired "
                              "pneumonia, 5-day course. Paracetamol for fever. Review in clinic in 3 days, "
                              "no further review needed after that if recovered."),
        example_check_ins={
            "on_track": "Fever went away yesterday, cough is less, taking the antibiotics on time.",
            "warning": "Still high fever after 3 days of antibiotics and now I get breathless just sitting.",
            "finished": "Finished the 5-day course. No fever, breathing is normal, feeling well.",
        },
    ),
    "diabetes": Pathway(
        id="diabetes",
        name="Suspected type 2 diabetes",
        short_name="Diabetes",
        tests=["HbA1c", "Fasting plasma glucose"],
        requires_imaging=False,
        imaging_focus="",
        lab_focus=(
            "HbA1c and fasting glucose values, and whether each is in the normal, "
            "prediabetes or diabetes range according to the report's reference ranges"
        ),
        follow_up_plan="Monthly glucose log and medication adherence check-in.",
        escalation_rules=[
            "any glucose reading above 300 mg/dL or below 70 mg/dL",
            "confusion, fainting or extreme thirst",
            "a foot wound that is not healing",
            "3 or more missed doses in the past week",
        ],
        completion_rule="The doctor's notes say routine follow-up is handed over to the clinic.",
        example_symptoms="Increased thirst and frequent urination for 2 months, tiredness, blurred vision",
        example_doctor_notes=("Start metformin as per clinic protocol. Diet and exercise counselling. "
                              "Repeat HbA1c in 3 months, then routine follow-up handed over to the clinic."),
        example_check_ins={
            "on_track": "Sugar readings between 110 and 150 this month, taking my tablets daily, no problems.",
            "warning": "Readings went above 320 twice this week and I felt very thirsty and dizzy.",
            "finished": "Three months done, HbA1c repeated, the doctor handed my follow-up to the clinic.",
        },
    ),
}


def get_pathway(pathway_id: str) -> Pathway | None:
    return PATHWAYS.get((pathway_id or "").lower())


def pathway_ids() -> str:
    """e.g. "tb, pneumonia or diabetes", for prompts and error messages."""
    ids = list(PATHWAYS)
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " or " + ids[-1]
