"""Getting a token for Foundry.

The Foundry portal offers an API key, and it does not work here: the client
library types `credential` as `TokenCredential` and its own documentation says
Entra ID is the only supported method. The key authenticates the raw Azure
OpenAI endpoint, not the Agents service, so using it would mean giving up the
agent service and writing the orchestration by hand.

Three ways in, tried in order, because the same code runs in three places:

  1. a service principal, from environment variables  — CI
  2. the Azure CLI session                            — a developer machine with az
  3. an interactive browser sign-in                   — a machine without az

The interactive fallback exists so that day one does not require installing
the Azure CLI first. It cannot work in CI, which is correct: CI should be
holding a service principal, not a human session.
"""

from __future__ import annotations

import os

SCOPE = "https://ai.azure.com/.default"


def get_credential(quiet: bool = False):
    from azure.identity import (
        AzureCliCredential,
        ClientSecretCredential,
        InteractiveBrowserCredential,
    )

    def note(msg: str) -> None:
        if not quiet:
            print(f"auth: {msg}")

    tenant = os.environ.get("AZURE_TENANT_ID")
    client_id = os.environ.get("AZURE_CLIENT_ID")
    secret = os.environ.get("AZURE_CLIENT_SECRET")
    if tenant and client_id and secret:
        note("using the service principal from the environment")
        return ClientSecretCredential(tenant, client_id, secret)

    try:
        cred = AzureCliCredential()
        cred.get_token(SCOPE)
        note("using the Azure CLI session")
        return cred
    except Exception:
        pass

    note("falling back to an interactive browser sign-in")
    return InteractiveBrowserCredential(tenant_id=tenant) if tenant else InteractiveBrowserCredential()


def project_endpoint() -> str:
    """Full project endpoint, assembled from parts if not given whole."""
    if endpoint := os.environ.get("AZURE_AI_PROJECT_ENDPOINT"):
        return endpoint.rstrip("/")
    resource = os.environ.get("AZURE_AI_RESOURCE")
    project = os.environ.get("AZURE_AI_PROJECT")
    if not (resource and project):
        raise SystemExit(
            "Set AZURE_AI_PROJECT_ENDPOINT, or both AZURE_AI_RESOURCE and "
            "AZURE_AI_PROJECT. The endpoint looks like\n"
            "  https://<resource>.services.ai.azure.com/api/projects/<project>"
        )
    return f"https://{resource}.services.ai.azure.com/api/projects/{project}"
