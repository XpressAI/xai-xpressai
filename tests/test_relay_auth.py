"""Exercise the authorization component without starting the Xircuits runtime."""
import runpy
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def component(monkeypatch):
    base = ModuleType("xai_components.base")
    class Port:
        def __class_getitem__(cls, item):
            return cls
    for name in ("InArg", "OutArg", "InCompArg"):
        setattr(base, name, Port)
    base.Component = base.BaseComponent = object
    base.secret = str
    base.xai_component = lambda cls=None, **kw: cls if cls is not None else lambda item: item
    monkeypatch.setitem(sys.modules, "xai_components", ModuleType("xai_components"))
    monkeypatch.setitem(sys.modules, "xai_components.base", base)
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "relay_components.py"))
    return module["XpressAIAuthorize"]()


@pytest.mark.parametrize("credential", [None, "", "   "])
def test_missing_relay_credential_never_creates_client(component, monkeypatch, credential):
    if credential is None:
        monkeypatch.delenv("XPRESSAI_RELAY_TOKEN", raising=False)
    else:
        monkeypatch.setenv("XPRESSAI_RELAY_TOKEN", credential)
    monkeypatch.setenv("XPRESSAI_API_TOKEN", "project-token-is-not-relay-auth")
    client = Mock()
    monkeypatch.setitem(component.execute.__globals__, "OpenAI", client)
    with pytest.raises(ValueError, match="XPRESSAI_RELAY_TOKEN"):
        component.execute({})
    client.assert_not_called()


def test_explicit_relay_credential_reaches_client(component, monkeypatch):
    monkeypatch.setenv("XPRESSAI_RELAY_TOKEN", "  relay-credential  ")
    monkeypatch.setenv("XPRESSAI_API_TOKEN", "wrong-project-credential")
    client = Mock()
    monkeypatch.setitem(component.execute.__globals__, "OpenAI", client)
    context = {}
    component.execute(context)
    client.assert_called_once_with(api_key="relay-credential", base_url="https://relay.public.cloud.xpress.ai/v1/")
    assert context["client"] is client.return_value
