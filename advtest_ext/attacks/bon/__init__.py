"""Best-of-N Jailbreaking (Hughes et al. 2024).

Reference: Hughes, John et al. "Best-of-N Jailbreaking." 2024. See
FOUNDATIONS.md ("Attack techniques: Ship these"); the power-law
scaling and 28x sample-efficiency-via-MSJ-composition are pitch-deck
material.
"""

from advtest_ext.attacks.bon.attack import BonAttack
from advtest_ext.attacks.bon.augmentations import (
    DEFAULT_TEXT_AUGMENTATIONS,
    TextAugmentation,
    audio_noise_inject,
    audio_pitch_shift,
    audio_speed_perturb,
    base64_wrap,
    character_noise,
    character_shuffle,
    image_color_jitter,
    image_position_shift,
    punctuation_insertion,
    random_capitalisation,
    rot13_wrap,
)
from advtest_ext.attacks.bon.generator import BonGenerator, BonVariant

__citations__: list[str] = ["hughes2024bon"]

__all__ = [
    "DEFAULT_TEXT_AUGMENTATIONS",
    "BonAttack",
    "BonGenerator",
    "BonVariant",
    "TextAugmentation",
    "audio_noise_inject",
    "audio_pitch_shift",
    "audio_speed_perturb",
    "base64_wrap",
    "character_noise",
    "character_shuffle",
    "image_color_jitter",
    "image_position_shift",
    "punctuation_insertion",
    "random_capitalisation",
    "rot13_wrap",
]
