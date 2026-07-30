from __future__ import annotations

from surf_agent.owned_pages import (
    AllocateOwnedPage,
    InspectOwnedPage,
    OwnedPageBridge,
    OwnedPageInspection,
    OwnedPageInspectionFailed,
    OwnedPageNotFound,
    OwnedPageOwnershipConflict,
    OwnedPageProtection,
    OwnedPageRef,
    OwnedPageScope,
    UnsupportedOwnedPageCapability,
)
from surf_agent.errors import BridgeIdentityUnproven, SurfAgentError

from .dom.readiness import CURRENT_SESSION_CLASSIFIER
from .errors import PublicError, PublicErrorType


SURF_CHATGPT_OWNER = "surf-chatgpt"
LOGIN_THREAD = "surf-chatgpt-login"
CHATGPT_HOME_URL = "https://chatgpt.com/"


class ChatGptOwnedPages:
    def __init__(self, bridge: OwnedPageBridge) -> None:
        self._bridge = bridge

    def prepare_login(self) -> OwnedPageRef:
        self._require_capabilities()
        try:
            return self._bridge.allocate(
                AllocateOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    thread=LOGIN_THREAD,
                    url=CHATGPT_HOME_URL,
                    allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
                    expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
                    protection=OwnedPageProtection.HUMAN_INTERVENTION,
                )
            )
        except UnsupportedOwnedPageCapability as error:
            raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY) from error
        except OwnedPageOwnershipConflict as error:
            raise PublicError(PublicErrorType.OWNERSHIP_CONFLICT) from error
        except BridgeIdentityUnproven as error:
            raise PublicError(PublicErrorType.BROWSER_IDENTITY_UNPROVEN) from error
        except SurfAgentError as error:
            raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error

    def inspect_thread(self, thread: str) -> OwnedPageInspection:
        self._require_capabilities()
        try:
            return self._bridge.inspect(
                InspectOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    thread=thread,
                    allowed_scope=OwnedPageScope.CHATGPT,
                    classifier=CURRENT_SESSION_CLASSIFIER,
                )
            )
        except UnsupportedOwnedPageCapability as error:
            raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY) from error
        except OwnedPageNotFound as error:
            raise PublicError(PublicErrorType.THREAD_NOT_FOUND) from error
        except OwnedPageOwnershipConflict as error:
            raise PublicError(PublicErrorType.OWNERSHIP_CONFLICT) from error
        except OwnedPageInspectionFailed as error:
            raise PublicError(PublicErrorType.INSPECTION_FAILED) from error
        except BridgeIdentityUnproven as error:
            raise PublicError(PublicErrorType.BROWSER_IDENTITY_UNPROVEN) from error
        except SurfAgentError as error:
            raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error

    def _require_capabilities(self) -> None:
        if not self._bridge.capabilities().supported:
            raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY)
