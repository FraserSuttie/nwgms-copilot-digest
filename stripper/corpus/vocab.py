"""Invented vocabulary for the synthetic corpus. Every person here is fictional.

Given names are common words from a generic pool; surnames are invented. Staff are
split into a synthetic "directory" (pass-through) and non-directory people.
"""

PATIENT_FIRST = ["Marlowe", "Odette", "Brannock", "Tamsin", "Ignatius", "Perdita", "Corwin", "Linnea", "Osric", "Wynne"]
PATIENT_LAST = ["Aldermoor", "Thornquist", "Pellowick", "Vandersloot", "Quillfeather", "Harcastle", "Dunmarrow", "Ostrelby", "Fenwhistle", "Calderbeck"]
EXT_FIRST = ["Rosalind", "Barnaby", "Philippa", "Evander", "Marguerite", "Lucian", "Isolde", "Tobias"]
EXT_LAST = ["Wetherbourne", "Ashgrove-Lyle", "Merriwick", "Stansbury-Cole", "Hollowell", "Pennyfeather", "Cragmoor", "Abernethy-Roe"]
STAFF_DIR = ["Ada Fairweather", "Callum Ridgeway", "Priya Lindqvist", "Oren Maddox", "Beatrix Holloway", "Ewan Tallis"]
DIRECTORY_EXTRA = ["Imogen Ridgeway"]  # shares a surname with Callum Ridgeway: bare "Ridgeway" is ambiguous
STAFF_NON_DIR = ["Hester Brindle", "Dominic Aspinwall", "Sunita Marchbanks", "Rory Quennell", "Zoe Kettleby"]
NICKNAMES = {"Marlowe": "Marly", "Odette": "Dettie", "Brannock": "Bran", "Tamsin": "Tammy", "Ignatius": "Iggy"}

STREETS = ["Brackenmoor", "Linnet", "Copperbeech", "Wrenfield", "Saltmarsh", "Harrowgate", "Mossgill"]
STREET_TYPES = ["Road", "Street", "Lane", "Avenue", "Close", "Drive", "Way", "Gardens"]
EMAIL_DOMAINS = ["example.com", "example.org", "example.net"]
TRUST_DOMAINS = ["nhs.net", "mft.nhs.uk"]

# Public gene / panel / test names: configuration vocabulary that must be KEPT.
CONFIG_VOCAB = ["BRCA1", "BRCA2", "TP53", "JAK2", "CFTR", "MLH1", "Cancer Panel R208", "Haem Onc panel", "CRF-2026-019", "DOC6676"]

# Placeholder lexicon (to be replaced by a file derived from the NGTD reference).
DIAGNOSES = ["breast cancer", "ovarian cancer", "acute myeloid leukaemia", "cystic fibrosis", "Lynch syndrome", "polycythaemia vera"]
RESULT_WORDS = ["positive", "not detected", "detected", "pathogenic", "likely pathogenic", "carrier"]

BENIGN = [
    "Thanks for the positive feedback on the release notes.",
    "The meeting is booked for 30 October 2026 in the usual room.",
    "Please review the change request before the next board.",
    "We expect the interface test to be negative for regressions.",
    "Server maintenance is planned for 14/11/2026 overnight.",
    "Can you confirm the Test environment refresh date?",
    "I will chase the supplier and update the ticket.",
]
