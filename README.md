# lumid-flowmesh-plugin

FlowMesh plugin that bridges lum.id identity, Runmesh billing, and supplier attribution. Loaded into a FlowMesh Server process via `FLOWMESH_PLUGINS=lumid_flowmesh_plugin`.

## What it provides

| Hook | Behaviour |
|---|---|
| `IdentityProvider` | Resolves bearer tokens via `POST {LUM_ID_BASE_URL}/oauth/introspect`. Accepts lum.id JWT and `lm_pat_*` PATs. Caches active introspect responses for 60 s, sha256-keyed, capped at 10 k entries. lum.id scopes pass through verbatim onto `PrincipalContext.scopes`. Stashes `principal_id → email` (TTL 24 h, cap 10 k entries) for later use by the usage sink. |
| `PermissionChecker` | Admin-bypass + scope-driven kind-level checks + ownership-driven concrete-id checks. See [Scope vocabulary](#scope-vocabulary) below. Reads ownership from the SQLite ACL written by `ResourceRegistrar`. |
| `ResourceRegistrar` | Mirrors FlowMesh's resource lifecycle (`register` on create, `deregister` on hard-delete) into a SQLite ownership table at `LUMID_ACL_DB_PATH`. Default path lives under FlowMesh's `FLOWMESH_PLUGIN_DATA_DIR` mount so the ACL survives restarts. |
| `SubmissionGuard` | Optional GPU-rental balance preflight against Runmesh. Off by default (`LUMID_BALANCE_GUARD=on` to enable). Fails open on Runmesh outage. |
| `UsageSink` | Mirrors usage rows to `POST {RUNMESH_BILLING_BASE_URL}/billing/flowmesh-entry` with `X-Bridge-Secret`. With this plugin as the sole `IdentityProvider`, every authenticated principal came through our resolve path, so every row is forwarded — *except* rows whose `principal_id` isn't in the email cache (anonymous or pre-restart principals Runmesh can't bill). One POST per row; failures logged and dropped. |
| `SupplierResolver` | Returns `worker.namespace` as the supplier id at dispatch time. |

`install()` is an `@asynccontextmanager`: it opens the ACL SQLite engine, bootstraps the schema, prunes stale rows, yields the bindings, and disposes the engine on FastAPI shutdown.

## Scope vocabulary

lum.id PATs mint against this list; the `PermissionChecker` reads it:

| Scope | Grants |
|---|---|
| `*` / `flowmesh:*` / `flowmesh:admin` | Admin bypass — all kinds, all actions. |
| `flowmesh:workflows:read` / `flowmesh:tasks:read` / `flowmesh:results:read` / `flowmesh:nodes:read` / `flowmesh:workers:read` / `flowmesh:system:read` | Call kind-level READ endpoints. Returned resources are filtered to the principal's own. |
| `flowmesh:workflows:write` | Create workflows. |
| `flowmesh:nodes:write` | Register nodes. |
| `flowmesh:workers:write` | Register workers. |

Concrete-id access is owner-only (admin aside).

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `LUM_ID_BASE_URL` | no | `https://lum.id` | Identity provider base URL. |
| `RUNMESH_BILLING_BASE_URL` | yes (for billing) | — | e.g. `https://kv.run:8000/Runmesh`. Empty disables sink + guard. |
| `FLOWMESH_BRIDGE_SECRET` | yes (for billing) | — | Shared secret used as `X-Bridge-Secret`. |
| `LUMID_BALANCE_GUARD` | no | `off` | `on` to enable preflight balance check. |
| `LUMID_ORG_ID` | no | `lumid` | Stamped on the `PrincipalContext.org_id` returned by the IdentityProvider. Used by the SubmissionGuard to scope its check to lumid principals; the UsageSink ignores it (task records don't preserve the PrincipalContext's `org_id`). |
| `FLOWMESH_API_KEY` | yes | — | FlowMesh's own server/worker bearer. When this plugin is the sole `IdentityProvider`, the key must itself be a token we can resolve (a lum.id JWT or `lm_pat_*`). Workers send it on every server call; the server also resolves it at boot to obtain the system principal that drives `ResourceRegistrar` calls. If the key is unresolvable, boot falls back to a synthetic admin and worker calls fail with 401. |
| `LUMID_ACL_DB_PATH` | no | `/app/plugin-data/lumid_acl.sqlite` | SQLite file for the `ResourceRegistrar` / `PermissionChecker` ownership table. The default path lives under FlowMesh's `FLOWMESH_PLUGIN_DATA_DIR` mount; override only if you keep plugin state elsewhere. |
| `LUMID_ACL_TTL_DAYS` | no | `90` | Prune ACL rows older than this on startup. `0` disables pruning. FlowMesh doesn't replay `register()` at boot, so this TTL bounds the worst-case growth from crash-during-delete; `deregister` is the steady-state cleanup. |

## Loading

Two deployment shapes, depending on whether you need a custom server image.

### Bind-mount (no custom image)

The prebuilt server image puts `/app/plugins` on `PYTHONPATH`, and `flowmesh stack` bind-mounts `${FLOWMESH_PLUGIN_DIR:-./plugins}` into that location. Drop a thin loader in there that re-exports `install` from the installed package:

```bash
mkdir -p plugins/lumid_flowmesh_plugin
cat > plugins/lumid_flowmesh_plugin/__init__.py <<'PY'
from lumid_flowmesh_plugin import install

__all__ = ["install"]
PY

cat >> .env <<'ENV'
FLOWMESH_PLUGINS=lumid_flowmesh_plugin
LUM_ID_BASE_URL=https://lum.id
RUNMESH_BILLING_BASE_URL=https://kv.run:8000/Runmesh
FLOWMESH_BRIDGE_SECRET=<shared-secret>
FLOWMESH_API_KEY=<lum.id JWT or lm_pat_*>
ENV

flowmesh stack up
```

This path only works if the runtime deps (`httpx`, `pydantic`, `fastapi`, `lumid-hooks`, `flowmesh-hook`) are already present in the server image. If not, use the overlay below.

### Overlay Dockerfile (bakes the wheel into a derived image)

```dockerfile
FROM ghcr.io/mlsys-io/flowmesh_server:<pinned-tag>
RUN pip install lumid-flowmesh-plugin==<version>
```

Build, push to your registry, then point the stack at the new tag via `FLOWMESH_REGISTRY` / `FLOWMESH_VERSION` (or `flowmesh stack up --image-tag <tag>`). Set the same env vars as the bind-mount example.

## Tests

```bash
uv sync --all-extras
uv run pytest
```
