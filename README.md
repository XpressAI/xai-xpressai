# XpressAI Xircuits Component Library

Xircuits components for automating agent workflows inside Xpress AI, alongside
the existing Relay chat and recognition components. `xai-tasks` is unchanged;
these components operate on Platform tasks, not a local SQLite database.


## Installation

```bash
pip install -r requirements.txt
```

To use this component library, simply copy the directory / clone or submodule the repository to your working Xircuits project directory.

## Platform agent workflows

These components run inside Platform jobs and services. They read the injected
environment automatically; there is no authorization node or token input port.

| Variable | Purpose |
| --- | --- |
| `XPRESSAI_PLATFORM_URL` | Platform base URL |
| `XPRESSAI_PROJECT_ID` | Canonical project ID (never the `personal` route alias) |
| `XPRESSAI_NAMESPACE` | Caller workload namespace (may differ from a team agent's namespace) |
| `XPRESSAI_API_TOKEN` | Project workload credential |
| `XPRESSAI_JOB_ID` | Optional job execution ID used for default retry keys |

The Platform must include the `/api/projects/{projectId}/workflows` API from
[ADR-079](https://github.com/XpressAI/platform/blob/main/adr/079-workload-agent-workflows.md).
Deploy that backend before updating the component library. Recreate older job
pods if they lack the new environment variables. A 404 on the workflow API
usually means the backend has not been updated. These components do not fall
back to the older agent-authenticated task API.

| Component | Inputs | Outputs / behavior |
| --- | --- | --- |
| `XpressAIListAgents` | None | `agents`: names, IDs, active status in this project |
| `XpressAIListProcedures` | `agent_name` | `procedures`: agent and shared procedures, with input and output definitions |
| `XpressAICreateTask` | `agent_name`, `summary`, `steps` (list of strings) | Assigned task queued as **Todo**, plus `task_id`, `task`, `status` |
| `XpressAIStartProcedure` | `agent_name`, `procedure_name`, `summary`; optional `inputs` dict and `procedure_scope` (`agent` or `shared`) | Validates inputs, applies defaults, snapshots the saved procedure and queues it as Todo |
| `XpressAIGetTask` | `task_id` | `task`, `status`, and `waiting_version` for workflow-created tasks |
| `XpressAIWaitForTask` | `task_id`; optional `timeout_seconds` (300), `poll_seconds` (2) | Returns **Done**, **Failed**, or settled **Waiting**; timeout raises an error without cancelling the task |
| `XpressAIRespondToTask` | `task_id`, `content`, `waiting_version` | Answers a settled Waiting task and queues continuation as Todo |

Creation components also accept optional `details` and a positive `token_budget`.
The `task` dict includes `steps`, `conversation`, `waiting_details`, and
`last_agent_error`. Procedure outputs are described to the agent; this API does
not guarantee a typed output object. Inspect the completed task's conversation.

Always branch on `status` after waiting: **Waiting** requires input and **Failed**
requires error handling. A successful component call only means the API request
succeeded, not that the agent completed the workflow. Task dispatch uses the
normal agent scheduler and is subject to agent capacity.

### Retry keys and approval responses

All writes accept `request_key`. A retry with the same key and unchanged inputs
returns the original task; changed inputs return HTTP 409. The API serializes
concurrent requests in PostgreSQL. Creation keys are reserved for the lifetime
of the task; deleting that task releases its key.

In a compiled Platform job, an unconnected `request_key` port derives a stable
key from the job ID and the Xircuits node ID. A new scheduled firing gets a new
job ID. For services, interactive runs, and loops, connect an explicit business
key such as `purchase:DEMO-42`. In a loop, use a different key for each item.
To intentionally run the same business operation again, supply a new key.

Connect the exact `waiting_version` from GetTask or WaitForTask to RespondToTask,
and supply a key for that response. Stale versions and active turns return 409;
fetch the current task before deciding whether the answer still applies. A
repeated response with the same key is appended only once. This represents
workflow input, not an attestation of a human approver's identity: obtain the
decision through your application's approval control before submitting it.

HTTP calls have timeouts and do not follow redirects or automatically retry
writes. If a write times out, its outcome may be unknown; retry with the same
key. Credentials stay in environment variables and are never stored in the
workflow file or returned through output ports.

### Scheduled approval example

1. Save [approval-demo.proc](examples/approval-demo.proc) as
   `agents/<agent_name>/procedures/approval-demo.proc` in your Platform workspace.
   Use an active agent with the desktop/tools and access required by your demo.
2. Open [ScheduledApproval.xircuits](examples/ScheduledApproval.xircuits). Its
   inputs dict contains the sample request ID `DEMO-42`; change it to a real
   demo request or connect data from your existing workflow.
3. Create a Platform Xircuits job or schedule pointing to
   `xai_components/xai_xpressai/examples/ScheduledApproval.xircuits`, with these
   job parameters (replace `approver` with your agent):

   ```json
   {"agent_name":"approver","procedure_name":"approval-demo","summary":"Review DEMO-42"}
   ```

The example queues the procedure and prints the returned task. It does not
wait for a human or approve anything automatically. In a longer workflow,
connect its `task_id` to WaitForTask, branch on status, and use RespondToTask
after collecting the decision. In a service or interactive run, connect a
`request_key` before executing this example.

These components use ordinary agent execution. They **do not enable READY**;
the Platform's READY plans are still previews pending runtime integration.
The example must be rehearsed against your actual legacy application before
claiming an end-to-end demo or a speed benchmark.

## Relay authentication

Set `XPRESSAI_RELAY_TOKEN` to the Relay credential provisioned for the agent or
workload whose usage budget should be charged. `XpressAIAuthorize` rejects a
missing or blank credential before creating a client. There is no shared default
token, and a Platform API token (`XPRESSAI_API_TOKEN`) is not a Relay credential.

Keep the credential in the runtime's secret configuration; do not put it in
workflow files or source control. Existing installations must set this variable
before upgrading.

## Development checks

```bash
pip install -r requirements.txt xircuits==1.19.2 pytest
python -m pytest -q
```

Tests use the real Xircuits component ports and compiler with local HTTP
fixtures, including the scheduled example, approval continuation, timeouts,
credential isolation, malformed responses and retry keys. They do not call a
live agent or enterprise application.
