# Programmatically Creating Microsoft Fabric Pipelines

A repo for creating and updating Microsoft Fabric data pipelines **programmatically**, without opening the Fabric portal. The sections below cover the file formats Fabric uses to define pipelines and associated activities, how Git integration should be leveraged to "deploy" those files, and the general process for creating or updating a pipeline from code.

The repo includes a working pipeline creation agent built on Microsoft Agent Framework + Microsoft Foundry. The agent is one consumer of the process outlined below; the process itself stands on its own.

## Fabric Pipeline Artifacts

In the Fabric portal, a data pipeline looks like a drag-and-drop canvas. Under the hood, it is two files:

```
<pipeline-name>.DataPipeline/
├── .platform
└── pipeline-content.json
```

When you export a pipeline from the portal, Fabric zips the `.DataPipeline` folder. When you import one, Fabric unzips it back into the same two-file structure. The pipeline _is_ the folder.

Notebooks follow the identical pattern:

```
<notebook-name>.Notebook/
├── .platform
└── notebook-content.py
```

### `.platform`, the Fabric item descriptor

A small JSON file that tells Fabric what kind of item this is and how to identify it:

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

- **`metadata.type`**: `DataPipeline`, `Notebook`, `Lakehouse`, etc. Tells Fabric which item kind to materialize.
- **`metadata.displayName`**: what shows up in the Fabric portal UI.
- **`config.logicalId`**: a stable GUID that identifies this item across environments. Other items reference it by this ID, not by name. Generate one with `uuid.uuid4()` (or any RFC 4122 UUID generator) when you create a new item, and **never change it** afterwards.

### `pipeline-content.json`, the pipeline definition

The actual pipeline graph. It uses the same JSON schema as Azure Data Factory pipelines:

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

Important things to know:

- The top-level shape is always `{ "properties": { "activities": [...] } }`.
- Each activity has a `name`, a `type` (e.g. `TridentNotebook`, `Copy`, `Lookup`, `IfCondition`, `ForEach`, `ExecutePipeline`), a `typeProperties` block specific to that type, an optional `policy`, and a `dependsOn` array that wires up the DAG.
- **Cross-item references use `logicalId`s, not display names.** When a `TridentNotebook` activity runs `MyNotebook`, its `notebookId` is the `logicalId` from `MyNotebook.Notebook/.platform`.
- **`workspaceId` in the Git-tracked JSON is always the zero-GUID** (`"00000000-0000-0000-0000-000000000000"`). Fabric resolves it to the actual workspace on **Update from Git**. Using a real workspace GUID here is not portable and causes sync errors across environments.
- **`dependsOn` orders activities _within the same pipeline_.** Each entry is `{ "activity": "<other activity's name>", "dependencyConditions": ["Succeeded"] }` (or `Failed` / `Skipped` / `Completed`). It is not how a pipeline points at a notebook; that link is `typeProperties.notebookId`.

## Git Integration

Fabric workspaces can be Git-integrated with an Azure DevOps repo (or GitHub). Once integrated:

1. Each Fabric item maps to one folder in the repo, named `<displayName>.<ItemType>` (e.g. `daily-sales-load.DataPipeline`).
2. The folder contains the two files described above.
3. When you press **Update from Git** in the workspace, Fabric reads every `.DataPipeline` / `.Notebook` / etc. folder it finds and materializes (or updates) the corresponding items.
4. When you press **Commit to Git** from the portal, Fabric writes the current state of each item back out as those same two files.

You do not need any Fabric API to create a pipeline. Just add the right folder to the Git repo and hit "Update from Git." 

Fabric exposes API endpoints for both the ["Commit to Git"](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/commit-to-git?tabs=HTTP) and the ["Update from Git"](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/update-from-git?tabs=HTTP) operations so the sync process itself can be triggered programmatically.

## Pipeline Creation Process

To create a new pipeline programmatically:

1. **Pick a folder name** of the form `<displayName>.DataPipeline`. The display name is what the user will see in Fabric.
2. **Generate a fresh `logicalId`** (any UUID v4 works). Save it; other items that reference this pipeline will need it.
3. **Write `.platform`** with `type: "DataPipeline"`, your chosen display name, and the new `logicalId`.
4. **Write `pipeline-content.json`** with the pipeline definition. Start from `{ "properties": { "activities": [] } }` if you want an empty pipeline; otherwise populate `activities` with the JSON shapes Fabric expects.
5. **Commit and push the folder** to the Git-integrated branch (typically via a PR).
6. **Trigger "Update from Git"** in the Fabric workspace, either manually in the portal or via the Fabric REST API (`POST /workspaces/{workspaceId}/git/updateFromGit`).

Only the last step talks to Fabric at all. Steps 1 through 5 are pure filesystem and Git operations.

### Updating an existing pipeline

Same flow. Edit the existing folder's `pipeline-content.json` (keep the `logicalId` in `.platform` unchanged) and PR the change. Fabric's **Update from Git** diffs the folder and applies the change.

### Adding a notebook the pipeline runs

A pipeline that calls a notebook needs the notebook's `logicalId` in its `TridentNotebook` activity. So the order is:

1. Create the notebook folder first (`<notebook-name>.Notebook/`) and generate its `logicalId`.
2. Reference that `logicalId` in the pipeline's `pipeline-content.json` as `notebookId` (flat under `typeProperties`, not nested in a `notebook` sub-object).
3. Commit both folders in the **same PR** so Fabric never sees a pipeline pointing at a notebook it does not yet know about.

## Agent Overview

The Pipeline Creation Agent is one potential surface for programmatic creation of Fabric Pipelines. It exposes Fabric and AzDO specific tools that allow the agent to create the requisite files for a pipeline and open a new PR against a repo. After the PR merges, a Fabric **Update from Git** operation (click or REST call) pulls the new items into the workspace. 

None of these operations are specific to the agent. Any script that performs the same filesystem and Git operations could be used to programmatically create a pipeline.

### Running the agent

Prerequisites:

- Python 3.10+
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) (used for authentication via `AzureCliCredential`)
- An Azure AI Foundry project with a deployed chat model (e.g. `gpt-4o`)
- An Azure DevOps repo that is Git-integrated with your Fabric workspace, and a PAT with **Code (Read & Write)** and **Pull Request (Read & Write)** scopes

**1. Create and activate a virtual environment** (from the repo root):

```powershell
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
python -m venv .venv
source .venv/bin/activate
```

**2. Install dependencies:**

```bash
pip install -r src/requirements.txt
```

**3. Configure environment variables.** Copy the example file and fill in the values:

```bash
cp src/.env.example src/.env
```

```
FOUNDRY_PROJECT_ENDPOINT=https://your-project.services.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL=gpt-4o
AZDO_ORG_URL=https://dev.azure.com/<org>
AZDO_PROJECT=<project>
AZDO_REPO=<repo>
AZDO_DEFAULT_BRANCH=main
AZDO_PAT=<personal access token>
```

**4. Sign in to Azure** (for the Foundry credential):

```bash
az login
```

**5. Run the agent:**

```bash
python src/agent.py
```

Type a request like "Create a pipeline named `daily-sales-load` that runs a notebook called `Load Sales`," let the agent draft the artifacts, then ask it to open a PR. Type `exit` or press `Ctrl+C` to quit.

## References

- [Fabric Git integration overview](https://learn.microsoft.com/fabric/cicd/git-integration/intro-to-git-integration)
- [Fabric data pipeline activity reference](https://learn.microsoft.com/fabric/data-factory/activity-overview) (per-activity `typeProperties` shapes)
- [Fabric Git integration REST API](https://learn.microsoft.com/rest/api/fabric/core/git), for automating **Update from Git** / **Commit to Git** without the portal
