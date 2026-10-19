"""Netlify build step: write config.js pointing the frontend at the API.

Reads NIROG_API_BASE (e.g. https://nirog-api.onrender.com, no trailing /api).
Without it, config.js leaves the default of http://127.0.0.1:5000 in place.
"""
import json
import os
from pathlib import Path

base = os.environ.get("NIROG_API_BASE", "").strip().rstrip("/")
if base.endswith("/api"):
    base = base[: -len("/api")]

out = Path(__file__).with_name("config.js")
if base:
    out.write_text(f"window.NIROG_API_BASE = {json.dumps(base)};\n")
    print(f"config.js -> {base}")
else:
    out.write_text("/* NIROG_API_BASE not set; using the local default. */\n")
    print("NIROG_API_BASE not set; frontend will call http://127.0.0.1:5000")
