import sys
import os
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "providers"))
import notion_subscriptions as ns  # noqa: E402

ns._load_dotenv_if_present()

TEST_DATABASE_ID = "3ab66ca4-2976-80fd-81fc-dd2603cebfcf"

headers = {
    "Authorization": "Bearer " + os.environ["NOTION_API_KEY"],
    "Notion-Version": ns.NOTION_VERSION,
    "Content-Type": "application/json",
}
resp = requests.post(
    f"https://api.notion.com/v1/databases/{TEST_DATABASE_ID}/query",
    headers=headers,
    json={},
    timeout=15,
)
print(resp.status_code)
print(resp.text[:500])
