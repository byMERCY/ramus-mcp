"""Import this first in a test module: puts src/ on the path and finds the sample models.

The sample models are the ones Ramus ships with. They are not part of this repository, so the
tests that need them skip when they are not installed; set RAMUS_SAMPLES to point at a
different copy of Ramus's ``doc`` folder.
"""

import os
import sys
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

SAMPLE_DIR = os.environ.get("RAMUS_SAMPLES", r"D:\Ramus\doc")


def sample(*parts: str) -> Optional[str]:
    """Path of a Ramus sample model, or None if it is not installed here."""
    path = os.path.join(SAMPLE_DIR, *parts)
    return path if os.path.isfile(path) else None


MODEL_EXAMPLE = sample("ru", "Model example.rsf")
ENTERPRISE = sample("en", "Enterprise activity.rsf")
