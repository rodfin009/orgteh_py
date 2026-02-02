import json
import asyncio
import traceback
import sys
from datetime import datetime
from .schemas import AgentState, PlanSchema, TaskSchema, Observation

try:
    from .agents.planner import PlannerAgent
    from .agents.executor import ExecutorAgent
except ImportError:
    from .planner import PlannerAgent
    from .executor import ExecutorAgent

class AdvancedOrchestrator:
    def __init__(self, session_id: str, user_instruction: str, files_context: dict, chat_history: list = None, config: dict = None):
        self.state = AgentState(
            session_id=session_id,
            files_context=files_context,
            memory=chat_history if chat_history else [],
            config=config if config else {} # [NEW] حفظ الإعدادات
        )
        self.instruction = user_instruction
        self.is_running = False
        self.planner = PlannerAgent()
        self.executor = ExecutorAgent()
        print(f"--- [ORCHESTRATOR] Initialized Session {session_id} (Model: {self.state.config.get('target_model')}) ---", flush=True)

    async def run(self):
        self.is_running = True

        # [FIX 2] إرسال إشارة للواجهة لإخفاء شريط الإدخال
        yield self._emit("ui_control", {"action": "hide_input", "state": True})
        yield self._emit("init", {"msg": "Initializing Nexus V2 Agents..."})

        try:
            print("--- [ORCHESTRATOR] STEP 1: Planning ---", flush=True)
            yield self._emit("thought", {
                "agent": "Planner", 
                "content": f"Analyzing user request: '{self.instruction}'..."
            })

            # CALLING PLANNER
            print("--- [ORCHESTRATOR] Calling PlannerAgent.create_plan()...", flush=True)
            plan_result = await self.planner.create_plan(self.instruction, self.state.files_context)
            print(f"--- [ORCHESTRATOR] Plan Created with {len(plan_result.steps)} steps ---", flush=True)

            self.state.plan = plan_result
            yield self._emit("plan_created", self.state.plan.dict())

            print("--- [ORCHESTRATOR] STEP 2: Execution Loop ---", flush=True)
            for i, task in enumerate(self.state.plan.steps):
                if not self.is_running: break

                print(f"--- [ORCHESTRATOR] Executing Task {i+1}/{len(self.state.plan.steps)}: {task.description} ---", flush=True)

                self.state.plan.current_step_index = i
                task.status = "in_progress"
                yield self._emit("step_update", {"task_id": task.id, "status": "in_progress"})

                yield self._emit("thought", {
                    "agent": "Executor", 
                    "content": f"Processing: {task.description}..."
                })

                # CALLING EXECUTOR with CONFIG
                # [FIX 3] تمرير الإعدادات للمنفذ ليستخدمها في الكود
                observation = await self.executor.execute_task(task, self.state.files_context, self.state.config)

                print(f"--- [ORCHESTRATOR] Task Result: Success={observation.success} ---", flush=True)

                self.state.observations[task.id] = observation

                if observation.success:
                    task.status = "completed"
                    yield self._emit("step_update", {"task_id": task.id, "status": "completed"})

                    if observation.artifacts and observation.output:
                        filename = observation.artifacts[0]
                        print(f"--- [ORCHESTRATOR] New Artifact Generated: {filename} ---", flush=True)

                        self.state.files_context[filename] = observation.output
                        yield self._emit("artifact", {
                            "filename": filename,
                            "content": observation.output
                        })
                else:
                    print(f"--- [ORCHESTRATOR] Task Failed: {observation.error} ---", flush=True)
                    task.status = "failed"
                    task.failure_reason = observation.error
                    yield self._emit("step_update", {"task_id": task.id, "status": "failed"})
                    yield self._emit("error", {"msg": f"Task failed: {observation.error}"})
                    # يمكن هنا إضافة منطق Retry بسيط مستقبلاً

            yield self._emit("finish", {"msg": "All tasks executed successfully."})
            print("--- [ORCHESTRATOR] Process Finished Successfully ---", flush=True)

        except Exception as e:
            err_msg = f"System Error: {str(e)}"
            print(f"!!! [ORCHESTRATOR CRASH] {err_msg} !!!", flush=True)
            traceback.print_exc()
            yield self._emit("error", {"msg": err_msg})
        finally:
            self.is_running = False
            # إعادة إظهار الشريط عند الانتهاء (اختياري)
            # yield self._emit("ui_control", {"action": "hide_input", "state": False})

    def _emit(self, event_type: str, data: dict):
        payload = {
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data
        }
        return json.dumps(payload) + "\n"

# API Entry Point
# [FIX 1] تحديث التوقيع لاستقبال config
async def run_advanced_orchestration(instruction: str, files_context: dict, session_id: str, chat_history: list = None, config: dict = None, *args, **kwargs):

    if not chat_history and args: chat_history = args[0]

    orchestrator = AdvancedOrchestrator(session_id, instruction, files_context, chat_history, config)
    async for event in orchestrator.run():
        yield event