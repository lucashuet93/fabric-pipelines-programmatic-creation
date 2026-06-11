# Programmatically Creating Microsoft Fabric Pipelines

Use the repo as a guide for creating and updating Microsoft Fabric data pipelines from source-controlled files instead of from the Fabric portal canvas.

The key idea is simple: a Fabric pipeline is a small folder of files. Commit that folder to a Git-integrated Fabric workspace, run **Update from Git**, and Fabric materializes the pipeline. Everything before the sync step is ordinary file generation, review, and Git workflow.

The repo also includes a working **Pipeline Creation Agent** built with Microsoft Agent Framework and Microsoft Foundry. The agent puts the process into practice: it gathers requirements conversationally, writes the Fabric artifact files, and can open a pull request with the generated pipeline and notebooks.

## The General Process

Programmatic pipeline creation has two distinct phases:

1. **Generate Fabric-compatible artifacts in Git.** Create or update the `.DataPipeline` and related `.Notebook` folders, including their `.platform` descriptors and content files.
2. **Sync Git into Fabric.** Merge the changes into the branch connected to the Fabric workspace, then trigger **Update from Git** manually in the portal or programmatically through the Fabric REST API.

That separation is useful. The artifact-generation phase can be handled by an agent, script, CLI, CI job, template engine, or hand-authored files. Fabric only needs the final Git-tracked folder structure.

A typical lifecycle looks like the following:

1. Decide which Fabric items are needed: pipeline, notebooks, dataflows, lakehouses, and so on.
2. Create one folder per Fabric item using Fabric's Git integration layout.
3. Generate stable `logicalId` values for new items and keep existing `logicalId` values unchanged when updating items.
4. Write the item content files, such as `pipeline-content.json` or `notebook-content.py`.
5. Commit the folders to the Git-integrated repo, preferably through a pull request.
6. Merge the PR into the connected branch.
7. Trigger **Update from Git** so Fabric reads the folders and creates or updates the workspace items.

Only the final sync step talks to Fabric. The rest is normal source control.

## Fabric Artifact Layout

In the Fabric portal, a data pipeline appears as a drag-and-drop canvas. In Git, the same pipeline is represented by a folder:

```text
<pipeline-name>.DataPipeline/
├── .platform
└── pipeline-content.json
```

The folder is the pipeline. When Fabric exports a pipeline, it exports that folder. When Fabric imports or syncs a pipeline from Git, it reads that folder.

Notebooks follow the same pattern:

```text
<notebook-name>.Notebook/
├── .platform
└── notebook-content.py
```

Other Fabric item types use the same general idea: a folder named `<displayName>.<ItemType>` plus a `.platform` descriptor and one or more item-specific content files.

### `.platform`

`.platform` is the Fabric item descriptor. It tells Fabric what kind of item the folder represents, what display name to show, and which stable ID identifies the item across syncs.

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
  "metadata": {
    "type": "DataPipeline",
    "displayName": "daily-sales-load"
  },
  "config": {
    "version": "2.0",
    "logicalId": "bf568e17-d37c-85ed-454c-6cfc7d86d4ad"
  }
}
```

Key fields:

- `metadata.type`: the Fabric item kind, such as `DataPipeline`, `Notebook`, or `Lakehouse`.
- `metadata.displayName`: the name shown in the Fabric workspace.
- `config.logicalId`: the stable identifier for the item. Generate it once for a new item and do not change it afterward.

Cross-item references use `logicalId`, not display name. For example, a pipeline activity that runs a notebook references the notebook's `.platform` `logicalId`.

### `pipeline-content.json`

`pipeline-content.json` defines the pipeline graph. Fabric uses the same general JSON model as Azure Data Factory pipelines: a top-level `properties` object with an `activities` array.

```json
{
  "properties": {
    "activities": [
      {
        "name": "Run Sales Notebook",
        "type": "TridentNotebook",
        "typeProperties": {
          "notebookId": "cd1efc69-e744-8796-4f1e-e432ff8de6e3",
          "workspaceId": "00000000-0000-0000-0000-000000000000"
        },
        "policy": {
          "timeout": "0.12:00:00",
          "retry": 0,
          "retryIntervalInSeconds": 30,
          "secureInput": false,
          "secureOutput": false
        },
        "dependsOn": []
      }
    ]
  }
}
```

Important conventions:

- The top-level shape is `{ "properties": { "activities": [...] } }`.
- Each activity has a `name`, `type`, activity-specific `typeProperties`, optional `policy`, and `dependsOn` array.
- `dependsOn` orders activities inside the same pipeline. It does not link a pipeline to a notebook.
- A notebook activity uses `type: "TridentNotebook"` and sets `typeProperties.notebookId` to the notebook item's `logicalId`.
- In Git-tracked pipeline JSON, `typeProperties.workspaceId` should be the zero GUID: `"00000000-0000-0000-0000-000000000000"`. Fabric resolves the actual workspace during **Update from Git**.

## Git Integration Deployment Model

A Fabric workspace can be connected to a Git repository. Once connected, the Git repo becomes the source of truth for supported Fabric items.

The basic deployment model is:

1. The repo contains folders such as `daily-sales-load.DataPipeline` and `Load Sales.Notebook`.
2. A pull request adds or updates those folders.
3. The pull request is reviewed and merged into the branch connected to Fabric.
4. Fabric runs **Update from Git**.
5. Fabric creates or updates the corresponding workspace items.

Fabric also exposes REST APIs for the sync operations, so the last step can be automated:

- [Commit to Git](https://learn.microsoft.com/rest/api/fabric/core/git/commit-to-git?tabs=HTTP) writes portal changes back to Git.
- [Update from Git](https://learn.microsoft.com/rest/api/fabric/core/git/update-from-git?tabs=HTTP) applies Git changes to the workspace.

You do not need a Fabric pipeline-creation API for the core workflow. You need valid artifact files, a Git-integrated workspace, and an update-from-Git sync.

## Creating A Pipeline By File Generation

To create a new pipeline without using the Fabric portal canvas:

1. Choose a display name and create a folder named `<displayName>.DataPipeline`.
2. Generate a new UUID for `config.logicalId`.
3. Write `.platform` with `metadata.type: "DataPipeline"`, the display name, and the generated `logicalId`.
4. Write `pipeline-content.json`. Use an empty activity list for a blank pipeline or populate the `activities` array with Fabric-compatible activity definitions.
5. Commit and push the folder to the Git-integrated repo.
6. Trigger **Update from Git** in the Fabric workspace.

To update an existing pipeline, edit the existing folder and keep its `.platform` `logicalId` unchanged. Fabric uses that stable ID to understand that the Git folder updates an existing item rather than creating a different one.

### Adding A Notebook Dependency

When a pipeline runs a notebook, create or identify the notebook item first so the pipeline can reference its `logicalId`.

Recommended flow:

1. Create `<notebook-name>.Notebook/` with its own `.platform` and `notebook-content.py`.
2. Copy the notebook's `.platform` `config.logicalId`.
3. Add a `TridentNotebook` activity to the pipeline with that value as `typeProperties.notebookId`.
4. Commit the notebook and pipeline folders in the same PR.
5. Merge and run **Update from Git**.

Keeping related items in the same PR prevents Fabric from seeing a pipeline that references a notebook not yet present in the workspace.

## Agent Implementation

The included Pipeline Creation Agent is one implementation of the file-generation phase. It does not change the deployment model above; it automates the tedious parts of creating the right folders and JSON.

The agent can:

- Create `pipelines/<name>.DataPipeline/` with `.platform` and `pipeline-content.json`.
- Create `notebooks/<name>.Notebook/` with `.platform` and `notebook-content.py`.
- Write pipeline and notebook content as the conversation evolves.
- Preserve the Fabric conventions for notebook references, zero-GUID workspace IDs, and dependency ordering.
- Push the generated artifacts to Azure DevOps and open a pull request.

The agent's behavior is defined in [src/instructions.md](src/instructions.md), and the tool implementation lives in [src/agent.py](src/agent.py).

### Agent Workflow

At runtime, the agent follows the same process a script or human would follow:

1. Ask for the pipeline name.
2. Create the local `.DataPipeline` folder and generate its `logicalId`.
3. Gather requirements for activities, notebooks, dependencies, and content.
4. Write complete artifact files to disk after meaningful changes.
5. Create any notebooks the pipeline needs and use their generated `logicalId` values in `TridentNotebook` activities.
6. When the user confirms, open a PR containing the pipeline and any related notebooks.
7. After the PR merges, Fabric **Update from Git** brings the items into the workspace.

In practice, the agent is a conversational front end over the general Git-based artifact workflow.

## Running The Agent

Prerequisites:

- Python 3.10+
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), used by `AzureCliCredential`
- An Microsoft Foundry project with a deployed chat model
- An Azure DevOps repo connected to a Fabric workspace through Git integration
- An Azure DevOps PAT with `Code (Read & Write)` and `Pull Request (Read & Write)` scopes

Create and activate a virtual environment from the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r src/requirements.txt
```

Copy the environment template and fill in real values:

```bash
cp src/.env.example src/.env
```

```text
FOUNDRY_PROJECT_ENDPOINT=https://your-project.services.ai.azure.com
FOUNDRY_MODEL=gpt-4o
FABRIC_WORKSPACE_ID=00000000-0000-0000-0000-000000000000
AZDO_ORG_URL=https://dev.azure.com/<org>
AZDO_PROJECT=<project>
AZDO_REPO=<repo>
AZDO_DEFAULT_BRANCH=main
AZDO_PAT=<personal-access-token>
```

Sign in to Azure for the Foundry credential:

```bash
az login
```

Run the agent:

```bash
python src/agent.py
```

Example request:

```text
Create a pipeline named daily-sales-load that runs a notebook called Load Sales.
```

The agent writes artifacts under `pipelines/` and `notebooks/`. When you are ready to publish, ask it to open a PR. After the PR merges, run **Update from Git** in the Fabric workspace or call the Fabric REST API.

## Repository Structure

```text
examples/
  notebooks/      Example Fabric notebook folder
  pipelines/      Example Fabric pipeline folder
notebooks/        Generated notebook artifacts
pipelines/        Generated pipeline artifacts
src/
  agent.py        Agent runtime and tool implementations
  instructions.md Agent system instructions
  requirements.txt
```

## References

- [Fabric Git integration overview](https://learn.microsoft.com/fabric/cicd/git-integration/intro-to-git-integration)
- [Fabric data pipeline activity reference](https://learn.microsoft.com/fabric/data-factory/activity-overview)
- [Fabric Git integration REST API](https://learn.microsoft.com/rest/api/fabric/core/git)
