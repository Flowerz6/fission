"""Corpus seeding — the API-poisoning half of retrieval IPI.

Plants poisoned artifacts (emails, Docs) into a controlled
Workspace account so a later Playwright retrieval trigger can ask
Gemini to summarise them and observe whether the hidden injection
fires. See :mod:`advtest_ext.corpus.artifact` for the threat-model
overview and the two-token (``unique_entity`` / ``canary``)
correlation scheme.

Pairs with:
* :mod:`advtest_ext.adapters.google_docs.ui_mode.retrieval` — the
  firing half (drives the Gemini side panel against a fresh doc).
* :class:`advtest_ext.judges.signals.canary_appears.CanaryAppears`
  — the scoring half (detects the canary in the response).
"""

from advtest_ext.corpus.artifact import (
    PlantedArtifact,
    make_canary,
    make_unique_entity,
)
from advtest_ext.corpus.docs_seeder import DocsCorpusSeeder
from advtest_ext.corpus.gmail_seeder import HIDDEN_CHANNELS, GmailCorpusSeeder
from advtest_ext.corpus.indexing import (
    drive_search_probe,
    gmail_search_probe,
    wait_until_retrievable,
)

__all__ = [
    "HIDDEN_CHANNELS",
    "DocsCorpusSeeder",
    "GmailCorpusSeeder",
    "PlantedArtifact",
    "drive_search_probe",
    "gmail_search_probe",
    "make_canary",
    "make_unique_entity",
    "wait_until_retrievable",
]
