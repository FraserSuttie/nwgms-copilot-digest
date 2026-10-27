"""Optional local NER layer (Presidio + spaCy). No network calls at run time.

If presidio-analyzer, spacy or the model are missing, this module degrades to a stub that
returns no hits and says so: check `STATUS` and print it in every report so a missing layer
can never be mistaken for a passing one.

Install (needs network once, then fully offline):
    pip install presidio-analyzer spacy
    python -m spacy download en_core_web_lg      # en_core_web_sm is lighter but much weaker on names
On a machine that blocks the download, install the model wheel from an approved source.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validator"))
import patterns as P  # noqa: E402

MODEL = "en_core_web_lg"  # sm misses many invented/lowercase names; see recall report
_ENTITIES = {"PERSON": "NER_PERSON", "LOCATION": "NER_LOCATION"}
_engine = None
STATUS = "not loaded"


def load(model: str = MODEL) -> bool:
    """Try to load the analyzer. Returns True if the real NER layer is active."""
    global _engine, STATUS
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        cfg = {"nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": model}]}
        nlp = NlpEngineProvider(nlp_configuration=cfg).create_engine()
        _engine = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
        STATUS = f"active (Presidio + spaCy {model})"
        return True
    except Exception as exc:  # noqa: BLE001 - any failure means the layer is a stub
        _engine = None
        STATUS = f"STUB: NER layer not active ({type(exc).__name__})"
        return False


def detect_ner(text: str) -> list[P.Hit]:
    """Return NER hits (people, places) on already-normalised text. Stub returns []."""
    if _engine is None:
        return []
    res = _engine.analyze(text=text, language="en", entities=list(_ENTITIES))
    return [P.Hit(_ENTITIES[r.entity_type], "fail", r.start, r.end) for r in res if r.score >= 0.4]
