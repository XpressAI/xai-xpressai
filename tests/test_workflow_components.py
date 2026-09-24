import json
import runpy
from pathlib import Path

import pytest
from xircuits.compiler.compiler import compile as compile_xircuit
from xai_components.base import InCompArg, OutArg
from xai_components.xai_xpressai.platform_client import PlatformClient, PlatformWorkflowError, request_key
from xai_components.xai_xpressai import workflow_components as components


def task(status="Todo", **extra):
    return {"id": 42, "status": status, "waiting_version": None, **extra}


def create_component(kind=components.XpressAICreateTask):
    c = kind()
    c.agent_name.value = "approver"
    c.summary.value = "Review purchase 42"
    c.request_key.value = "purchase:42"
    if kind is components.XpressAICreateTask:
        c.steps.value = ["Inspect the request", "Wait for approval", "Apply the decision"]
    else:
        c.procedure_name.value = "review"
        c.inputs.value = {"request_id": "42", "amount": 12000}
    return c


def test_real_ports_create_and_read_using_only_injected_workload_credentials(api):
    api.responses.extend([(200, task(), {}), (200, task("Doing"), {})])
    c = create_component()
    assert isinstance(c.agent_name, InCompArg)
    assert isinstance(c.task_id, OutArg)
    c.execute({})
    read = components.XpressAIGetTask()
    read.task_id.connect(c.task_id)
    read.execute({})
    assert c.task_id.value == 42 and read.status.value == "Doing"
    method, path, headers, payload = api.requests[0]
    assert (method, path) == ("POST", "/api/projects/personal-owner/workflows/tasks")
    assert payload["request_key"] == "purchase:42" and payload["steps"][1] == "Wait for approval"
    assert headers["X-Task-Authorization"] == "workload-test-token"
    assert headers["X-Task-Namespace"] == "owner-ns"
    assert not any(key in headers for key in ("Authorization", "X-Agent-Name", "X-Agent-Authorization"))
    assert api.requests[1][1].endswith("/tasks/42")


def test_start_shared_procedure_preserves_structured_inputs(api):
    api.responses.append((200, task(), {}))
    c = create_component(components.XpressAIStartProcedure)
    c.procedure_scope.value = "shared"
    c.execute({})
    payload = api.requests[0][3]
    assert payload["inputs"] == {"request_id": "42", "amount": 12000}
    assert payload["procedure_scope"] == "shared" and payload["procedure_name"] == "review"
    assert "steps" not in payload


def test_list_agents_and_procedures(api):
    api.responses.extend([(200, [{"name": "approver", "is_active": True}], {}),
                          (200, [{"procedureName": "review", "agentName": "shared"}], {})])
    agents = components.XpressAIListAgents()
    agents.execute({})
    procedures = components.XpressAIListProcedures()
    procedures.agent_name.value = agents.agents.value[0]["name"]
    procedures.execute({})
    assert procedures.procedures.value[0]["procedureName"] == "review"
    assert api.requests[1][1].endswith("/agents/approver/procedures")


def test_wait_then_answer_approval_uses_exact_version_and_request_key(api):
    api.responses.extend([(200, task("Doing"), {}), (200, task("Waiting"), {}),
                          (200, task("Waiting", waiting_version="exact-state"), {}), (200, task(), {})])
    wait = components.XpressAIWaitForTask()
    wait.task_id.value = 42
    wait.poll_seconds.value = 0.001
    wait.execute({})
    assert wait.status.value == "Waiting"
    response = components.XpressAIRespondToTask()
    response.task_id.value = 42
    response.waiting_version.connect(wait.waiting_version)
    response.content.value = "Approved by operator"
    response.request_key.value = "approval-response:42"
    response.execute({})
    assert response.status.value == "Todo"
    assert api.requests[-1][3] == {"content": "Approved by operator", "waiting_version": "exact-state",
                                   "request_key": "approval-response:42"}


@pytest.mark.parametrize("status", ["Done", "Failed"])
def test_wait_exposes_completion_and_failure_separately(api, status):
    api.responses.append((200, task(status), {}))
    c = components.XpressAIWaitForTask()
    c.task_id.value = 42
    c.execute({})
    assert c.status.value == status


def test_wait_timeout_is_bounded_and_does_not_cancel_task(api, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(components.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(components.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    api.responses.extend([(200, task("Doing"), {})] * 3)
    c = components.XpressAIWaitForTask()
    c.task_id.value = 42
    c.timeout_seconds.value = 5
    c.poll_seconds.value = 2
    with pytest.raises(TimeoutError, match="42"):
        c.execute({})
    assert clock[0] == 5
    assert len(api.requests) == 3 and all(r[0] == "GET" for r in api.requests)


@pytest.mark.parametrize("bad", [[], "{}", 42, True, {1: "value"}])
def test_invalid_procedure_inputs_never_make_a_request(api, bad):
    c = create_component(components.XpressAIStartProcedure)
    c.inputs.value = bad
    with pytest.raises(ValueError, match="inputs"):
        c.execute({})
    assert not api.requests


@pytest.mark.parametrize("variable", ["XPRESSAI_PROJECT_ID", "XPRESSAI_PLATFORM_URL", "XPRESSAI_NAMESPACE", "XPRESSAI_API_TOKEN"])
def test_missing_runtime_configuration_fails_before_network(api, monkeypatch, variable):
    monkeypatch.delenv(variable)
    with pytest.raises(ValueError, match=variable):
        create_component().execute({})
    assert not api.requests


def test_personal_alias_is_not_derived_from_namespace(api, monkeypatch):
    monkeypatch.setenv("XPRESSAI_PROJECT_ID", "personal")
    with pytest.raises(ValueError, match="canonical"):
        PlatformClient()


def test_redirects_never_forward_workload_credentials(api):
    api.responses.append((307, {}, {"Location": "http://127.0.0.1:1/stolen"}))
    with pytest.raises(PlatformWorkflowError, match="307"):
        create_component().execute({})
    assert len(api.requests) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 500])
def test_server_errors_do_not_retry_writes_or_expose_credentials(api, status):
    api.responses.append((status, {"error": "example workload-test-token"}, {}))
    with pytest.raises(PlatformWorkflowError) as error:
        create_component().execute({})
    assert str(status) in str(error.value)
    assert "workload-test-token" not in str(error.value)
    assert len(api.requests) == 1


@pytest.mark.parametrize("response", [b"not JSON", [], {"id": True, "status": "Todo"}, {"id": 42, "status": "unknown"}])
def test_malformed_success_response_is_not_reported_as_a_task(api, response):
    api.responses.append((200, response, {}))
    with pytest.raises(PlatformWorkflowError):
        create_component().execute({})


def test_job_default_keys_are_stable_per_node_and_firing(monkeypatch):
    monkeypatch.setenv("XPRESSAI_JOB_ID", "123")
    a = request_key(None, "node-a")
    assert request_key(None, "node-a") == a
    assert request_key(None, "node-b") != a
    monkeypatch.setenv("XPRESSAI_JOB_ID", "124")
    assert request_key(None, "node-a") != a
    assert request_key("business:42", None) == "business:42"
    monkeypatch.delenv("XPRESSAI_JOB_ID")
    with pytest.raises(ValueError, match="request_key"):
        request_key(None, "node-a")


def test_compiled_scheduled_procedure_example_runs_with_real_xircuits(api, tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "ScheduledApproval.py"
    compile_xircuit(str(root / "examples" / "ScheduledApproval.xircuits"), str(output))
    api.responses.append((200, task(), {}))
    monkeypatch.setenv("XPRESSAI_JOB_ID", "987")
    module = runpy.run_path(str(output))
    flow = module["ScheduledApproval"]()
    flow.agent_name.value = "approver"
    flow.procedure_name.value = "review"
    flow.summary.value = "Review purchase 42"
    flow.execute({})
    payload = api.requests[0][3]
    assert payload["procedure_name"] == "review"
    assert payload["inputs"]["request_id"] == "DEMO-42"
    assert payload["request_key"].startswith("job:")
    assert len(api.requests) == 1
