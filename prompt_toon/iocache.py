"""Integrity-verified, sha256-keyed condensation cache (TIN-2700, Phase 0).

Cache-first: repeat condensations of identical raw input + settings are
O(1). Entries persist the raw bytes, which restores provenance
re-derivability for cached runs (the manifest sha256 points at bytes that
exist again).

INV-6: the cache lives on a tool-writable filesystem, so nothing is ever
served without authentication. Every entry carries an HMAC over the sha256
digests of its files, keyed by a store-local secret created 0600. An entry
that fails verification is quarantined and treated as a miss; the caller
re-derives.

The measured-savings gate (beats_margin) is the by-construction guarantee
that a condensation round trip can never inflate: callers must pass raw
through unchanged when the condensed form does not beat the policy margin.
"""

from __future__ import annotations

import hmac
import json
import secrets
import shutil
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from .cli import compact_json, rough_token_count, state_root

ENTRY_FILE = "entry.json"
RAW_FILE = "raw.bin"
MAC_FILE = "integrity.hmac"
RESERVED = {ENTRY_FILE, MAC_FILE}


def cache_root() -> Path:
    return state_root() / "cache"


def _secret(root: Path) -> bytes:
    key_path = root / ".key"
    if not key_path.exists():
        root.mkdir(parents=True, exist_ok=True)
        key_path.touch(mode=0o600)
        key_path.write_bytes(secrets.token_bytes(32))
        key_path.chmod(0o600)
    return key_path.read_bytes()


def entry_key(raw: bytes, settings: dict[str, Any]) -> str:
    return f"{sha256(raw).hexdigest()}-{sha256(compact_json(settings).encode('utf-8')).hexdigest()[:16]}"


def _digest_map(entry_dir: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in sorted(entry_dir.iterdir()):
        if path.name == MAC_FILE or not path.is_file():
            continue
        digests[path.name] = sha256(path.read_bytes()).hexdigest()
    return digests


def _mac(secret: bytes, digests: dict[str, str]) -> str:
    canonical = compact_json(digests).encode("utf-8")
    return hmac.new(secret, canonical, sha256).hexdigest()


def store(raw: bytes, settings: dict[str, Any], artifacts: dict[str, str]) -> Path:
    """Persist raw bytes + condensation artifacts under an authenticated entry.

    artifacts maps file names (e.g. summary.md, source-cards.jsonl,
    manifest.json) to text content. Returns the entry directory.
    """
    for name in artifacts:
        if name in RESERVED or name == RAW_FILE or "/" in name:
            raise ValueError(f"reserved or invalid artifact name: {name}")
    root = cache_root()
    secret = _secret(root)
    entry_dir = root / entry_key(raw, settings)
    if entry_dir.exists():
        for child in entry_dir.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
    else:
        entry_dir.mkdir(parents=True, exist_ok=True)

    (entry_dir / RAW_FILE).write_bytes(raw)
    for name, content in artifacts.items():
        (entry_dir / name).write_text(content, encoding="utf-8")
    entry = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "settings": settings,
        "sha256_raw": sha256(raw).hexdigest(),
    }
    (entry_dir / ENTRY_FILE).write_text(
        json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (entry_dir / MAC_FILE).write_text(_mac(secret, _digest_map(entry_dir)), encoding="utf-8")
    return entry_dir


def _quarantine(entry_dir: Path) -> None:
    target = entry_dir.with_name(f"{entry_dir.name}.quarantined-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    try:
        entry_dir.rename(target)
    except OSError:
        pass


def load(raw: bytes, settings: dict[str, Any]) -> dict[str, Any] | None:
    """Return authenticated artifacts for (raw, settings), or None on miss.

    Any verification failure quarantines the entry and reports a miss —
    poisoned entries are never served (INV-6).
    """
    root = cache_root()
    entry_dir = root / entry_key(raw, settings)
    if not entry_dir.is_dir():
        return None
    mac_path = entry_dir / MAC_FILE
    entry_path = entry_dir / ENTRY_FILE
    raw_path = entry_dir / RAW_FILE
    if not (mac_path.is_file() and entry_path.is_file() and raw_path.is_file()):
        _quarantine(entry_dir)
        return None
    secret = _secret(root)
    expected = _mac(secret, _digest_map(entry_dir))
    if not hmac.compare_digest(expected, mac_path.read_text(encoding="utf-8").strip()):
        _quarantine(entry_dir)
        return None
    if sha256(raw_path.read_bytes()).hexdigest() != sha256(raw).hexdigest():
        _quarantine(entry_dir)
        return None
    artifacts = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(entry_dir.iterdir())
        if path.is_file() and path.name not in RESERVED and path.name != RAW_FILE
    }
    return {"dir": entry_dir, "entry": json.loads(entry_path.read_text(encoding="utf-8")), "artifacts": artifacts}


def measured_savings(raw_text: str, condensed_text: str) -> dict[str, Any]:
    raw_tokens = rough_token_count(raw_text)
    condensed_tokens = rough_token_count(condensed_text)
    savings = 0.0 if raw_tokens == 0 else (raw_tokens - condensed_tokens) / raw_tokens
    return {
        "raw_tokens": raw_tokens,
        "condensed_tokens": condensed_tokens,
        "savings": round(savings, 4),
    }


def beats_margin(raw_text: str, condensed_text: str, min_savings: float) -> bool:
    """The by-construction gate: emit condensed only when it beats raw by margin."""
    return measured_savings(raw_text, condensed_text)["savings"] >= min_savings
