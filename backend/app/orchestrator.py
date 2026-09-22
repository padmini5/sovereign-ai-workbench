"""Explicit state machine: PLAN->CHECK_PERMISSIONS->RETRIEVE->EXTRACT->DRAFT->CITE->REVIEW->SIGN->LOG"""
from dataclasses import dataclass, field

STATES = ["PLAN", "CHECK_PERMISSIONS", "RETRIEVE", "EXTRACT", "DRAFT", "CITE", "REVIEW", "SIGN", "LOG", "DONE"]

@dataclass
class Task:
    task_id: str
    query: str
    user: dict
    state: str = "PLAN"
    plan: list = field(default_factory=lambda: STATES.copy())
    events: list = field(default_factory=list)
    chunks: list = field(default_factory=list)
    draft: str = ""
    citations: list = field(default_factory=list)
    status: str = "draft"
    doc_path: str = ""

    def emit(self, state: str, detail: str = ""):
        self.state = state
        self.events.append({"state": state, "detail": detail})

TASKS: dict[str, Task] = {}
