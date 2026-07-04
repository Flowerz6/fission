"""Attack guides: the normalized steering docs the LLM attacker consumes, plus
the normalizers that convert heterogeneous source datasets into them.

See `docs/FISSION_GUIDE_FORMAT.md`.
"""

from advtest_ext.guides.corpus import load_guide, load_guides
from advtest_ext.guides.models import (
    Guide,
    GuideProvenance,
    GuideSuccess,
    GuideTarget,
    content_hash,
)
from advtest_ext.guides.normalize import (
    LlmNormalizer,
    Normalizer,
    PassthroughNormalizer,
)

__all__ = [
    "Guide",
    "GuideProvenance",
    "GuideSuccess",
    "GuideTarget",
    "LlmNormalizer",
    "Normalizer",
    "PassthroughNormalizer",
    "content_hash",
    "load_guide",
    "load_guides",
]
