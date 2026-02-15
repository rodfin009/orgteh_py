from typing import List, Dict, Optional, Any, Literal
from pydantic import BaseModel, Field, field_validator
import uuid
from datetime import datetime

# --- [1] هياكل بيانات الاستيضاح (Clarification Phase) ---
class ClarificationQuestion(BaseModel):
    id: str = Field(..., description="Unique identifier for the question")
    text: str = Field(..., description="The text of the question")
    options: List[str] = Field(..., description="List of available options")
    allow_other: bool = Field(default=True, description="Allow user to type custom answer")

class ClarificationResponse(BaseModel):
    needs_clarification: bool
    questions: List[ClarificationQuestion] = []

# --- [2] هياكل المهام والتخطيط (Core Logic) ---
class TaskSchema(BaseModel):
    id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:6]}")
    type: Literal["run_shell", "read_file", "edit_file", "call_api", "general_reasoning"]
    description: str
    target_resource: Optional[str] = None 
    status: Literal["pending", "in_progress", "completed", "failed", "skipped"] = "pending"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    failure_reason: Optional[str] = None

    # مصحح تلقائي لأنواع المهام لضمان استقرار النظام
    @field_validator('type', mode='before')
    @classmethod
    def sanitize_task_type(cls, v):
        mapping = {
            "create_file": "edit_file",
            "write_file": "edit_file",
            "make_file": "edit_file",
            "update_file": "edit_file",
            "code": "edit_file",
            "run_command": "run_shell",
            "exec_shell": "run_shell",
            "terminal": "run_shell",
            "analyze": "general_reasoning",
            "plan": "general_reasoning",
            "think": "general_reasoning"
        }
        clean_v = str(v).lower().strip()
        mapped_v = mapping.get(clean_v, clean_v)
        allowed = ["run_shell", "read_file", "edit_file", "call_api", "general_reasoning"]

        if mapped_v not in allowed:
            return "general_reasoning"
        return mapped_v

class PlanSchema(BaseModel):
    goal: str
    steps: List[TaskSchema] = []
    current_step_index: int = 0
    status: Literal["planning", "executing", "completed", "failed"] = "planning"

class Observation(BaseModel):
    task_id: str
    success: bool
    output: Optional[Any] = None 
    error: Optional[str] = None
    output_data: Optional[Dict[str, Any]] = None # لنقل محتوى الملفات والتحليلات
    artifacts: List[str] = [] 
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

# --- [3] حالة الوكيل (State Management) ---
class AgentState(BaseModel):
    session_id: str
    files_context: Dict[str, str] = {} # تخزين الملفات الحالية في الذاكرة
    plan: Optional[PlanSchema] = None
    observations: Dict[str, Observation] = {}
    memory: List[Dict] = []
    config: Dict[str, Any] = {}

    # حالة النظام العامة
    status: Literal["init", "clarifying", "planning", "executing", "verifying", "finished"] = "init"