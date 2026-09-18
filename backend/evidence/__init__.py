"""Evidence engine: reference helpers, validation, assembly and confidence policy."""
from backend.evidence.evidence_engine import Assembled, assemble, assemble_items, confidence_for
from backend.evidence.record_refs import dedupe_sorted, doc_ref, rec_ref, ref_dict, ref_tuple
from backend.evidence.validator import ValidationResult, validate, validate_refs

__all__ = [
    "Assembled", "assemble", "assemble_items", "confidence_for",
    "dedupe_sorted", "doc_ref", "rec_ref", "ref_dict", "ref_tuple",
    "ValidationResult", "validate", "validate_refs",
]
