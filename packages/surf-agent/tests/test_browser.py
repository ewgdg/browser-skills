from unittest.mock import Mock, patch

import pytest

from surf_agent import Browser, SurfAgentError, Thread


def test_backend_configuration_is_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SURF_AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("SURF_AGENT_BACKEND", raising=False)
    browser = Browser()
    with patch("surf_agent.browser.SurfAgent") as factory:
        factory.return_value.browser_backend.bridge_stop.return_value = 0
        browser.set_backend("axi")
    assert browser.backend().backend == "axi"
    browser.reset_backend()
    assert browser.backend().backend == "patchright"
    assert capsys.readouterr() == ("", "")


def test_admin_actions_are_silent_and_fail_on_status(capsys):
    agent = Mock()
    agent.browser_backend.bridge_stop.return_value = 0
    agent.browser_backend.close_matching.return_value = 1
    with patch("surf_agent.browser.SurfAgent", return_value=agent):
        Browser().stop_bridge()
        with pytest.raises(SurfAgentError):
            Browser().close_matching("task-*")
    assert capsys.readouterr() == ("", "")


def test_thread_focus():
    agent = Mock()
    agent.browser_backend.focus.return_value = 0
    with patch("surf_agent.thread._create_agent", return_value=agent):
        thread = Thread("task")
        thread.focus()
    agent.browser_backend.focus.assert_called_once_with()


def test_thread_reset_forgets_axi_state_without_closing_window():
    agent = Mock(backend="axi")
    with patch("surf_agent.thread._create_agent", return_value=agent):
        Thread("task").reset()
    agent.reset_state.assert_called_once_with()
    agent.browser_backend.close.assert_not_called()


def test_thread_reset_rejects_patchright_without_changing_ownership():
    agent = Mock(backend="patchright")
    with patch("surf_agent.thread._create_agent", return_value=agent):
        with pytest.raises(SurfAgentError, match="not supported.*Patchright"):
            Thread("task").reset()
    agent.reset_state.assert_not_called()
    agent.browser_backend.close.assert_not_called()


@pytest.mark.parametrize(
    "persisted,override,target,cleanup",
    [
        ("patchright", None, "axi", "patchright"),
        ("axi", None, "patchright", "axi"),
        ("axi", None, "axi", None),
        ("axi", "patchright", "axi", None),
        ("axi", "patchright", "patchright", "axi"),
        ("invalid", None, "patchright", None),
        (None, None, "axi", "patchright"),
    ],
)
def test_backend_switch_cleans_previous_persisted_runtime(
    persisted, override, target, cleanup, monkeypatch, capsys
):
    from surf_agent import config, runtime

    config.write_config(runtime.backend_config_file(), {"backend": persisted, "other": True})
    if override:
        monkeypatch.setenv("SURF_AGENT_BACKEND", override)
    else:
        monkeypatch.delenv("SURF_AGENT_BACKEND", raising=False)
    with patch("surf_agent.browser.SurfAgent") as factory:
        factory.return_value.browser_backend.bridge_stop.return_value = 0
        Browser().set_backend(target)
        if cleanup:
            factory.assert_called_once_with(backend=cleanup)
            factory.return_value.browser_backend.bridge_stop.assert_called_once_with()
        else:
            factory.assert_not_called()
    assert config.load_config(runtime.backend_config_file()) == {"backend": target, "other": True}
    assert capsys.readouterr() == ("", "")


def test_backend_switch_does_not_commit_when_cleanup_fails(monkeypatch):
    from surf_agent import config, runtime

    monkeypatch.delenv("SURF_AGENT_BACKEND", raising=False)
    config.set_backend("axi", path=runtime.backend_config_file())
    with patch("surf_agent.browser.SurfAgent") as factory:
        factory.return_value.browser_backend.bridge_stop.return_value = 1
        with pytest.raises(SurfAgentError, match="cleanup"):
            Browser().set_backend("patchright")
    assert Browser().backend().backend == "axi"


def test_explicit_cleanup_backend_ignores_temporary_environment_override(monkeypatch):
    from surf_agent.runtime import SurfAgent

    monkeypatch.setenv("SURF_AGENT_BACKEND", "patchright")
    assert SurfAgent(backend="axi").browser_backend.name == "axi"
    monkeypatch.setenv("SURF_AGENT_BACKEND", "axi")
    assert SurfAgent(backend="patchright").browser_backend.name == "patchright"


def test_profile_and_manual_open_are_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SURF_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SURF_AGENT_BACKEND", "patchright")
    info = Browser().profile()
    assert info.backend == "patchright"
    assert info.profile_dir == tmp_path / "profiles" / "chrome"
    with patch("surf_agent.browser.SurfAgent") as factory:
        factory.return_value.profile_open.return_value = 0
        Browser().open_profile("https://example.test/login")
        factory.return_value.profile_open.assert_called_once_with(
            "https://example.test/login"
        )
    assert capsys.readouterr() == ("", "")


def test_setup_reports_missing_requirements_without_installing(monkeypatch, tmp_path):
    monkeypatch.setenv("SURF_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SURF_AGENT_BACKEND", "patchright")
    monkeypatch.setattr(
        "surf_agent.runtime.python_module_available", lambda name: False
    )
    with pytest.raises(SurfAgentError, match="patchright extra"):
        Browser().setup()


def test_threads_use_nonstarting_local_inventory(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SURF_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SURF_AGENT_BACKEND", "patchright")
    from surf_agent.runtime import SurfAgent

    agent = SurfAgent()
    agent.patchright_client = Mock()
    agent.patchright_client.call_tool_if_running.return_value = '{"pages":[{"thread":"task","page_id":7,"url":"https://example.test/","title":"Example"}]}'
    with patch("surf_agent.browser.SurfAgent", return_value=agent):
        threads = Browser().threads()
        assert [(item.name, item.page_id, item.title) for item in threads] == [
            ("task", 7, "Example")
        ]
        agent.patchright_client.call_tool_if_running.assert_called_once_with("list", {})
        agent.patchright_client.call_tool.assert_not_called()
        agent.patchright_client.call_tool_if_running.return_value = None
        assert Browser().threads() == []
        agent.patchright_client.call_tool_if_running.return_value = '{"pages":[null]}'
        with pytest.raises(SurfAgentError, match="thread list"):
            Browser().threads()
    assert capsys.readouterr() == ("", "")
