"""Production security helpers shared by HTTP and document adapters."""
from __future__ import annotations
import re,secrets
from pathlib import Path
from apex_documents import validate_file, MAX_FILE_BYTES

class SecurityError(ValueError): pass

def secure_upload(path, destination, max_bytes=MAX_FILE_BYTES):
    source=validate_file(path)
    if source.stat().st_size>max_bytes: raise SecurityError("Upload exceeds the configured size limit.")
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    # Never preserve a user-controlled path; random prefix avoids collisions.
    safe_name = source.name.replace("/", "_").replace("\\", "_")
    name=f"{secrets.token_hex(8)}-{safe_name}"
    target=(destination/name).resolve()
    if destination.resolve() not in target.parents: raise SecurityError("Unsafe upload path.")
    target.write_bytes(source.read_bytes())
    return target

def security_headers():
    return {"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"no-referrer","Content-Security-Policy":"default-src 'self'; frame-ancestors 'none'"}

def redact_secret(value):
    if not value:return value
    return value[:3]+"…" if len(value)>3 else "…"

def admin_required(user: dict):
    if not user or user.get("role")!="admin": raise SecurityError("Administrator permission required.")
    return True
