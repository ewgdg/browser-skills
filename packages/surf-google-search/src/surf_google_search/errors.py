from __future__ import annotations

from enum import StrEnum


class PublicErrorType(StrEnum):
    INVALID_REQUEST = "invalid_request"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    HUMAN_INTERVENTION_REQUIRED = "human_intervention_required"
    UI_CHANGED = "ui_changed"
    INTERNAL_ERROR = "internal_error"


_ERROR_DETAILS = {
    PublicErrorType.INVALID_REQUEST: (
        "The search request is invalid.",
        "Use --help to inspect the supported command grammar.",
    ),
    PublicErrorType.BROWSER_UNAVAILABLE: (
        "The browser bridge or page is unavailable.",
        "Start or repair the selected Surf backend, then retry.",
    ),
    PublicErrorType.HUMAN_INTERVENTION_REQUIRED: (
        "Google requires user intervention.",
        "Complete the browser action, then retry using the preserved thread.",
    ),
    PublicErrorType.UI_CHANGED: (
        "The required Google Search interface could not be identified.",
        "Update surf-google-search for the current Google interface before retrying.",
    ),
    PublicErrorType.INTERNAL_ERROR: (
        "An internal surf-google-search error occurred.",
        "Retry once; if the failure persists, update surf-google-search.",
    ),
}


class PublicError(Exception):
    def __init__(self, error_type: PublicErrorType) -> None:
        self.type = error_type
        super().__init__(_ERROR_DETAILS[error_type][0])

    def to_public_json(self) -> dict[str, str]:
        message, hint = _ERROR_DETAILS[self.type]
        return {"type": self.type.value, "message": message, "hint": hint}
