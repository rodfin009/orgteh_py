import json
import logging
import os
import re
import time
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

# إزالة الاعتماد الصريح على httpx لتجنب مشاكل الاتصال في Replit
try:
    from openai import AsyncOpenAI
except ImportError:
    print(">>> [CRITICAL] 'openai' library not found. Please run 'pip install openai'")
    AsyncOpenAI = None

try:
    from services.providers import NVIDIA_API_KEY
except ImportError:
    NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

try:
    from tools.registry import TOOLS_DB
except ImportError:
    TOOLS_DB = {}

from .schemas import PlanSchema, TaskSchema, Observation

# --- CONFIGURATION ---
MODEL_ID_CHAT = "qwen/qwen3-coder-480b-a35b-instruct" 
MODEL_ID_EMBED = "nvidia/llama-3.2-nemoretriever-300m-embed-v2"
PROJECT_HOST_URL = "https://dbb6c587-f1e9-40cc-92c9-473f4e530773-00-88b1dcic7jcs.riker.replit.dev" 

# ==============================================================================
# 1. THE EMBEDDING & MEMORY ENGINE
# ==============================================================================
class NvidiaEmbedder:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

    async def get_embedding(self, text: str) -> List[float]:
        try:
            clean_text = text[:2000] if len(text) > 2000 else text
            response = await self.client.embeddings.create(
                input=[clean_text],
                model=MODEL_ID_EMBED,
                encoding_format="float",
                extra_body={"input_type": "query", "truncate": "NONE"}
            )
            return response.data[0].embedding
        except Exception as e:
            print(f">>> [EMBED ERROR] {e}")
            return [0.0] * 1024 

class ProjectContext:
    def __init__(self, goal: str, clarifications: Dict, client: AsyncOpenAI):
        self.goal = goal
        self.clarifications = clarifications
        self.client = client
        self.embedder = NvidiaEmbedder(client)

        self.files_content: Dict[str, str] = {} 
        self.files_vectors: Dict[str, List[float]] = {} 

        self.plan_summary: str = "Plan not started."
        self.current_step_info: str = "Initializing..."

    def update_plan_info(self, plan_str: str, current_step: str):
        self.plan_summary = plan_str
        self.current_step_info = current_step

    async def add_file_artifact(self, filename: str, content: str):
        self.files_content[filename] = content
        vector = await self.embedder.get_embedding(f"File: {filename}\nContent: {content[:1000]}")
        self.files_vectors[filename] = vector

    async def get_relevant_context(self, query: str, top_k: int = 2) -> str:
        if not self.files_vectors: return "No existing files."
        query_vector = await self.embedder.get_embedding(query)
        scores = []
        for fname, vec in self.files_vectors.items():
            if not vec: continue
            score = np.dot(query_vector, vec) / (np.linalg.norm(query_vector) * np.linalg.norm(vec) + 1e-9)
            scores.append((score, fname))
        scores.sort(key=lambda x: x[0], reverse=True)
        top_files = [fname for _, fname in scores[:top_k]]
        context_str = ""
        for fname in top_files:
            snippet = self.files_content[fname][:1500] 
            context_str += f"\n--- REFERENCE FILE: {fname} ---\n{snippet}\n"
        return context_str

    def get_global_brief(self) -> str:
        reqs = ", ".join([f"{k}: {v}" for k, v in self.clarifications.items()])
        existing_files = list(self.files_content.keys())
        return f"PROJECT GOAL: {self.goal}\nREQUIREMENTS: {reqs}\nCOMPLETED FILES: {existing_files}"

# ==============================================================================
# 2. BASE CLIENT
# ==============================================================================
class NvidiaClient:
    def __init__(self):
        if not AsyncOpenAI: raise ImportError("OpenAI library missing")
        self.client = AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=NVIDIA_API_KEY,
            timeout=90.0
        )

    async def invoke(self, messages: list, temperature: float = 0.2, max_tokens: int = 4096, agent_name: str = "Unknown"):
        try:
            print(f">>> [{agent_name}] ⏳ Sending request...", flush=True)
            completion = await self.client.chat.completions.create(
                model=MODEL_ID_CHAT,
                messages=messages,
                temperature=temperature,
                top_p=0.7,
                max_tokens=max_tokens
            )
            return completion.choices[0].message.content
        except Exception as e:
            print(f">>> [{agent_name}] ❌ ERROR: {str(e)}", flush=True)
            return None

class BaseAgent:
    def __init__(self):
        self.nv_client = NvidiaClient()
        self.client = self.nv_client.client 

    def _extract_json(self, content: str) -> Optional[dict]:
        if not content: return None
        try:
            clean = re.sub(r"```json\s*", "", content, flags=re.IGNORECASE)
            clean = re.sub(r"```\s*", "", clean).strip()
            return json.loads(clean)
        except:
            try:
                match = re.search(r'(\{.*\})', content, re.DOTALL)
                if match: return json.loads(match.group(1))
            except: pass
        return None

# ==============================================================================
# 3. INTELLIGENT AGENTS
# ==============================================================================

class ClarificationAgent(BaseAgent):
    async def generate_questions(self, user_prompt: str, config: dict = {}) -> dict:
        target_tools = config.get("target_tools", "")
        tools_context = "NO EXTERNAL TOOLS SELECTED. The user relies on standard web capabilities."
        if target_tools:
            tools_desc = [f"- {t}" for t in target_tools.split(',')]
            tools_context = "USER SELECTED TOOLS (Assume we have API access): " + ", ".join(tools_desc)

        system_prompt = f"""
        Role: Senior Technical Product Owner.

        [CONTEXT]
        User wants a web app.
        Capabilities Context: {tools_context}

        [TASK]
        Analyze request: "{user_prompt}".

        [RULES]
        1. **IF TOOLS SELECTED**: Do NOT ask for API keys or data sources. Assume the tools provide the data.
        2. **IF NO TOOLS**: Do NOT ask how to get data. Plan to use 'Mock Data' (hardcoded JSON) for the prototype.
        3. **ASK ONLY IF**: The core idea is missing.
        4. **SKIP IF**: The user said "News app with blue theme" (This is clear enough).

        Output JSON: {{ "needs_clarification": true/false, "questions": [ {{ "id": "q1", "text": "...", "options": ["..."] }} ] }}
        """
        content = await self.nv_client.invoke([{"role": "system", "content": system_prompt}], temperature=0.2, agent_name="Clarifier")
        return self._extract_json(content) or {"needs_clarification": False}

class PlannerAgent(BaseAgent):
    async def create_plan(self, user_goal: str, clarifications: dict, files_context: dict, chat_history: list = [], config: dict = {}) -> PlanSchema:
        reqs_str = "\n".join([f"- {k}: {v}" for k, v in clarifications.items()])
        existing_files = list(files_context.keys())
        target_tools = config.get("target_tools", "")

        history_str = ""
        if chat_history:
            history_str = "PREVIOUS CONTEXT:\n" + "\n".join([f"[{m.get('role','user')}]: {m.get('content','').strip()[:200]}" for m in chat_history[-6:]])

        tool_instruction = "Implement Mock Data (JSON) for dynamic content. NO BACKEND."
        if target_tools:
            tool_instruction = "Implement API integration using the provided Nexus Tools endpoints."

        system_prompt = f"""
        You are a Pragmatic Software Architect.

        [STATE]
        Goal: "{user_goal}"
        Reqs: "{reqs_str}"
        Files: {existing_files}
        History: {history_str}
        Tools Status: {'Tools Enabled' if target_tools else 'No Tools (Use Mock Data)'}

        [RULES]
        1. **MAX 6 STEPS**: Group related files (HTML/CSS/JS) into logical feature steps.
        2. **CLIENT-SIDE ONLY**: No Node.js/Python servers. 
        3. **DATA STRATEGY**: {tool_instruction}
        4. **COMPLETENESS**: If 'Settings' or 'Dashboard' requested, plan specific HTML files.

        [OUTPUT JSON]
        {{ "goal": "Summary", "steps": [ {{ "type": "edit_file", "description": "Create [List of files] for [Feature]", "target_resource": "index.html" }} ] }}
        """
        content = await self.nv_client.invoke([{"role": "system", "content": system_prompt}], temperature=0.2, agent_name="Planner")
        data = self._extract_json(content)

        if not data:
            return PlanSchema(goal=user_goal, steps=[TaskSchema(type="edit_file", description="Create main application files", target_resource="index.html")])

        steps = []
        if data and "steps" in data:
            for s in data["steps"]:
                try: steps.append(TaskSchema(**s))
                except: pass

        if not steps:
             steps.append(TaskSchema(type="edit_file", description="Create application structure", target_resource="index.html"))

        return PlanSchema(goal=data.get('goal', user_goal), steps=steps)

class ExecutorAgent(BaseAgent):

    def _enforce_nexus_api(self, code: str, tools_list: list) -> str:
        if not code: return code
        # 1. Enforce News Tool if enabled
        if "nexus-news-general" in tools_list and "fetch" in code:
            bad_fetch_pattern = r"fetch\s*\(\s*['\"`].*?\/api\/.*?(?:news|feed).*?['\"`]"
            correct_url = f"'{PROJECT_HOST_URL}/api/tools/execute/nexus-news-general'"
            if re.search(bad_fetch_pattern, code, re.IGNORECASE):
                print(">>> [AUTO-FIX] Replacing News API URL.")
                code = re.sub(bad_fetch_pattern, f"fetch({correct_url}", code, flags=re.IGNORECASE)
                code = f"/* NEXUS TOOL HELPER INJECTED */\nconst NEWS_API_URL = {correct_url};\n" + code
        # 2. If NO TOOLS are enabled, block ANY fetch to /api/ (Prevent 404s)
        elif not tools_list and "fetch('/api/" in code:
             mock_replace = "Promise.resolve({ json: () => Promise.resolve({ articles: [{title: 'Mock News 1', description: 'Sample data because no tool was selected.'}] }) })"
             code = re.sub(r"fetch\s*\(\s*['\"`]\/api\/.*?['\"`].*?\)", mock_replace, code, flags=re.DOTALL)
        return code

    # --- ROBUST PARSER ---
    def _smart_parse_files(self, content: str, default_filename: str) -> List[Tuple[str, str]]:
        extracted = []

        # 1. Strict Parsing
        pattern_strict = r"###\s*([\w\.-]+)\s*\n```(?:\w+)?\n(.*?)\n```"
        strict_matches = re.findall(pattern_strict, content, re.DOTALL)
        if strict_matches:
            return strict_matches

        # 2. Fallback
        print(f">>> [SMART PARSER] Strict parsing failed. Using Fallback.")
        loose_pattern = r"```(?:\w+)?\n(.*?)```"
        blocks = re.findall(loose_pattern, content, re.DOTALL)

        if not blocks and ("<html" in content or "function" in content):
             return [(default_filename, content)]

        if blocks:
            # If multiple blocks found but no headers, assume they belong to default file (concatenated) 
            # OR logic to split CSS/JS? For safety, combine into the target file.
            combined_code = "\n\n".join([b.strip() for b in blocks])
            return [(default_filename, combined_code)]

        return []

    async def execute_task(self, task: TaskSchema, files_context: dict, ctx_manager: ProjectContext, config: dict = {}) -> Observation:
        for fname, content in files_context.items():
            if fname not in ctx_manager.files_content: await ctx_manager.add_file_artifact(fname, content)

        relevant_context = await ctx_manager.get_relevant_context(query=f"{task.description} {task.target_resource}")
        target_tools_str = config.get("target_tools", "")
        tools_list = target_tools_str.split(',') if target_tools_str else []

        tools_docs = ""
        if tools_list:
            tools_docs = "\n\n[CRITICAL: API USAGE RULES]\n"
            tools_docs += "You MUST use the following External Tools via `fetch`. DO NOT invent other APIs.\n"
            for t_id in tools_list:
                tool = TOOLS_DB.get(t_id.strip(), {})
                tools_docs += f"\n- Tool: {tool.get('name_en')}\n  ENDPOINT: `{PROJECT_HOST_URL}/api/tools/execute/{t_id}`\n"
        else:
            tools_docs = "\n\n[CRITICAL: NO EXTERNAL TOOLS]\n"
            tools_docs += "Do NOT write `fetch('/api/...')`. Create 'Mock Data' (hardcoded JSON arrays) inside the JS code.\n"

        system_prompt = f"""
        You are 'Nexus AI', Expert Frontend Engineer.

        [CONTEXT]
        {ctx_manager.get_global_brief()}

        [DATA STRATEGY]
        {tools_docs}

        [TASK]
        Step: {task.description}
        Main File: {task.target_resource}

        [OUTPUT RULES]
        1. **STRICT FORMAT**:
        ### {task.target_resource}
        ```javascript
        // Code here
        ```
        2. **MULTI-FILE OUTPUT**: If you create helper files (e.g. style.css, api.js), use the same format:
        ### style.css
        ```css
        ...
        ```
        """

        messages = [{"role": "system", "content": system_prompt}]
        messages.append({"role": "user", "content": f"Implement: {task.description}. Target: {task.target_resource}"})

        for attempt in range(3):
            content = await self.nv_client.invoke(messages, temperature=0.2, max_tokens=8192, agent_name=f"Exec_{attempt}")
            if not content: continue

            parsed_files = self._smart_parse_files(content, task.target_resource)

            if parsed_files:
                # [CORE FIX]: Instead of returning one file, return ALL files in a list
                all_generated_files = []

                for fname, fcode in parsed_files:
                    clean_code = self._enforce_nexus_api(fcode.strip(), tools_list)
                    await ctx_manager.add_file_artifact(fname, clean_code)

                    all_generated_files.append({
                        "filename": fname,
                        "content": clean_code
                    })

                # output_data now contains "files": [...]
                return Observation(task_id=task.id, success=True, output_data={"files": all_generated_files})

            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"ERROR: Code block missing. Please use ```code```."})

        return Observation(task_id=task.id, success=False, error="Failed to generate code after 3 attempts.")

class VerifierAgent(BaseAgent):
    async def verify_project(self, plan: PlanSchema, files_context: dict) -> List[str]:
        missing = []
        files_found = list(files_context.keys())
        print(f">>> [VERIFIER] Checking files: {files_found}")

        for step in plan.steps:
            if step.type == "edit_file" and step.target_resource:
                if step.target_resource not in files_context or len(files_context[step.target_resource]) < 20:
                    missing.append(step.target_resource)
        return list(set(missing))