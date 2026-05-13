# lumid-flowmesh-plugin

FlowMesh V2 plugin that bridges lum.id identity, Runmesh billing, and supplier attribution. Loaded into a FlowMesh Server process via `FLOWMESH_PLUGINS=lumid_flowmesh_plugin`.

## What it provides

| Hook | Behaviour |
|---|---|
| `IdentityProvider` | Resolves bearer tokens via `POST {LUM_ID_BASE_URL}/oauth/introspect`. Accepts lum.id JWT and `lm_pat_*` PATs. Caches active introspect responses for 60 s, sha256-keyed, capped at 10 k entries. Maps `flowmesh:*` scopes to FlowMesh's vocabulary. Stashes `principal_id → email` for later use by the usage sink. |
| `SubmissionGuard` | Optional GPU-rental balance preflight against Runmesh. Off by default (`LUMID_BALANCE_GUARD=on` to enable). Fails open on Runmesh outage. |
| `UsageSink` | Mirrors lumid-tenant usage rows to `POST {RUNMESH_BILLING_BASE_URL}/billing/flowmesh-entry` with `X-Bridge-Secret`. One POST per row; failures logged and dropped. |
| `SupplierResolver` | Returns `worker.namespace` as the supplier id at dispatch time. |

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `LUM_ID_BASE_URL` | no | `https://lum.id` | Identity provider base URL. |
| `RUNMESH_BILLING_BASE_URL` | yes (for billing) | — | e.g. `https://kv.run:8000/Runmesh`. Empty disables sink + guard. |
| `FLOWMESH_BRIDGE_SECRET` | yes (for billing) | — | Shared secret used as `X-Bridge-Secret`. |
| `LUMID_BALANCE_GUARD` | no | `off` | `on` to enable preflight balance check. |
| `LUMID_ORG_ID` | no | `lumid` | Internal org-id stamped on lum.id PrincipalContexts. Usage rows with other org_ids are passed through. |

## Loading

```bash
pip install lumid-flowmesh-plugin
export FLOWMESH_PLUGINS=lumid_flowmesh_plugin
export LUM_ID_BASE_URL=https://lum.id
export RUNMESH_BILLING_BASE_URL=https://kv.run:8000/Runmesh
export FLOWMESH_BRIDGE_SECRET=<shared-secret>
# start the FlowMesh server
```

## Tests

```bash
uv sync --all-extras
uv run pytest
```
