# lumid-flowmesh-plugin

FlowMesh plugin that bridges lum.id identity, permission checking, Runmesh billing, and supplier attribution. Loaded into a FlowMesh Server process via `FLOWMESH_PLUGINS=lumid_flowmesh_plugin`.

## What it provides

| Hook | Behaviour |
|---|---|
| `IdentityProvider` | Resolves bearer tokens via `POST {LUM_ID_BASE_URL}/oauth/introspect`. Accepts lum.id JWT and `lm_pat_*` PATs. Caches active introspect responses for 60 s, sha256-keyed, capped at 10 k entries. lum.id scopes pass through verbatim onto `PrincipalContext.scopes`. Stashes `principal_id → email` for later use by the usage sink. |
| `PermissionChecker` | Admin-bypass + scope-driven kind-level checks + grant-driven concrete-id checks. See [Scope vocabulary](#scope-vocabulary) below. Reads grants from the SQLite ACL written by `ResourceRegistrar`. |
| `ResourceRegistrar` | Mirrors FlowMesh's resource lifecycle (`register` on create, `deregister` on hard-delete, `refresh` + `purge_stale` at startup reconcile) into a SQLite grants table at `LUMID_ACL_DB_PATH`. The table is keyed by `(kind, id, principal_id)`, so multiple principals can hold grants on the same resource. Default path lives under FlowMesh's `FLOWMESH_PLUGIN_DATA_DIR` mount so the ACL survives restarts. |
| `SubmissionGuard` | Optional GPU-rental balance preflight against Runmesh. Off by default (`LUMID_BALANCE_GUARD=on` to enable). Fails open on Runmesh outage. |
| `UsageSink` | Mirrors usage rows to `POST {RUNMESH_BILLING_BASE_URL}/billing/flowmesh-entry` with `X-Bridge-Secret`. With this plugin as the sole `IdentityProvider`, every authenticated principal came through our resolve path, so every row is forwarded — *except* rows whose `principal_id` isn't in the email cache (anonymous or pre-restart principals Runmesh can't bill). One POST per row; failures logged and dropped. |
| `SupplierResolver` | Returns `worker.namespace` as the supplier id at dispatch time. |

`install()` is an `@asynccontextmanager`: it opens the ACL SQLite engine, bootstraps the schema, prunes stale rows, yields the bindings, and disposes the engine on FastAPI shutdown.

## Scope vocabulary

lum.id PATs mint against this list; the `PermissionChecker` reads it:

| Scope | Grants |
|---|---|
| `*` / `flowmesh:*` / `flowmesh:admin` | Admin bypass — all kinds, all actions. |
| `flowmesh:workflows:read` / `flowmesh:tasks:read` / `flowmesh:results:read` / `flowmesh:nodes:read` / `flowmesh:workers:read` / `flowmesh:system:read` | Call kind-level READ endpoints. Returned resources are filtered to those the principal holds a grant on. |
| `flowmesh:workflows:write` | Create workflows. |
| `flowmesh:nodes:write` | Register nodes. |
| `flowmesh:workers:write` | Register workers. |

Concrete-id access requires a grant on the resource (admin aside).

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `LUM_ID_BASE_URL` | no | `https://lum.id` | Identity provider base URL. |
| `RUNMESH_BILLING_BASE_URL` | yes (for billing) | — | e.g. `https://kv.run:8000/Runmesh`. Empty disables sink + guard. |
| `FLOWMESH_BRIDGE_SECRET` | yes (for billing) | — | Shared secret used as `X-Bridge-Secret`. |
| `LUMID_BALANCE_GUARD` | no | `off` | `on` to enable preflight balance check. |
| `LUMID_ORG_ID` | no | `lumid` | Stamped on the `PrincipalContext.org_id` returned by the IdentityProvider. Used by the SubmissionGuard to scope its check to lumid principals; the UsageSink ignores it (task records don't preserve the PrincipalContext's `org_id`). |
| `LUMID_ACL_DB_PATH` | no | `/app/plugin-data/lumid_acl.sqlite` | SQLite file for the `ResourceRegistrar` / `PermissionChecker` grants table. The default path lives under FlowMesh's `FLOWMESH_PLUGIN_DATA_DIR` mount; override only if you keep plugin state elsewhere. |

## Loading

Both paths set the same env vars:

```ini
FLOWMESH_PLUGINS=lumid_flowmesh_plugin
LUM_ID_BASE_URL=https://lum.id
RUNMESH_BILLING_BASE_URL=https://kv.run:8000/Runmesh
FLOWMESH_BRIDGE_SECRET=<shared-secret>
```

### Bind-mount the source

Drop the plugin's source tree under the host plugin directory (`${FLOWMESH_PLUGIN_DIR:-./plugins}`) so the server can import it from `/app/plugins`:

```bash
git clone --branch v<version> https://github.com/mlsys-io/lumid.flowmesh-plugin /tmp/lumid-flowmesh-plugin
cp -r /tmp/lumid-flowmesh-plugin/src/lumid_flowmesh_plugin plugins/

flowmesh stack up
```

Requires `sqlalchemy[asyncio]`, `aiosqlite`, `httpx`, `pydantic`, `fastapi`, `lumid-hooks`, `flowmesh-hook` in the server image. If any are missing, use the overlay below.

### Overlay image

Build a derived image that installs the plugin from git:

```dockerfile
FROM ghcr.io/mlsys-io/flowmesh_server:<pinned-tag>
RUN pip install git+https://github.com/mlsys-io/lumid.flowmesh-plugin@v<version>
```

Push to your registry and point the stack at it via `FLOWMESH_REGISTRY` / `FLOWMESH_VERSION` (or `flowmesh stack up --image-tag <tag>`).

## Tests

```bash
uv sync --all-extras
uv run pytest
```
