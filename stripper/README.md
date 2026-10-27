# stripper/ (content lane, build slice 1)

Local, deterministic stripper for work communications. Design lives in the NWGMS vault
(`Projects/Comms-Intake-Design.md`); decision record: `docs/decisions/0002-content-lane-local-stripper.md`.
Synthetic data only. Nothing here reads real mail.

| Path | What |
|---|---|
| `corpus/generate.py`, `corpus/vocab.py` | Labelled synthetic corpus (output git-ignored, reproducible from seed) |
| `detectors.py` | Deterministic detectors; reuses `validator/patterns.py`. Returns code and position only |
| `ner.py` | Optional Presidio + spaCy layer; degrades to a stub that says so |
| `recall.py` | Per-category recall report, counts only |

Run from the repo root:

    python -m unittest discover -s stripper/tests -v
    python stripper/recall.py                # deterministic only
    python stripper/recall.py --ner          # + NER (pip install presidio-analyzer spacy; python -m spacy download en_core_web_lg)

Not built yet: name gate and review queue, transforms/tokens, residual rescan, quarantine, key store.
