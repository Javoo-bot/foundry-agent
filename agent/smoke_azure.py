"""Go/no-go check for the Foundry side.

Runs in four steps that fail in a useful order: a token, then the project, then
the model deployments, then one real agent round trip. Each step prints what it
found, so a failure says which of the four is wrong rather than just refusing.

Step three lists the deployments in the project. The name printed there is what
belongs in AZURE_AI_MODEL_DEPLOYMENT_NAME — it is the name given to the
deployment, which is not necessarily the name of the model.

    python gishub/agent/smoke_azure.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import envfile  # noqa: E402

envfile.load()

from agent.auth import get_credential, project_endpoint  # noqa: E402


def main() -> int:
    endpoint = project_endpoint()
    print(f"project  {endpoint}\n")

    print("[1/4] acquiring a token")
    try:
        credential = get_credential()
    except Exception as exc:
        print(f"FAIL  could not build a credential: {exc}")
        return 1

    print("\n[2/4] connecting to the project")
    from azure.ai.projects import AIProjectClient

    try:
        client = AIProjectClient(endpoint=endpoint, credential=credential)
    except Exception as exc:
        print(f"FAIL  {type(exc).__name__}: {exc}")
        return 1
    print("PASS  project client constructed")

    print("\n[3/4] listing model deployments")
    names: list[str] = []
    try:
        for dep in client.deployments.list():
            name = getattr(dep, "name", "?")
            model = getattr(dep, "model_name", None) or getattr(dep, "model", "?")
            names.append(name)
            print(f"      {name:32s} model={model}")
        if not names:
            print(
                "FAIL  no deployments. In the Foundry portal: Models + endpoints -> "
                "Deploy model -> Deploy base model -> gpt-4.1-mini -> Global Standard."
            )
            return 1
        print(f"PASS  {len(names)} deployment(s)")
    except Exception as exc:
        print(f"WARN  could not list deployments ({type(exc).__name__}: {exc})")
        print("      continuing; set AZURE_AI_MODEL_DEPLOYMENT_NAME by hand if step 4 fails")

    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME") or (names[0] if names else "")
    if not deployment:
        print("FAIL  no deployment name available")
        return 1
    print(f"\n[4/4] one agent round trip on '{deployment}'")

    from azure.ai.projects.models import PromptAgentDefinition

    try:
        agent = client.agents.create_version(
            agent_name="gishub-smoke",
            definition=PromptAgentDefinition(
                model=deployment,
                instructions="Reply with exactly one word.",
            ),
        )
        with client.get_openai_client() as oai:
            conversation = oai.conversations.create(
                items=[{"type": "message", "role": "user", "content": "Say OK."}]
            )
            response = oai.responses.create(
                conversation=conversation.id,
                extra_body={"agent": {"name": agent.name, "type": "agent_reference"}},
                input="",
            )
            print(f"PASS  agent replied: {(response.output_text or '').strip()!r}")
            oai.conversations.delete(conversation_id=conversation.id)
        client.agents.delete_version(agent_name=agent.name, agent_version=agent.version)
    except Exception as exc:
        print(f"FAIL  {type(exc).__name__}: {exc}")
        print(
            "\nIf this is a 401 or 403, the signed-in identity needs a Foundry role on "
            "the resource: Azure AI User is enough to run agents. If it is a 404 on the "
            "deployment, the name above is wrong."
        )
        return 1

    print(f"\nAll checks passed. Use AZURE_AI_MODEL_DEPLOYMENT_NAME={deployment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
