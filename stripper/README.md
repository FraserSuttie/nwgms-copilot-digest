# stripper/ (content lane, build slices 1 to 8)

Local, deterministic stripper for work communications. Design lives in the NWGMS vault
(`Projects/Comms-Intake-Design.md`); decision record: `docs/decisions/0002-content-lane-local-stripper.md`.
Synthetic data only. Nothing here reads real mail.

| Path | What |
|---|---|
| `corpus/generate.py`, `corpus/vocab.py` | Labelled synthetic corpus (output git-ignored, reproducible from seed); separate regression set from the session 11 leak read (`regression()`, own seed, Directory with external rows): credentials, cued order numbers, doubled-separator dates, external people in the Directory; plus measured limitation kinds (A2: un-cued lowercase patient names and initials, reported not asserted) |
| `detectors.py` | Deterministic detectors; reuses `validator/patterns.py`; credential cue (ADR 0012); cued order numbers and malformed dates (ADR 0013). Returns code and position only |
| `ner.py` | Optional Presidio + spaCy layer; degrades to a stub that says so |
| `namegate.py` | Name gate: directory (NWGMS/MFT rows only, bracketed nicknames: ADR 0014), header participants (Trust display names normalised, both name orders, shared mailboxes exact-only: ADR 0009), bare-name position rules, ignore-word trimming and header first names (ADR 0015), hyphenated ignore words and Surname First order (ADR 0016), `[NAME-n]` numbering, local state |
| `review.py` | Local review-queue CLI for unknown names (run by Fraser only; state outside the repo and OneDrive); hides ignore-file words, marks vocab-only ones (ADR 0016) |
| `transform.py` | Transform engine: placeholders, keyed REF/SMP/WS/QC/CASE tokens, CONTACT/ATT, `[NAME-n]`, `[CLIN]` escalations (ADR 0004). `python stripper/transform.py` prints a counts-only corpus report |
| `keystore.py` | Local key store (DPAPI via ctypes), decode tables, keyed message hash, audit log. State lives in `%LOCALAPPDATA%\nwgms-stripper`, never in the repo or OneDrive |
| `residual.py` | Residual scan (fail-closed, emitted-token check), density check and cue check (`CUE_NAME`: unrecognised word after a patient cue, ADR 0008); `Limits` holds the thresholds (ADR 0005) |
| `quarantine.py` | Whole-message quarantine store in the local state dir; `list` and `purge` CLI, counts only |
| `pipeline.py` | Eligibility, transform, residual scan, density, quarantine. `python stripper/pipeline.py [--ner]` prints a counts-only corpus report |
| `density.py` | Density threshold sweep over the synthetic corpus (`--ner` for the real numbers) |
| `mailfront.py` | Email front end: `nwgms-rawdrop/1` contract check, normaliser (HTML, history, signature, disclaimers, encodings), message record (ADR 0006) |
| `runner.py` | Email batch runner: raw drop to per-thread append-only blocks plus an `_Intake` batch manifest; counts only; NER required; `--caps-limit N` (default 1); `--phrases F` (ADR 0011) |
| `capsscan.py` | Read-only scan of a raw drop through `Pipeline.evaluate` for the caps tools; keeps unknown capitalised words in memory only (ADR 0010) |
| `capsdiag.py` | Count-only DENS_CAPS diagnostic: outcome groups, histogram, release curve by limit, shape and section splits, vocabulary coverage. Prints no word, writes no file |
| `capsvocab.py` | Local vocabulary builder (Fraser only, terminal only): ranked words, flags, allow one at a time, appends to a local `--vocab` file; `--phrases F` for adjacent word pairs; `--list` writes nothing |
| `decode.py` | Local decode CLI (run by Fraser only): tokens or `--thread` lookup table, terminal only, audit line, never message text (ADR 0007) |
| `corpus/rawdrop.py` | Synthetic raw-drop generator and per-block scorer (counts only) |
| `recall.py` | Per-category recall report, counts only (`--ner`, `--gate`); regression set table after the main one (`pipeline.py` prints its pipeline counts too, and released initials, which the name-fragment count cannot see) |

Run from the repo root:

    python -m unittest discover -s stripper/tests -v
    python stripper/recall.py                # deterministic only
    python stripper/recall.py --gate         # + name gate (directory + header rules)
    python stripper/recall.py --ner --gate   # + NER (pip install presidio-analyzer spacy; python -m spacy download en_core_web_lg)

Synthetic email run in PowerShell (folders outside the repo; the state folder must also be outside OneDrive):

    python stripper/corpus/rawdrop.py --dir $env:TEMP\rd
    python stripper/runner.py --drop $env:TEMP\rd\drop --state $env:TEMP\rd-state --out $env:TEMP\rd-out `
        --directory $env:TEMP\rd\directory.md --vocab $env:TEMP\rd\vocab.txt
    python stripper/corpus/rawdrop.py --dir $env:TEMP\rd --score $env:TEMP\rd-out

Decode (PowerShell, in a terminal; redirected output is refused):

    python stripper/decode.py REF-XXXXXXXX CONTACT-0001 ATT-1@<12 hex from the block heading>
    python stripper/decode.py --thread <out>\Comms\_Messages\THR-XXXXXXXX.md

Caps diagnostic and vocabulary builder (PowerShell; `--state` is an EXISTING baseline state, used read-only; both need NER):

    python stripper/capsdiag.py --drop $B\drop --state $B\v070\state --directory $B\directory.md --vocab $B\vocab.txt
    python stripper/capsvocab.py --drop $B\drop --state $B\v070\state --directory $B\directory.md --vocab $B\vocab.txt --list
    python stripper/capsvocab.py --drop $B\drop --state $B\v070\state --directory $B\directory.md --vocab $B\vocab.txt
