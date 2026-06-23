"""Shared types for the corpus seeder — the API-poisoning half of
retrieval IPI.

Direct IPI puts the malicious content in the document the user
deliberately opens. *Retrieval* IPI plants it in the user's
Workspace history (an email, an old Doc) and lets Gemini's
cross-surface retrieval pull it into context when the user later
asks a routine question against a *clean* document. The seeder is
the planting half; the Playwright retrieval trigger
(``adapters/google_docs/ui_mode/retrieval.py``) is the firing half.

A :class:`PlantedArtifact` is the handle the seeder returns: enough
to (a) drive natural-language retrieval at it (``unique_entity``),
(b) detect the injection firing (``canary``), (c) open it for
forensics (``url``), and (d) tear it down (``kind`` + ``artifact_id``).

Two correlation tokens, deliberately distinct:

* ``unique_entity`` — a plausible *topic* string ("Project 9F2C1A")
  embedded in the artifact's visible body so a natural-language
  prompt ("summarise my email about Project 9F2C1A") retrieves
  *this* artifact and not something else. It is the retrieval
  handle and is meant to be visible.
* ``canary`` — a SHOUTY uniquely-shaped token the *injection*
  instructs the model to emit. It never appears in the visible
  body, so its presence in the response is proof the hidden
  instruction was followed. Feeds straight into the existing
  :class:`~advtest_ext.judges.signals.canary_appears.CanaryAppears`
  signal.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class PlantedArtifact:
    """Handle to one poisoned artifact living in the victim corpus.

    Returned by every ``*.plant(...)`` call and consumed by the
    matching ``*.teardown(artifact)``. Frozen so a run can stash a
    list of them and tear down in a ``finally`` without worrying
    about mutation.
    """

    kind: str
    """``"gmail"`` or ``"gdoc"`` — selects the teardown path."""

    artifact_id: str
    """Gmail message id / Drive file id. The teardown key."""

    canary: str
    """Token the injection tells the model to emit. Proof-of-firing."""

    unique_entity: str
    """Visible topic string for natural-language (Tier-2) retrieval."""

    title: str
    """Subject line (email) / document title. Human-readable label,
    and the string a Tier-1 ``@``-mention references."""

    url: str | None = None
    """Human-openable URL for forensics. ``None`` when the surface
    has no stable deep-link."""

    hidden_channel: str = ""
    """Which carrier channel hid the injection (e.g. ``"plain"``,
    ``"html_whitetext"``, ``"white_text"``). Recorded so a run can
    report ASR *per channel* — the whole point of the exercise."""


def make_canary(prefix: str = "ADVTEST-CANARY") -> str:
    """Mint a uniquely-shaped canary token.

    SHOUTY-CASE with a hex suffix so a case-sensitive
    ``CanaryAppears`` can't false-positive on lowercase prose, and
    so it's visually obvious in a captured response. Example:
    ``ADVTEST-CANARY-3F9A2B7C4D1E``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def make_unique_entity(prefix: str = "Project") -> str:
    """Mint a plausible-looking topic string for Tier-2 retrieval.

    Reads like a project codename so it sits naturally in an email
    or doc body and in the retrieval prompt ("summarise my notes on
    Project 9F2C1A"). Short hex keeps it memorable for a live demo.
    """
    return f"{prefix} {uuid.uuid4().hex[:6].upper()}"


__all__ = ["PlantedArtifact", "make_canary", "make_unique_entity"]
