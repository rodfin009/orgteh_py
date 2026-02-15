import json
import logging
import asyncio
import traceback
from typing import List, Dict, Any, AsyncGenerator

from .agents import (
    ClarificationAgent, 
    PlannerAgent, 
    ExecutorAgent, 
    VerifierAgent, 
    ProjectContext 
)
from .schemas import PlanSchema, TaskSchema

# Initialize Agents
clarifier = ClarificationAgent()
planner = PlannerAgent()
executor = ExecutorAgent()
verifier = VerifierAgent()

async def run_advanced_orchestration(
    user_instruction: str, 
    files_context: Dict[str, str], 
    session_id: str,
    chat_history: List[Dict],
    config: Dict[str, Any]
) -> AsyncGenerator[str, None]:

    yield json.dumps({"type": "status", "data": "initializing"}).encode() + b"\n"

    try:
        # --- PHASE 1: CLARIFICATION ---
        existing_answers = config.get('clarifications', {})

        if not existing_answers:
            clarification_result = await clarifier.generate_questions(user_instruction, config)
            if clarification_result.get("needs_clarification"):
                yield json.dumps({
                    "type": "clarification_request", 
                    "data": clarification_result
                }).encode() + b"\n"
                return

        # --- PHASE 2: MEMORY SETUP ---
        ctx = ProjectContext(
            goal=user_instruction,
            clarifications=existing_answers,
            client=clarifier.client 
        )
        for fname, fcontent in files_context.items():
            await ctx.add_file_artifact(fname, fcontent)

        # --- PHASE 3: PLANNING ---
        yield json.dumps({"type": "status", "data": "planning"}).encode() + b"\n"

        plan: PlanSchema = await planner.create_plan(
            user_instruction, 
            existing_answers, 
            files_context, 
            chat_history, 
            config
        )

        ctx.update_plan_info(json.dumps(plan.model_dump()), "Planning Complete")

        yield json.dumps({
            "type": "plan_created", 
            "data": plan.model_dump()
        }).encode() + b"\n"

        # --- PHASE 4: EXECUTION ---
        for task in plan.steps:
            yield json.dumps({
                "type": "step_update", 
                "data": {"task_id": task.id, "status": "in_progress"}
            }).encode() + b"\n"

            ctx.current_step_info = f"Executing: {task.description}"

            observation = await executor.execute_task(
                task, 
                files_context, 
                ctx,           
                config
            )

            if observation.success:
                output_payload = observation.output_data

                # [CORE FIX]: Check if multiple files were returned
                files_to_send = []
                if "files" in output_payload:
                    files_to_send = output_payload["files"]
                else:
                    # Fallback for legacy format
                    files_to_send = [output_payload]

                # Process all files
                for file_obj in files_to_send:
                    # Update Memory Context
                    files_context[file_obj['filename']] = file_obj['content']

                    # Send Artifact Event to Frontend
                    yield json.dumps({
                        "type": "artifact", 
                        "data": file_obj
                    }).encode() + b"\n"

                yield json.dumps({
                    "type": "step_update", 
                    "data": {"task_id": task.id, "status": "completed"}
                }).encode() + b"\n"
            else:
                yield json.dumps({
                    "type": "step_update", 
                    "data": {"task_id": task.id, "status": "failed"}
                }).encode() + b"\n"
                yield json.dumps({
                    "type": "error", 
                    "data": {"msg": f"Task Failed: {observation.error}"}
                }).encode() + b"\n"
                return

        # --- PHASE 5: VERIFICATION & REPAIR ---
        yield json.dumps({"type": "status", "data": "verifying"}).encode() + b"\n"

        missing_files = await verifier.verify_project(plan, files_context)

        if missing_files:
            print(f">>> [ORCHESTRATOR] Auto-Repairing files: {missing_files}")
            yield json.dumps({"type": "status", "data": "retry_msg"}).encode() + b"\n"

            for missing_file in missing_files:
                repair_task = TaskSchema(
                    type="edit_file",
                    description=f"Create missing file: {missing_file}",
                    target_resource=missing_file
                )

                yield json.dumps({"type": "step_update", "data": {"task_id": repair_task.id, "status": "in_progress"}}).encode() + b"\n"

                obs = await executor.execute_task(repair_task, files_context, ctx, config)

                if obs.success:
                    # Handle multiple files in repair too
                    files_to_send = obs.output_data.get("files", [obs.output_data])
                    for f_obj in files_to_send:
                        files_context[f_obj['filename']] = f_obj['content']
                        yield json.dumps({"type": "artifact", "data": f_obj}).encode() + b"\n"

                    yield json.dumps({"type": "step_update", "data": {"task_id": repair_task.id, "status": "completed"}}).encode() + b"\n"

        yield json.dumps({"type": "finish", "data": {}}).encode() + b"\n"

    except Exception as e:
        print(f">>> [ORCHESTRATOR ERROR]: {e}")
        traceback.print_exc()
        yield json.dumps({
            "type": "error", 
            "data": {"msg": str(e)}
        }).encode() + b"\n"