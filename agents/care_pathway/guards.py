"""Code-level safety guard.

Prompts can be talked around; this cannot. Before ANY tool runs (in the coordinator
or a sub-agent), we check the case's current step against ALLOWED_TOOLS. A blocked
call never executes: the model gets an error back and the attempt is written to the
audit log, so the doctor can see that the agent tried to skip a gate.
"""

from typing import Any, Optional

from .state_schema import ALLOWED_TOOLS, IDLE_STEPS, CareStep
from .tools import audit


def enforce_step_permissions(tool, args: dict[str, Any], tool_context) -> Optional[dict]:
    state = tool_context.state
    step = state.get("current_step", CareStep.INTAKE)
    allowed = ALLOWED_TOOLS.get(step, set())
    if tool.name in allowed:
        return None  # let the tool run

    if step in IDLE_STEPS:
        reason = (
            f"The case is paused in {step}, waiting for the external "
            f"'{IDLE_STEPS[step]}' event. No actions are allowed until it arrives."
        )
    else:
        reason = f"'{tool.name}' is not permitted in step {step}. Allowed: {sorted(allowed) or 'none'}."

    audit(state, "guard", f"BLOCKED {tool.name} during {step}")
    return {"status": "blocked", "message": reason}
