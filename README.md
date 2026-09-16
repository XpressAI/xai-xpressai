# XpressAI Xircuits Component Library

This library contains Xircuits components for interacting with the XpressAI Relay API, allowing you to easily integrate AI functionalities from different providers, such as generating text, into your Xircuits projects. It also contains a workflow to help you get started.


## Installation

```bash
pip install -r requirements.txt
```

To use this component library, simply copy the directory / clone or submodule the repository to your working Xircuits project directory.

## Authentication

Set `XPRESSAI_RELAY_TOKEN` to the Relay credential provisioned for the agent or
workload whose usage budget should be charged. `XpressAIAuthorize` rejects a
missing or blank credential before creating a client. There is no shared default
token, and a Platform API token (`XPRESSAI_API_TOKEN`) is not a Relay credential.

Keep the credential in the runtime's secret configuration; do not put it in
workflow files or source control. Existing installations must set this variable
before upgrading.
