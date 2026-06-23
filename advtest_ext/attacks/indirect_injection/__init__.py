"""Indirect-injection attack module — 25 hiding techniques + the
:class:`IndirectInjectionAttack` plugin.

This is the most load-bearing module in advtest (FOUNDATIONS.md
"Hiding techniques" + "open problems" #2 + #3). The 25-technique x
20-payload cross-product is what the demo heatmap plots.

Citation discipline: every technique declares its own
``references`` list (bib keys from ``references.bib``). The four
synthesised techniques (no published citation) declare an empty list
and explicitly say so in their docstrings; tests/test_indirect_injection
locks both the empty and non-empty cases.
"""

from advtest_ext.attacks.indirect_injection.attack import IndirectInjectionAttack
from advtest_ext.attacks.indirect_injection.base import (
    HidingTechniqueImpl,
    encode_base64,
    encode_homoglyph,
    encode_leetspeak,
    encode_rot13,
    encode_unicode_tag,
    encode_zero_width,
    technique_supported,
)
from advtest_ext.attacks.indirect_injection.techniques import (
    CitationBlockCarrier,
    CommentReplyThread,
    CommentThread,
    CrossDocumentChain,
    DocumentMetadata,
    EncodedPayload,
    FontColorNearBackground,
    FootnoteCarrier,
    HeaderFooterCarrier,
    ImageAltText,
    ImageRenderingExfil,
    LinkedContentCarrier,
    MarkdownInjection,
    Multilingual,
    RoleConfusion,
    SuggestedEditCarrier,
    TableCellHidden,
    TableOfContentsCarrier,
    TextBoxOffscreen,
    TinyFont,
    ToolFormatMimicry,
    UnicodeHomoglyph,
    UnicodeTagChars,
    WhiteText,
    ZeroWidthChars,
    build_default_techniques,
)

__citations__: list[str] = [
    "greshake2023ipi",
    "geminijack_2025",
    "promptware_nassi2025",
    "boucher2022badchars",
    "hughes2024bon",
    "zhan2024injecagent",
    "zhang2025asb",
    "slackai_promptarmor_2024",
    "gemini_trifecta_2025",
    "mazeika2024harmbench",
]

__all__ = [
    "CitationBlockCarrier",
    "CommentReplyThread",
    "CommentThread",
    "CrossDocumentChain",
    "DocumentMetadata",
    "EncodedPayload",
    "FontColorNearBackground",
    "FootnoteCarrier",
    "HeaderFooterCarrier",
    "HidingTechniqueImpl",
    "ImageAltText",
    "ImageRenderingExfil",
    "IndirectInjectionAttack",
    "LinkedContentCarrier",
    "MarkdownInjection",
    "Multilingual",
    "RoleConfusion",
    "SuggestedEditCarrier",
    "TableCellHidden",
    "TableOfContentsCarrier",
    "TextBoxOffscreen",
    "TinyFont",
    "ToolFormatMimicry",
    "UnicodeHomoglyph",
    "UnicodeTagChars",
    "WhiteText",
    "ZeroWidthChars",
    "build_default_techniques",
    "encode_base64",
    "encode_homoglyph",
    "encode_leetspeak",
    "encode_rot13",
    "encode_unicode_tag",
    "encode_zero_width",
    "technique_supported",
]
