"""
Memory system: attention window, subconscious fragments,
salience scoring, re-encode booster, decay scheduler,
pattern separation, mismatch detection, hippocampal replay,
and pattern completion.

Implements the full cognitive pipeline stages 5-7 PLUS
hippocampal-subfield mechanisms:
  - DG: PatternSeparator (orthogonalization before storage)
  - CA3: PatternCompleter (associative retrieval)
  - CA1: MismatchDetector (prediction-error salience)
  - SWR: HippocampalReplay (offline consolidation)
"""

from .attention_window import AttentionWindow
from .fragment_store import FragmentStore
from .salience import SalienceScorer
from .reencode import ReencodeBooster
from .decay import MemoryDecayScheduler
from .pattern_separator import PatternSeparator
from .mismatch_detector import MismatchDetector
from .hippocampal_replay import HippocampalReplay
from .pattern_completer import PatternCompleter
from .hooks import MemoryHook

__all__ = [
    "AttentionWindow",
    "FragmentStore",
    "SalienceScorer",
    "ReencodeBooster",
    "MemoryDecayScheduler",
    "PatternSeparator",
    "MismatchDetector",
    "HippocampalReplay",
    "PatternCompleter",
    "MemoryHook",
]
