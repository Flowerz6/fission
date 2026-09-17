"""Attack guides: the normalized steering docs the LLM attacker consumes, plus
the normalizers that convert heterogeneous source datasets into them.

See `docs/FISSION_GUIDE_FORMAT.md`.
"""

from advtest_ext.guides.corpus import load_guide, load_guides
from advtest_ext.guides.models import (
    Guide,
    GuideMutationOperator,
    GuideProvenance,
    GuideSetupVerification,
    GuideSuccess,
    GuideTarget,
    content_hash,
)
from advtest_ext.guides.mutation_runtime import (
    GuideMutationMaterializer,
    MaterializedMutation,
)
from advtest_ext.guides.normalize import (
    LlmNormalizer,
    Normalizer,
    PassthroughNormalizer,
)

__all__ = [
    "Guide",
    "GuideMutationMaterializer",
    "GuideMutationOperator",
    "GuideProvenance",
    "GuideSetupVerification",
    "GuideSuccess",
    "GuideTarget",
    "LlmNormalizer",
    "MaterializedMutation",
    "Normalizer",
    "PassthroughNormalizer",
    "content_hash",
    "load_guide",
    "load_guides",
]
