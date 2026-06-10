"""Pipeline Creation Agent.

Microsoft Agent Framework agent backed by an Azure AI Foundry model. The
agent's system prompt lives in ``src/instructions.md`` and is loaded at
startup; edit that file to change the agent's behavior.

Run:

    python src/agent.py

Configuration is read from environment variables (a local ``.env`` file is
loaded automatically if ``python-dotenv`` is installed):

    FOUNDRY_PROJECT_ENDPOINT   e.g. https://my-project.services.ai.azure.com
    FOUNDRY_MODEL              e.g. gpt-4o
    FABRIC_WORKSPACE_ID        Fabric workspace the pipeline targets
    AZDO_ORG_URL               e.g. https://dev.azure.com/my-org
    AZDO_PROJECT               Azure DevOps project name
    AZDO_REPO                  Azure DevOps repo name
    AZDO_DEFAULT_BRANCH        Base branch for PRs (default: main)
    AZDO_PAT                   Personal access token with Code (read/write) +
                               PullRequest (read/write) scopes
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Annotated

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential
from pydantic import Field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


INSTRUCTIONS_PATH = Path(__file__).with_name("instructions.md")
REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINES_ROOT = REPO_ROOT / "pipelines"
NOTEBOOKS_ROOT = REPO_ROOT / "notebooks"

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_NOTEBOOK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")

NOTEBOOK_SKELETON = """# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# CELL ********************

# Welcome to your new notebook
# Type here in the cell editor to add code!


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
"""


def _load_instructions() -> str:
    return INSTRUCTIONS_PATH.read_text(encoding="utf-8")


def _pipeline_dir(name: str) -> Path:
    if not _NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid pipeline name {name!r}: use lowercase letters, digits, "
            "and hyphens only (must start with a letter or digit)."
        )
    return PIPELINES_ROOT / f"{name}.DataPipeline"


def _notebook_dir(name: str) -> Path:
    if not _NOTEBOOK_NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid notebook name {name!r}: use letters, digits, spaces, "
            "hyphens, and underscores only (must start with a letter or digit)."
        )
    return NOTEBOOKS_ROOT / f"{name}.Notebook"


@tool(approval_mode="never_require")
def create_pipeline(
    name: Annotated[
        str,
        Field(description="Kebab-case pipeline name (e.g. 'daily-sales-load')."),
    ],
) -> str:
    """Create ``pipelines/<name>.DataPipeline/`` with the Fabric Git layout.

    Writes both ``pipeline-content.json`` (empty pipeline) and ``.platform``
    (Fabric Git integration metadata). Fails if the folder already exists.
    """
    pipeline_dir = _pipeline_dir(name)
    if pipeline_dir.exists():
        return f"Pipeline folder already exists: {pipeline_dir.relative_to(REPO_ROOT)}"

    pipeline_dir.mkdir(parents=True, exist_ok=False)

    content_path = pipeline_dir / "pipeline-content.json"
    content_path.write_text(
        json.dumps({"properties": {"activities": []}}, indent=2) + "\n",
        encoding="utf-8",
    )

    platform_path = pipeline_dir / ".platform"
    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": "DataPipeline",
            "displayName": name,
        },
        "config": {
            "version": "2.0",
            "logicalId": str(uuid.uuid4()),
        },
    }
    platform_path.write_text(json.dumps(platform, indent=2) + "\n", encoding="utf-8")

    return (
        f"Created {content_path.relative_to(REPO_ROOT)} and "
        f"{platform_path.relative_to(REPO_ROOT)}"
    )


@tool(approval_mode="never_require")
def write_pipeline_content(
    name: Annotated[
        str,
        Field(description="Kebab-case pipeline name previously passed to create_pipeline."),
    ],
    content: Annotated[
        str,
        Field(description="Full pipeline-content.json body as a JSON string. Overwrites the file."),
    ],
) -> str:
    """Overwrite ``pipelines/<name>.DataPipeline/pipeline-content.json``.

    The pipeline folder must already exist (call ``create_pipeline`` first).
    The content must be valid JSON.
    """
    pipeline_dir = _pipeline_dir(name)
    if not pipeline_dir.exists():
        return (
            f"Pipeline folder does not exist: {pipeline_dir.relative_to(REPO_ROOT)}. "
            "Call create_pipeline first."
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})"

    content_path = pipeline_dir / "pipeline-content.json"
    content_path.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
    return f"Wrote {content_path.relative_to(REPO_ROOT)}"


@tool(approval_mode="never_require")
def create_notebook(
    name: Annotated[
        str,
        Field(description="Notebook display name (e.g. 'Daily Sales Load')."),
    ],
    description: Annotated[
        str,
        Field(description="Optional notebook description."),
    ] = "",
) -> str:
    """Create ``notebooks/<name>.Notebook/`` with the Fabric Git layout.

    Writes ``notebook-content.py`` (a minimal pyspark notebook skeleton) and
    ``.platform`` (Fabric Git integration metadata). Fails if the folder
    already exists.
    """
    notebook_dir = _notebook_dir(name)
    if notebook_dir.exists():
        return f"Notebook folder already exists: {notebook_dir.relative_to(REPO_ROOT)}"

    notebook_dir.mkdir(parents=True, exist_ok=False)

    content_path = notebook_dir / "notebook-content.py"
    content_path.write_text(NOTEBOOK_SKELETON, encoding="utf-8")

    logical_id = str(uuid.uuid4())
    platform_path = notebook_dir / ".platform"
    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": "Notebook",
            "displayName": name,
            "description": description,
        },
        "config": {
            "version": "2.0",
            "logicalId": logical_id,
        },
    }
    platform_path.write_text(json.dumps(platform, indent=2) + "\n", encoding="utf-8")

    return (
        f"Created {content_path.relative_to(REPO_ROOT)} and "
        f"{platform_path.relative_to(REPO_ROOT)} (logicalId={logical_id}). "
        f"Use this logicalId as the notebookId in any pipeline TridentNotebook activity "
        f"that references this notebook."
    )


@tool(approval_mode="never_require")
def write_notebook_content(
    name: Annotated[
        str,
        Field(description="Notebook name previously passed to create_notebook."),
    ],
    content: Annotated[
        str,
        Field(
            description=(
                "Full notebook-content.py body. Must include the Fabric "
                "'# Fabric notebook source' header and '# METADATA' / '# CELL' "
                "marker blocks. Overwrites the file verbatim."
            )
        ),
    ],
) -> str:
    """Overwrite ``notebooks/<name>.Notebook/notebook-content.py``.

    The notebook folder must already exist (call ``create_notebook`` first).
    Content is written verbatim; the agent is responsible for keeping the
    Fabric notebook marker comments intact.
    """
    notebook_dir = _notebook_dir(name)
    if not notebook_dir.exists():
        return (
            f"Notebook folder does not exist: {notebook_dir.relative_to(REPO_ROOT)}. "
            "Call create_notebook first."
        )

    content_path = notebook_dir / "notebook-content.py"
    if not content.endswith("\n"):
        content += "\n"
    content_path.write_text(content, encoding="utf-8")
    return f"Wrote {content_path.relative_to(REPO_ROOT)}"


def _azdo_config() -> tuple[str, str, str, str, str]:
    org_url = os.environ.get("AZDO_ORG_URL")
    project = os.environ.get("AZDO_PROJECT")
    repo = os.environ.get("AZDO_REPO")
    base_branch = os.environ.get("AZDO_DEFAULT_BRANCH", "main")
    pat = os.environ.get("AZDO_PAT")
    missing = [
        n
        for n, v in (
            ("AZDO_ORG_URL", org_url),
            ("AZDO_PROJECT", project),
            ("AZDO_REPO", repo),
            ("AZDO_PAT", pat),
        )
        if not v
    ]
    if missing:
        raise RuntimeError(
            f"Missing required Azure DevOps env vars: {', '.join(missing)}"
        )
    return org_url, project, repo, base_branch, pat  # type: ignore[return-value]


def _git_client():
    from azure.devops.connection import Connection  # local import: optional dep
    from msrest.authentication import BasicAuthentication

    org_url, project, repo, base_branch, pat = _azdo_config()
    connection = Connection(
        base_url=org_url, creds=BasicAuthentication("", pat)
    )
    return connection.clients.get_git_client(), org_url, project, repo, base_branch


def _repo_file_exists(git_client, project: str, repo: str, path: str, branch: str) -> bool:
    from azure.devops.exceptions import AzureDevOpsServiceError
    from azure.devops.v7_1.git.models import GitVersionDescriptor

    try:
        git_client.get_item(
            repository_id=repo,
            path=path,
            project=project,
            version_descriptor=GitVersionDescriptor(
                version=branch, version_type="branch"
            ),
        )
        return True
    except AzureDevOpsServiceError:
        return False


@tool(approval_mode="never_require")
def create_pull_request(
    pipeline_name: Annotated[
        str,
        Field(description="Pipeline name previously passed to create_pipeline."),
    ],
    title: Annotated[
        str,
        Field(description="Pull request title."),
    ],
    description: Annotated[
        str,
        Field(description="Pull request description (markdown supported)."),
    ] = "",
    notebook_names: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional list of notebook names (previously passed to "
                "create_notebook) to include in the same PR alongside the "
                "pipeline. Use this for any notebooks the pipeline depends on."
            )
        ),
    ] = None,
    source_branch: Annotated[
        str | None,
        Field(
            description=(
                "Branch name to push commits to. If omitted, defaults to "
                "'pipeline/<pipeline_name>-<unix-timestamp>'."
            )
        ),
    ] = None,
) -> str:
    """Push the pipeline and any related notebooks to Azure DevOps as one PR.

    Bundles ``pipelines/<pipeline_name>.DataPipeline/`` plus each
    ``notebooks/<name>.Notebook/`` folder into a single commit on a new branch
    and opens a PR against ``AZDO_DEFAULT_BRANCH``. Returns the PR URL on
    success.
    """
    items: list[tuple[Path, str, str]] = []

    pipeline_dir = _pipeline_dir(pipeline_name)
    if not pipeline_dir.exists():
        return (
            f"Pipeline folder does not exist: {pipeline_dir.relative_to(REPO_ROOT)}. "
            "Call create_pipeline first."
        )
    items.append((pipeline_dir, f"/{pipeline_name}.DataPipeline", "Pipeline"))

    for nb_name in notebook_names or []:
        nb_dir = _notebook_dir(nb_name)
        if not nb_dir.exists():
            return (
                f"Notebook folder does not exist: {nb_dir.relative_to(REPO_ROOT)}. "
                "Call create_notebook first."
            )
        items.append((nb_dir, f"/{nb_name}.Notebook", "Notebook"))

    return _open_pr_for_items(
        items=items,
        default_branch_prefix=f"pipeline/{pipeline_name}",
        title=title,
        description=description,
        source_branch=source_branch,
    )


def _open_pr_for_items(
    *,
    items: list[tuple[Path, str, str]],
    default_branch_prefix: str,
    title: str,
    description: str,
    source_branch: str | None,
) -> str:
    from azure.devops.v7_1.git.models import (
        Change,
        GitCommitRef,
        GitPullRequest,
        GitPush,
        GitRefUpdate,
        ItemContent,
    )

    for local_dir, _, kind in items:
        if not any(p.is_file() for p in local_dir.iterdir()):
            return f"{kind} folder is empty: {local_dir.relative_to(REPO_ROOT)}"

    git_client, org_url, project, repo, base_branch = _git_client()

    refs = list(
        git_client.get_refs(
            repository_id=repo, project=project, filter=f"heads/{base_branch}"
        )
    )
    if not refs:
        return f"Base branch '{base_branch}' not found in {project}/{repo}."
    base_sha = refs[0].object_id

    branch = source_branch or f"{default_branch_prefix}-{int(time.time())}"
    existing = list(
        git_client.get_refs(
            repository_id=repo, project=project, filter=f"heads/{branch}"
        )
    )
    if existing:
        return (
            f"Branch '{branch}' already exists in {project}/{repo}. "
            "Pick a different source_branch."
        )

    changes: list = []
    for local_dir, repo_folder, _ in items:
        for local_file in sorted(p for p in local_dir.iterdir() if p.is_file()):
            repo_path = f"{repo_folder}/{local_file.name}"
            change_type = (
                "edit"
                if _repo_file_exists(git_client, project, repo, repo_path, base_branch)
                else "add"
            )
            changes.append(
                Change(
                    change_type=change_type,
                    item={"path": repo_path},
                    new_content=ItemContent(
                        content=local_file.read_text(encoding="utf-8"),
                        content_type="rawText",
                    ),
                )
            )

    push = GitPush(
        ref_updates=[
            GitRefUpdate(
                name=f"refs/heads/{branch}",
                old_object_id=base_sha,
            )
        ],
        commits=[GitCommitRef(comment=title, changes=changes)],
    )
    git_client.create_push(push=push, repository_id=repo, project=project)

    pr = git_client.create_pull_request(
        git_pull_request_to_create=GitPullRequest(
            source_ref_name=f"refs/heads/{branch}",
            target_ref_name=f"refs/heads/{base_branch}",
            title=title,
            description=description,
        ),
        repository_id=repo,
        project=project,
    )

    pr_url = f"{org_url}/{project}/_git/{repo}/pullrequest/{pr.pull_request_id}"
    folder_summary = ", ".join(local_dir.name for local_dir, _, _ in items)
    return (
        f"Opened PR #{pr.pull_request_id} on branch '{branch}' "
        f"with {folder_summary}: {pr_url}"
    )


def _build_agent() -> Agent:
    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    model = os.environ.get("FOUNDRY_MODEL")

    if not project_endpoint or not model:
        raise RuntimeError(
            "FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL must be set "
            "(see src/.env.example)."
        )

    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=AzureCliCredential(),
    )

    return Agent(
        client=client,
        name="PipelineCreationAgent",
        instructions=_load_instructions(),
        tools=[
            create_pipeline,
            write_pipeline_content,
            create_notebook,
            write_notebook_content,
            create_pull_request,
        ],
    )


async def main() -> None:
    agent = _build_agent()
    session = agent.create_session()

    PIPELINES_ROOT.mkdir(exist_ok=True)
    NOTEBOOKS_ROOT.mkdir(exist_ok=True)

    print("Pipeline Creation Agent. Type 'exit' or Ctrl+C to quit.\n")
    print(
        "Agent: Hi! I'm the Pipeline Creation Agent. I'll help you build a "
        "Microsoft Fabric data pipeline and write it to disk as we go.\n"
        "       To get started, what kebab-case name would you like for the "
        "new pipeline (e.g. 'daily-sales-load')?"
    )

    loop = asyncio.get_running_loop()
    while True:
        try:
            user_input = await loop.run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        print("Agent: ", end="", flush=True)
        async for chunk in agent.run(user_input, session=session, stream=True):
            if chunk.text:
                print(chunk.text, end="", flush=True)
        print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
