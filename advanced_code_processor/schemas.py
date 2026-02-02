from typing import List, Dict, Optional, Any, Literal
from pydantic import BaseModel, Field
import uuid
from datetime import datetime

class TaskDependency(BaseModel):
    task_id: str
    condition: str = "completed"

class TaskSchema(BaseModel):
    id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:6]}")
    type: Literal["run_shell", "read_file", "edit_file", "call_api", "upload_artifact", "general_reasoning"]
    description: str
    target_resource: Optional[str] = None # File path or URL
    inputs: Dict[str, Any] = {}
    expected_output: Dict[str, Any] = {}
    retry_policy: Dict[str, int] = {"max_attempts": 2, "backoff_s": 5}
    timeout_s: int = 120
    status: Literal["pending", "in_progress", "completed", "failed", "skipped"] = "pending"
    dependencies: List[str] = []
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    # حقل جديد لحفظ سبب الفشل إذا وجد
    failure_reason: Optional[str] = None

class PlanSchema(BaseModel):
    goal: str
    steps: List[TaskSchema] = []
    current_step_index: int = 0
    status: Literal["planning", "executing", "completed", "failed"] = "planning"
    meta: Dict[str, Any] = {}

class Observation(BaseModel):
    task_id: str
    success: bool
    output: Any
    error: Optional[str] = None
    artifacts: List[str] = [] # List of filenames created/modified
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

class AgentState(BaseModel):
    session_id: str
    plan: Optional[PlanSchema] = None
    memory: List[Dict[str, Any]] = [] # Chat history + internal logs
    observations: Dict[str, Observation] = {}
    files_context: Dict[str, str] = {} # Virtual file system snapshot

    # [NEW] Configuration Context for the Agents
    config: Dict[str, Any] = Field(default_factory=dict) 
    # Example: {"api_key": "nx-...", "model_id": "deepseek...", "tools": [...]}