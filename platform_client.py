"""Project workload client for the Platform workflow API (not Relay authentication)."""

import hashlib
import math
import os
import re
from urllib.parse import quote, urlsplit

import requests
from urllib3.util import Timeout


class PlatformWorkflowError(RuntimeError):
    """A Platform workflow request failed; writes must be retried with the same key."""


def required_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def positive_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def task_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("task_id must be a positive integer")
    return value


def request_key(value, node_id):
    """Stable for retries of a compiled job node; services supply business keys."""
    if value is None:
        job_id = os.getenv("XPRESSAI_JOB_ID", "")
        if not re.fullmatch(r"[0-9]+", job_id) or not isinstance(node_id, str) or not node_id:
            raise ValueError("request_key is required for services or executions without a Platform job and node ID")
        value = "job:" + hashlib.sha256(f"{job_id}:{node_id}".encode()).hexdigest()
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        raise ValueError("request_key must contain 1-128 letters, digits, dots, underscores, colons or hyphens")
    return value


class PlatformClient:
    def __init__(self):
        env = {name: required_text(os.getenv(name), name).strip() for name in (
            "XPRESSAI_PLATFORM_URL", "XPRESSAI_PROJECT_ID", "XPRESSAI_NAMESPACE", "XPRESSAI_API_TOKEN"
        )}
        if env["XPRESSAI_PROJECT_ID"] == "personal":
            raise ValueError("XPRESSAI_PROJECT_ID must be canonical; the personal route alias is not supported")
        base = env["XPRESSAI_PLATFORM_URL"].rstrip("/")
        url = urlsplit(base)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("XPRESSAI_PLATFORM_URL must be an absolute HTTP(S) base URL without credentials, query or fragment")
        self.base_url = base + "/api/projects/" + quote(env["XPRESSAI_PROJECT_ID"], safe="") + "/workflows"
        self._token = env["XPRESSAI_API_TOKEN"]
        self.session = requests.Session()
        # No implicit netrc credentials or machine-specific proxy authentication.
        self.session.trust_env = False
        self.session.headers.update({
            "X-Task-Authorization": self._token,
            "X-Task-Namespace": env["XPRESSAI_NAMESPACE"],
            "Accept": "application/json",
        })

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.close()

    def request(self, method, path, payload=None, timeout=30):
        positive_number(timeout, "timeout")
        try:
            response = self.session.request(
                method, self.base_url + path, json=payload, allow_redirects=False,
                timeout=Timeout(total=timeout, connect=min(5, timeout)),
            )
        except requests.RequestException:
            suffix = " The write outcome is unknown; retry with the same request_key." if method == "POST" else ""
            raise PlatformWorkflowError("Platform request failed or timed out." + suffix) from None
        if not 200 <= response.status_code < 300:
            message = ""
            try:
                body = response.json()
                message = str(body.get("error", "")) if isinstance(body, dict) else ""
            except ValueError:
                pass
            message = message.replace(self._token, "[redacted]")[:300]
            hint = " Check that the Platform workflow API is deployed." if response.status_code == 404 else ""
            raise PlatformWorkflowError(f"Platform returned HTTP {response.status_code}: {message}" + hint)
        try:
            return response.json()
        except ValueError:
            raise PlatformWorkflowError("Platform returned an invalid JSON response") from None

    def list_agents(self):
        return self._list(self.request("GET", "/agents"))

    def list_procedures(self, agent_name):
        name = quote(required_text(agent_name, "agent_name"), safe="")
        return self._list(self.request("GET", f"/agents/{name}/procedures"))

    def create_task(self, payload):
        return self._task(self.request("POST", "/tasks", payload))

    def get_task(self, identifier, timeout=30):
        return self._task(self.request("GET", f"/tasks/{task_id(identifier)}", timeout=timeout))

    def respond(self, identifier, payload):
        return self._task(self.request("POST", f"/tasks/{task_id(identifier)}/respond", payload))

    @staticmethod
    def _list(value):
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise PlatformWorkflowError("Platform returned an invalid list response")
        return value

    @staticmethod
    def _task(value):
        if (not isinstance(value, dict) or isinstance(value.get("id"), bool)
                or not isinstance(value.get("id"), int) or value["id"] <= 0
                or value.get("status") not in {"Backlog", "Todo", "Doing", "Waiting", "Done", "Failed"}):
            raise PlatformWorkflowError("Platform returned an invalid task response")
        return value
