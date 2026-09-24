"""Automate agent tasks from Xircuits jobs and services running inside Xpress AI."""

import time

from xai_components.base import Component, InArg, InCompArg, OutArg, xai_component

from .platform_client import PlatformClient, positive_number, request_key, required_text, task_id


def _creation(component):
    payload = {
        "agent_name": required_text(component.agent_name.value, "agent_name"),
        "summary": required_text(component.summary.value, "summary"),
        "request_key": request_key(component.request_key.value, component.__id__),
    }
    if component.details.value is not None:
        if not isinstance(component.details.value, str):
            raise ValueError("details must be a string")
        payload["details"] = component.details.value
    if component.token_budget.value is not None:
        budget = component.token_budget.value
        if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
            raise ValueError("token_budget must be a positive integer")
        payload["token_budget"] = budget
    return payload


def _output(component, task):
    component.task.value = task
    component.status.value = task["status"]
    component.waiting_version.value = task.get("waiting_version")


@xai_component
class XpressAIListAgents(Component):
    """List project agents, with id, name and is_active; uses injected workload credentials."""
    agents: OutArg[list]

    def execute(self, ctx):
        with PlatformClient() as client:
            self.agents.value = client.list_agents()


@xai_component
class XpressAIListProcedures(Component):
    """List an agent's saved procedures and shared procedures, including input definitions.

    Each result has agentName (the owner), procedureName, inputs, outputs and steps.
    Use procedure_scope=shared when selecting a procedure whose agentName is shared.
    """
    agent_name: InCompArg[str]
    procedures: OutArg[list]

    def execute(self, ctx):
        with PlatformClient() as client:
            self.procedures.value = client.list_procedures(self.agent_name.value)


@xai_component
class XpressAICreateTask(Component):
    """Assign and queue an agent task as Todo. steps is a non-empty list of strings.

    request_key identifies one business request. A repeated key with unchanged inputs
    returns the original task; changed inputs fail. In compiled Platform jobs it can
    be omitted to derive a key from job + node IDs. Loops need one key per item.
    """
    agent_name: InCompArg[str]
    summary: InCompArg[str]
    steps: InCompArg[list]
    details: InArg[str]
    request_key: InArg[str]
    token_budget: InArg[int]
    task_id: OutArg[int]
    task: OutArg[dict]
    status: OutArg[str]
    waiting_version: OutArg[str]

    def execute(self, ctx):
        payload = _creation(self)
        steps = self.steps.value
        if not isinstance(steps, list) or not steps or not all(isinstance(s, str) and s.strip() for s in steps):
            raise ValueError("steps must be a non-empty list of non-empty strings")
        payload["steps"] = steps
        with PlatformClient() as client:
            task = client.create_task(payload)
        self.task_id.value = task["id"]
        _output(self, task)


@xai_component
class XpressAIStartProcedure(Component):
    """Queue a saved procedure on an active agent using ordinary agent execution.

    inputs is a dict; missing required or unknown inputs fail before task creation.
    procedure_scope defaults to agent; shared selects the shared procedure directory.
    READY execution is not provided by this component.
    """
    agent_name: InCompArg[str]
    procedure_name: InCompArg[str]
    summary: InCompArg[str]
    inputs: InArg[dict]
    procedure_scope: InArg[str]
    details: InArg[str]
    request_key: InArg[str]
    token_budget: InArg[int]
    task_id: OutArg[int]
    task: OutArg[dict]
    status: OutArg[str]
    waiting_version: OutArg[str]

    def execute(self, ctx):
        payload = _creation(self)
        payload["procedure_name"] = required_text(self.procedure_name.value, "procedure_name")
        values = self.inputs.value
        if values is not None and (not isinstance(values, dict) or not all(isinstance(k, str) for k in values)):
            raise ValueError("inputs must be a dictionary with string keys")
        payload["inputs"] = {} if values is None else values
        scope = "agent" if self.procedure_scope.value is None else self.procedure_scope.value
        if scope not in ("agent", "shared"):
            raise ValueError("procedure_scope must be agent or shared")
        payload["procedure_scope"] = scope
        with PlatformClient() as client:
            task = client.create_task(payload)
        self.task_id.value = task["id"]
        _output(self, task)


@xai_component
class XpressAIGetTask(Component):
    """Read a task created by the workflow API in this project.

    task contains status, steps, conversation, waiting_details and last_agent_error.
    A non-empty waiting_version can be connected to XpressAIRespondToTask.
    """
    task_id: InCompArg[int]
    task: OutArg[dict]
    status: OutArg[str]
    waiting_version: OutArg[str]

    def execute(self, ctx):
        with PlatformClient() as client:
            _output(self, client.get_task(self.task_id.value))


@xai_component
class XpressAIWaitForTask(Component):
    """Wait for Done, Failed or settled Waiting; branch on status before further actions.

    Defaults: timeout_seconds=300 and poll_seconds=2. Timeout raises TimeoutError;
    it does not cancel the agent task. A Waiting task still needs approval/input.
    Failed is returned as a status, not mistaken for success.
    """
    task_id: InCompArg[int]
    timeout_seconds: InArg[float]
    poll_seconds: InArg[float]
    task: OutArg[dict]
    status: OutArg[str]
    waiting_version: OutArg[str]

    def execute(self, ctx):
        identifier = task_id(self.task_id.value)
        timeout = positive_number(300 if self.timeout_seconds.value is None else self.timeout_seconds.value, "timeout_seconds")
        poll = positive_number(2 if self.poll_seconds.value is None else self.poll_seconds.value, "poll_seconds")
        deadline = time.monotonic() + timeout
        with PlatformClient() as client:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Task {identifier} did not finish or request input within {timeout} seconds")
                task = client.get_task(identifier, timeout=min(30, remaining))
                _output(self, task)
                if task["status"] in ("Done", "Failed") or (task["status"] == "Waiting" and task.get("waiting_version")):
                    return
                time.sleep(min(poll, max(0, deadline - time.monotonic())))


@xai_component
class XpressAIRespondToTask(Component):
    """Answer a settled Waiting workflow task and queue it for continuation.

    waiting_version must come from GetTask/WaitForTask. A stale approval fails with
    HTTP 409. request_key permits replay without appending the same answer twice.
    This conveys workflow input; it does not attest to a human approver's identity.
    """
    task_id: InCompArg[int]
    content: InCompArg[str]
    waiting_version: InCompArg[str]
    request_key: InArg[str]
    task: OutArg[dict]
    status: OutArg[str]

    def execute(self, ctx):
        payload = {
            "content": required_text(self.content.value, "content"),
            "waiting_version": required_text(self.waiting_version.value, "waiting_version"),
            "request_key": request_key(self.request_key.value, self.__id__),
        }
        with PlatformClient() as client:
            task = client.respond(self.task_id.value, payload)
        self.task.value = task
        self.status.value = task["status"]
