from __future__ import annotations

import argparse
import json
import socket
from collections.abc import Callable
from typing import Any

from surf_agent.errors import BridgeUnavailable
from surf_agent.owned_pages import (
    AllocateOwnedPage,
    InspectOwnedPage,
    ObserveOwnedPageAssignment,
    OwnedPageAssignmentObservation,
    OwnedPageAssignmentState,
    OwnedPageCapabilities,
    OwnedPageInspection,
    OwnedPageInspectionState,
    OwnedPagePromptSubmission,
    OwnedPageProtection,
    OwnedPageRef,
    OwnedPageSubmissionAlreadyAttempted,
    OwnedPagePreparationState,
    OwnedPageSubmissionPreparation,
    OwnedPageSubmissionState,
    PrepareOwnedPageSubmission,
    ProtectOwnedPage,
    RebindOwnedPage,
    SubmitOwnedPagePrompt,
)
from surf_chatgpt import cli
from surf_chatgpt.errors import SubmissionPhase
from surf_chatgpt.session_lifecycle import OwnedPageSessionLifecycle


class JsonLineChannel:
    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection
        self._reader = connection.makefile("rb")

    def send(self, message: dict[str, object]) -> None:
        encoded = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        self._connection.sendall(encoded)

    def receive(self) -> dict[str, Any]:
        try:
            line = self._reader.readline()
        except OSError as error:
            raise BridgeUnavailable("The scripted bridge disconnected.") from error
        if not line:
            raise BridgeUnavailable("The scripted bridge disconnected.")
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise BridgeUnavailable("The scripted bridge returned an invalid response.")
        return decoded


class RemoteOwnedPageBridge:
    def __init__(self, channel: JsonLineChannel) -> None:
        self._channel = channel

    def capabilities(self) -> OwnedPageCapabilities:
        return OwnedPageCapabilities.complete()

    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        response = self._call(
            "allocate",
            thread=request.thread,
            url=request.url,
            protection=_protection_value(request.protection),
        )
        return _page(response)

    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        response = self._call("inspect", thread=request.thread)
        return OwnedPageInspection(
            _page(response),
            OwnedPageInspectionState(response["state"]),
        )

    def prepare_submission(
        self,
        request: PrepareOwnedPageSubmission,
    ) -> OwnedPageSubmissionPreparation:
        response = self._call(
            "prepare_submission",
            thread=request.thread,
            page_token=request.expected_page_token,
        )
        return OwnedPageSubmissionPreparation(
            _page(response),
            state=OwnedPagePreparationState(response["state"]),
        )

    def submit_prompt(
        self,
        request: SubmitOwnedPagePrompt,
        *,
        on_send_may_have_occurred: Callable[[], None],
    ) -> OwnedPagePromptSubmission:
        response = self._call(
            "submit_prompt",
            on_request_may_have_been_dispatched=on_send_may_have_occurred,
            thread=request.thread,
            page_token=request.expected_page_token,
        )
        return OwnedPagePromptSubmission(
            _page(response),
            OwnedPageSubmissionState(response["state"]),
        )

    def observe_assignment(
        self,
        request: ObserveOwnedPageAssignment,
    ) -> OwnedPageAssignmentObservation:
        response = self._call(
            "observe_assignment",
            thread=request.thread,
            page_token=request.expected_page_token,
        )
        return OwnedPageAssignmentObservation(
            _page(response),
            OwnedPageAssignmentState(response["state"]),
            response.get("session_id"),
        )

    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef:
        response = self._call(
            "rebind",
            source_thread=request.source_thread,
            destination_thread=request.destination_thread,
            page_token=request.expected_page_token,
            expected_exact_url=request.expected_exact_url,
        )
        return _page(response)

    def protect(self, request: ProtectOwnedPage) -> None:
        self._call(
            "protect",
            thread=request.thread,
            page_token=request.expected_page_token,
            expected_protection=_protection_value(request.expected_protection),
            protection=_protection_value(request.protection),
        )

    def _call(
        self,
        operation: str,
        *,
        on_request_may_have_been_dispatched: Callable[[], None] | None = None,
        **fields: object,
    ) -> dict[str, Any]:
        try:
            self._channel.send({"operation": operation, **fields})
            if on_request_may_have_been_dispatched is not None:
                on_request_may_have_been_dispatched()
            response = self._channel.receive()
        except (BrokenPipeError, ConnectionError, OSError) as error:
            raise BridgeUnavailable("The scripted bridge disconnected.") from error
        if response.get("error") == "submission_already_attempted":
            raise OwnedPageSubmissionAlreadyAttempted
        if response.get("ok") is not True:
            raise RuntimeError("The scripted bridge returned an invalid response.")
        return response


def _page(response: dict[str, Any]) -> OwnedPageRef:
    return OwnedPageRef(
        thread=response["thread"],
        page_token=response["page_token"],
        exact_url=response["url"],
    )


def _protection_value(protection: OwnedPageProtection | None) -> str | None:
    return protection.value if protection is not None else None


def _phase_observer(
    channel: JsonLineChannel,
    target: str | None,
) -> Callable[[SubmissionPhase], None]:
    def observe(phase: SubmissionPhase) -> None:
        if phase.value != target or phase is SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
            return
        channel.send({"phase": phase.value})
        channel.receive()

    return observe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bridge_fd", type=int)
    parser.add_argument("control_fd", type=int)
    parser.add_argument("barrier")
    parser.add_argument("cli_argv_json")
    args = parser.parse_args()

    target = None if args.barrier == "none" else args.barrier
    bridge_socket = socket.socket(fileno=args.bridge_fd)
    control_socket = socket.socket(fileno=args.control_fd)
    bridge = RemoteOwnedPageBridge(JsonLineChannel(bridge_socket))
    control = JsonLineChannel(control_socket)
    lifecycle = OwnedPageSessionLifecycle(
        bridge,
        submission_thread_factory=lambda: "surf-chatgpt-submit-subprocess",
        phase_observer=_phase_observer(control, target),
    )
    cli_argv = json.loads(args.cli_argv_json)
    if not isinstance(cli_argv, list) or not all(
        isinstance(argument, str) for argument in cli_argv
    ):
        raise ValueError("CLI arguments must be a JSON string array.")
    return cli.main(cli_argv, lifecycle=lifecycle)


if __name__ == "__main__":
    raise SystemExit(main())
