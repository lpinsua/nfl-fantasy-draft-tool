"""Test fixtures.

These live in ``draftkit.demo`` so that ``--demo`` mode and the test suite
exercise exactly the same synthetic data, instead of two generators that
quietly drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from draftkit.demo import (  # noqa: F401  (re-exported for tests)
    ADP_BASE,
    ADP_STEP,
    HALF_PPR_SCORING,
    POS_SHAPE,
    TEAMS,
    DemoClient,
    make_draft,
    make_league,
    make_picks,
    make_players,
    make_projections,
)
