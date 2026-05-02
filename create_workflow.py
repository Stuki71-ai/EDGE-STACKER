"""Create TOP 5 Daily workflow via n8n API directly."""
import json
import urllib.request
import os
from pathlib import Path

# n8n API config - MCP uses host.docker.internal but we run on host directly
# Host URL is likely localhost:5678
N8N_API_URL = os.environ.get('N8N_API_URL', 'http://localhost:5678')
N8N_API_KEY = os.environ.get('N8N_API_KEY', '')

# Read workflow payload
base = Path(r'C:\Users\istva\.claude\CODE\EDGE STACKER')
payload = json.loads((base / 'workflow_payload.json').read_text(encoding='utf-8'))

# n8n API create workflow endpoint
# Note: the settings field must conform to n8n schema
body = {
    "name": payload["name"],
    "nodes": payload["nodes"],
    "connections": payload["connections"],
    "settings": payload["settings"],
}

data = json.dumps(body, ensure_ascii=False).encode('utf-8')

headers = {
    "Content-Type": "application/json",
    "X-N8N-API-KEY": N8N_API_KEY,
}

req = urllib.request.Request(
    f"{N8N_API_URL}/api/v1/workflows",
    data=data,
    headers=headers,
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        print("SUCCESS")
        print(f"Workflow ID: {result.get('id')}")
        print(f"Name: {result.get('name')}")
        print(f"Active: {result.get('active')}")
except urllib.error.HTTPError as e:
    print(f"HTTP Error {e.code}: {e.reason}")
    print(e.read().decode('utf-8'))
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
