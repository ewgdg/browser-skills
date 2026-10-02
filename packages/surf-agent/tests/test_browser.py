from unittest.mock import Mock, patch

import pytest

from surf_agent import Browser, SurfAgentError, Thread


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


def test_profile_and_manual_open_are_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SURF_AGENT_HOME", str(tmp_path))
    info = Browser().profile()
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
    monkeypatch.setattr(
        "surf_agent.runtime.python_module_available", lambda name: False
    )
    with pytest.raises(SurfAgentError, match="patchright extra"):
        Browser().setup()


def test_threads_use_nonstarting_local_inventory(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SURF_AGENT_HOME", str(tmp_path))
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
