from __future__ import annotations

from typing import Any, Callable

from ..constants import CAMOUFOX_BACKEND, DEFAULT_BACKEND, PATCHRIGHT_BACKEND
from ..errors import BridgeUnavailable, SurfAgentError
from .axi import (
    AxiBackend,
    AxiBridgeClient,
    AxiBridgeConfigMismatch,
    AxiBridgeUnavailable,
    extract_page_id,
    find_page,
    format_bridge_eval_result,
    format_bridge_text_result,
    is_no_pages_output,
    is_surf_agent_bootstrap_identity,
    load_state_file,
    map_axi_cli_args_to_bridge,
    merge_page,
    parse_axi_eval_json,
    parse_axi_eval_string,
    parse_axi_pages,
    parse_bridge_eval_result,
    save_state_file,
    strip_axi_page_list,
    surf_agent_app_url,
    unwrap_no_arg_iife,
    wrap_script_expression,
)
from .base import AgentPage, BrowserBackend, ScreenshotOptions
from .camoufox import CamoufoxBackend, CamoufoxBridgeClient
from .patchright import PatchrightBackend, PatchrightBridgeClient


def create_backend(
    agent: Any,
    name: str,
    *,
    camoufox_client: CamoufoxBridgeClient,
    patchright_client: PatchrightBridgeClient,
    welcome_url: Callable[[], str],
) -> BrowserBackend:
    if name == DEFAULT_BACKEND:
        return AxiBackend(agent)
    if name == CAMOUFOX_BACKEND:
        return CamoufoxBackend(agent, client=camoufox_client, welcome_url=welcome_url)
    if name == PATCHRIGHT_BACKEND:
        return PatchrightBackend(agent, client=patchright_client, welcome_url=welcome_url)
    raise SurfAgentError(f"unsupported surf-agent backend: {name}", exit_code=2)


__all__ = [
    "AgentPage",
    "ScreenshotOptions",
    "extract_page_id",
    "find_page",
    "format_bridge_eval_result",
    "format_bridge_text_result",
    "is_no_pages_output",
    "is_surf_agent_bootstrap_identity",
    "load_state_file",
    "merge_page",
    "parse_axi_eval_json",
    "parse_axi_eval_string",
    "parse_axi_pages",
    "parse_bridge_eval_result",
    "save_state_file",
    "strip_axi_page_list",
    "surf_agent_app_url",
    "unwrap_no_arg_iife",
    "wrap_script_expression",
    "AxiBackend",
    "AxiBridgeClient",
    "AxiBridgeConfigMismatch",
    "AxiBridgeUnavailable",
    "BridgeUnavailable",
    "BrowserBackend",
    "CamoufoxBackend",
    "CamoufoxBridgeClient",
    "PatchrightBackend",
    "PatchrightBridgeClient",
    "create_backend",
    "map_axi_cli_args_to_bridge",
]
