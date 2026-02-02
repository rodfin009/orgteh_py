# tools/nvidia_engine.py
import os
import httpx
import base64
from fastapi import UploadFile

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

async def execute_ocr(file_input: UploadFile):
    if not file_input: return {"error": "File required"}
    try:
        contents = await file_input.read()
        image_b64 = base64.b64encode(contents).decode()
        invoke_url = "https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-ocr-v1"
        payload = {"input": [{"type": "image_url", "url": f"data:image/png;base64,{image_b64}"}]}
        headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Accept": "application/json"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(invoke_url, json=payload, headers=headers, timeout=40.0)
            return resp.json()
    except Exception as e:
        return {"error": str(e)}

async def execute_embedding(text_input: str, truncate: str):
    if not text_input: return {"error": "Text required"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{NVIDIA_BASE_URL}/embeddings",
                headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
                json={
                    "input": [text_input],
                    "model": "nvidia/llama-3.2-nemoretriever-300m-embed-v2",
                    "encoding_format": "float",
                    "extra_body": {"input_type": "query", "truncate": truncate}
                }, timeout=10.0
            )
            data = resp.json()
            if "data" not in data: return {"error": "API Error", "details": data}
            vector = data["data"][0]["embedding"]
            return {"object": "embedding", "vector_preview": vector[:5], "dims": len(vector)}
    except Exception as e:
        return {"error": str(e)}