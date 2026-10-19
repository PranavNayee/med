
"""Netlify build step: write config.js pointing the frontend at the API.

Uses the correct production Render backend URL by default.
An optional NIROG_API_BASE environment variable can override it.
"""

import json
import os
from pathlib import Path

DEFAULT_API_BASE = "https://nirog-api-4k56.onrender.com"

base = (
    os.environ.get("NIROG_API_BASE", "").strip()
    or DEFAULT_API_BASE
)
base = base.rstrip("/")

# Remove /api if accidentally included.
if base.endswith("/api"):
    base = base[:-4]

out = Path(__file__).with_name("config.js")
out.write_text(
    f"window.NIROG_API_BASE = {json.dumps(base)};\n",
    encoding="utf-8",
)

print(f"NIROG_API_BASE configured: {base}")
print(f"Generated config.js at: {out}")
