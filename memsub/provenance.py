"""Cryptographic channel provenance: HMAC-signed write-channel tags.

SMSR (arXiv:2606.12703) proves a formal blind spot in any provenance-free,
content-based heuristic: an adversary who disguises a poisoned payload as an
observed fact can always defeat a rule that only looks at what a memory's
content or `kind` claims about itself (memsub measured this blind spot
empirically, see docs/poisoning-benchmark.md's Attack B: 98.7% ASR, the
observed-beats-inferred rule contributes nothing). The fix here is to stop
trusting content and start recording, at write time, which code path actually
produced the memory -- a fact the content can never forge because the content
never controls it.

Three channels, one per class of write path memsub actually has:

  user_stated   a human directly said or wrote this (a real transcript turn
                extracted as an observed claim, a vault human correction, a
                promoted human note).
  llm_inferred  a model derived this rather than observing it (memsub.sleep's
                inference pass).
  imported      written through a generic entry point (memsub.sdk.MemoryClient
                .add, the MCP add_memory tool) that has no pipeline signal
                about whether the content was actually observed.

Every new episode is tagged with its channel and an HMAC-SHA256 signature over
(content, channel, timestamp, namespace), keyed by a secret generated on first
use and stored at ~/.memsub/provenance.key (0600, never committed). At
consolidation time the signature is re-verified; a row whose signature does
not check out is quarantined, never trusted, never silently deleted.

Migration: episodes written before this module existed carry no `channel` /
`channel_sig` provenance keys at all. They are not quarantined (nothing was
tampered, the field simply predates this scheme) but they are also not
verifiable, so they are graded "unsigned-legacy": still retrievable, but
memsub.consolidate's trust ranking never lets an unsigned-legacy row outrank a
signed user_stated row. See docs/poisoning-provenance-2026-07-17.md.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from .store import Episode

CHANNEL_USER_STATED = "user_stated"
CHANNEL_LLM_INFERRED = "llm_inferred"
CHANNEL_IMPORTED = "imported"
VALID_CHANNELS = frozenset({CHANNEL_USER_STATED, CHANNEL_LLM_INFERRED, CHANNEL_IMPORTED})

STATUS_VERIFIED = "verified"
STATUS_UNSIGNED_LEGACY = "unsigned_legacy"
STATUS_QUARANTINED = "quarantined"

_SEP = "\x1f"  # unit separator: won't appear in memory text, avoids field-concat ambiguity
_KEY_BYTES = 32


def default_key_path() -> Path:
    env = os.environ.get("MEMSUB_PROVENANCE_KEY_PATH")
    if env:
        return Path(env)
    return Path.home() / ".memsub" / "provenance.key"


def load_or_create_key(path: str | Path | None = None) -> bytes:
    """Load the local signing secret, generating and persisting one (0600) on
    first use. Called only from write paths (tagging a new episode); read
    paths must use load_key so a missing key never gets silently created just
    by verifying something."""
    p = Path(path) if path else default_key_path()
    if p.exists():
        return p.read_bytes()
    p.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(_KEY_BYTES)
    try:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # lost a create race to another process: use what it wrote
        return p.read_bytes()
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def load_key(path: str | Path | None = None) -> bytes | None:
    """Load the signing secret for verification, or None if it does not
    exist. Never creates one: a read/consolidation path must fail safe (treat
    every claimed-signed row as unverifiable) rather than conjure a key that
    could never match anything actually signed at write time."""
    p = Path(path) if path else default_key_path()
    try:
        return p.read_bytes()
    except FileNotFoundError:
        return None


def _message(content: str, channel: str, ts: float, namespace: str) -> bytes:
    return _SEP.join([content, channel, repr(ts), namespace]).encode("utf-8")


def sign(content: str, channel: str, ts: float, namespace: str, key: bytes) -> str:
    return hmac.new(key, _message(content, channel, ts, namespace), hashlib.sha256).hexdigest()


def tag_episode(episode: Episode, channel: str, key: bytes | None = None) -> Episode:
    """Return a copy of `episode` carrying a channel tag and its HMAC
    signature. Finalizes id/ts first (via with_defaults) so the signature
    covers exactly the (content, channel, ts, namespace) that ends up in the
    store; with_defaults is idempotent, so this is safe to call before or
    after id/ts are otherwise set."""
    if channel not in VALID_CHANNELS:
        raise ValueError(f"unknown channel: {channel!r}")
    key = key if key is not None else load_or_create_key()
    ep = episode.with_defaults()
    sig = sign(ep.content, channel, ep.ts, ep.namespace, key)
    provenance = dict(ep.provenance)
    provenance["channel"] = channel
    provenance["channel_sig"] = sig
    return Episode(
        content=ep.content,
        run_id=ep.run_id,
        session_id=ep.session_id,
        kind=ep.kind,
        importance=ep.importance,
        provenance=provenance,
        namespace=ep.namespace,
        ts=ep.ts,
        id=ep.id,
        # bi-temporal fields must survive signing: dropping them here
        # silently destroyed every signed episode's validity window until
        # the 2026-07-21 timeline live-probe caught it
        valid_from=ep.valid_from,
        valid_to=ep.valid_to,
    )


def channel_status(ep: Episode, key: bytes | None) -> tuple[str, str | None]:
    """Verify an episode's channel signature.

    Returns (status, claimed_channel). claimed_channel is None only for
    unsigned-legacy rows (nothing to claim). A channel value outside
    VALID_CHANNELS, or a signature that does not match, or a key that is
    unavailable to check against, all quarantine the row -- verification
    failing open (trusting an unverifiable claim) is exactly the mistake this
    module exists to avoid.
    """
    channel = ep.provenance.get("channel")
    sig = ep.provenance.get("channel_sig")
    if channel is None or sig is None:
        return STATUS_UNSIGNED_LEGACY, None
    if channel not in VALID_CHANNELS:
        return STATUS_QUARANTINED, channel
    if key is None:
        # fail safe: a claimed signature we cannot check is not a verified one
        return STATUS_QUARANTINED, channel
    expected = sign(ep.content, channel, ep.ts, ep.namespace, key)
    if hmac.compare_digest(expected, sig):
        return STATUS_VERIFIED, channel
    return STATUS_QUARANTINED, channel
