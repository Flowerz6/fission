"""Document layer: format-independent document model and the
``DocumentTarget`` ABC that renders / invokes / tears down per
target product.

Hiding techniques and their citations (see FOUNDATIONS.md):
  - Zero-width / Unicode tag chars.
  - White text / tiny font / off-page text boxes.
  - Comments / footnotes / metadata.
  - Encoded payloads (b64, ROT13) from BoN augmentation set.
  - Tool-response mimicry (InjecAgent, ASB).
  - Markdown image-tag exfiltration (Slack AI / GeminiJack chain).

Each individual hiding technique records its own ``__citations__``; the
package-level list below is the union across all techniques shipped.
"""

from advtest_ext.document.blocks import (
    Block,
    CitationBlock,
    Formatting,
    HeadingBlock,
    HiddenContent,
    ImageBlock,
    Position,
    TableBlock,
    TableCell,
    TextBlock,
    TextBoxBlock,
)
from advtest_ext.document.document import AbstractDocument
from advtest_ext.document.elements import (
    Comment,
    Footnote,
    HeaderFooter,
    Link,
    Reply,
    SuggestedEdit,
)
from advtest_ext.document.target import (
    DocumentCapabilities,
    DocumentTarget,
    NativeContext,
    Response,
    StubDocumentTarget,
)

__citations__: list[str] = [
    "boucher2022badchars",
    "geminijack_2025",
    "promptware_nassi2025",
    "hughes2024bon",
    "slackai_promptarmor_2024",
    "zhan2024injecagent",
    "zhang2025asb",
    "greshake2023ipi",
]

__all__ = [
    "AbstractDocument",
    "Block",
    "CitationBlock",
    "Comment",
    "DocumentCapabilities",
    "DocumentTarget",
    "Footnote",
    "Formatting",
    "HeaderFooter",
    "HeadingBlock",
    "HiddenContent",
    "ImageBlock",
    "Link",
    "NativeContext",
    "Position",
    "Reply",
    "Response",
    "StubDocumentTarget",
    "SuggestedEdit",
    "TableBlock",
    "TableCell",
    "TextBlock",
    "TextBoxBlock",
]
