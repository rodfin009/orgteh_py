import json
import logging
import re
import uuid
from services.providers import NVIDIA_API_KEY, NVIDIA_BASE_URL
from openai import AsyncOpenAI
from tools.registry import TOOLS_DB 

# Flexible Import for Schemas
try:
    from schemas import PlanSchema, TaskSchema, Observation
except ImportError:
    from .schemas import PlanSchema, TaskSchema, Observation

# Logger Setup
logger = logging.getLogger("NexusAgents")
PROJECT_HOST_URL = "https://dbb6c587-f1e9-40cc-92c9-473f4e530773-00-88b1dcic7jcs.riker.replit.dev"

class BaseAgent:
    def __init__(self, model_id: str = "z-ai/glm4.7"):
        self.client = AsyncOpenAI(base_url=NVIDIA_BASE_URL, api_key=NVIDIA_API_KEY)
        self.model = model_id

    async def query_llm(self, messages: list, json_mode: bool = True, temperature: float = 0.3) -> dict:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                top_p=0.7,
                max_tokens=16384,
                extra_body={"chat_template_kwargs": {"enable_thinking": True, "clear_thinking": False}}
            )
            content = response.choices[0].message.content
            if json_mode:
                return self._clean_and_parse_json(content)
            return content
        except Exception as e:
            print(f"!!! [LLM ERROR] {str(e)}", flush=True)
            return {"error": str(e)}

    def _clean_and_parse_json(self, content: str) -> dict:
        try:
            # Clean Markdown
            if "```json" in content:
                pattern = r"```json(.*?)```"
                match = re.search(pattern, content, re.DOTALL)
                if match: content = match.group(1).strip()
            elif "```" in content:
                pattern = r"```(.*?)```"
                match = re.search(pattern, content, re.DOTALL)
                if match: content = match.group(1).strip()

            # Find JSON Brackets
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1:
                content = content[start : end + 1]

            return json.loads(content, strict=False)
        except Exception:
            return {"error": "Invalid JSON", "raw": content}

class PlannerAgent(BaseAgent):
    async def create_plan(self, user_goal: str, files_context: dict) -> PlanSchema:
        files_list = list(files_context.keys())
        system_prompt = f"""
        You are the 'Architect Planner' for Nexus V2.
        [CONTEXT] Existing Files: {json.dumps(files_list)}
        [GOAL] "{user_goal}"
        [RULES]
        1. Break goal into atomic tasks (edit_file, run_shell).
        2. MANDATORY: Create necessary files (HTML/JS/Python) first.
        3. LOGICAL ORDER: Requirements -> Backend -> Frontend -> Logic.

        [OUTPUT JSON]
        {{
            "goal": "Summary",
            "steps": [
                {{ "type": "edit_file", "description": "Create app.py", "target_resource": "app.py" }}
            ]
        }}
        """
        result = await self.query_llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_goal}
        ])

        steps = []
        if "steps" in result:
            for s in result["steps"]:
                steps.append(TaskSchema(
                    id=f"task-{uuid.uuid4().hex[:6]}",
                    type=s.get("type", "general_reasoning"),
                    description=s.get("description"),
                    target_resource=s.get("target_resource"),
                    inputs=s.get("inputs", {})
                ))
        return PlanSchema(goal=user_goal, steps=steps)

class ExecutorAgent(BaseAgent):
    def __init__(self, context_config: dict):
        super().__init__()
        self.user_api_key = context_config.get("api_key", "")
        self.target_model = context_config.get("target_model", "deepseek-ai/deepseek-v3.2")
        self.target_tools = context_config.get("target_tools", "")

    async def execute_task(self, task: TaskSchema, files_context: dict) -> Observation:
        try:
            if task.type == "edit_file":
                return await self._handle_code_generation(task, files_context)
            elif task.type == "general_reasoning":
                return await self._handle_reasoning(task)
            else:
                return Observation(task_id=task.id, success=True, output="Simulated Success", artifacts=[])
        except Exception as e:
            return Observation(task_id=task.id, success=False, error=str(e), output=None)

    async def _handle_code_generation(self, task: TaskSchema, files_context: dict) -> Observation:
        target_file = task.target_resource
        existing_content = files_context.get(target_file, "")

        tools_context = ""
        if self.target_tools:
            t_ids = self.target_tools.split(',')
            tools_context = "ENABLED TOOLS:\n"
            for tid in t_ids:
                tool = TOOLS_DB.get(tid.strip())
                if tool: tools_context += f"- ID: {tid}, Use: {tool.get('usage_python')}\n"

        system_prompt = f"""
        You are 'Nexus AI Developer'. Write COMPLETE code for '{target_file}'.

        [CRITICAL CONFIGURATION]
        - Your Backend URL: {PROJECT_HOST_URL}/v1
        - User API Key: {self.user_api_key}
        - AI Model ID: {self.target_model}

        [RULES]
        1. If writing JS/Python to call AI, USE the Configuration above.
        2. If calling Tools, use endpoint: /v1/tools/execute/{{TOOL_ID}} with FormData.
        3. NO placeholders. Write full production code.
        4. Use TailwindCSS for HTML.

        {tools_context}

        [EXISTING CONTENT]
        {existing_content[:1500]}

        [OUTPUT JSON]
        {{ "filename": "{target_file}", "content": "... code string ..." }}
        """

        result = await self.query_llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task.description}
        ])

        if "content" in result:
            return Observation(
                task_id=task.id,
                success=True,
                output=result["content"],
                artifacts=[result.get("filename", target_file)]
            )

        return Observation(task_id=task.id, success=False, error=f"Invalid Response: {result.get('error')}")

    async def _handle_reasoning(self, task: TaskSchema) -> Observation:
        content = await self.query_llm([
            {"role": "system", "content": "Analyze and summarize."},
            {"role": "user", "content": task.description}
        ], json_mode=False)
        return Observation(task_id=task.id, success=True, output=str(content))