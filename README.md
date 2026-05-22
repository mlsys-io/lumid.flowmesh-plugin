# lumid-flowmesh-plugin

FlowMesh plugin that bridges lum.id identity, permission checking, Runmesh billing, and supplier attribution. Loaded into a FlowMesh Server process via `FLOWMESH_PLUGINS=lumid_flowmesh_plugin`.

## What it provides

| Hook | Behaviour |
|---|---|
| `IdentityProvider` | Resolves bearer tokens via `POST {LUM_ID_BASE_URL}/oauth/introspect`. Accepts lum.id JWT and `lm_pat_*` PATs. Caches active introspect responses for 60 s, sha256-keyed, capped at 10 k entries. lum.id scopes pass through verbatim onto `PrincipalContext.scopes`. Stashes `principal_id → email` for later use by the usage sink. |
| `PermissionChecker` | Admin-bypass + scope-driven kind-level checks + grant-driven concrete-id checks. See [Scope vocabulary](#scope-vocabulary) below. Reads grants from the SQLite ACL written by `ResourceRegistrar`. |
| `ResourceRegistrar` | Mirrors FlowMesh's resource lifecycle (`register` on create, `deregister` on hard-delete, `reconcile` at startup) into a SQLite grants table at `LUMID_ACL_DB_PATH`. The table is keyed by `(kind, id, principal_id)`, so multiple principals can hold grants on the same resource. `reconcile` runs as a single atomic transaction. Backed by the stdlib `sqlite3` module. |
| `SubmissionGuard` | Optional GPU-rental balance preflight against Runmesh. Off by default (`LUMID_BALANCE_GUARD=on` to enable). Fails open on Runmesh outage. |
| `UsageSink` | Mirrors usage rows to `POST {RUNMESH_BILLING_BASE_URL}/billing/flowmesh-entry` with `X-Bridge-Secret`. Forwards each row whose `principal_id` is in the email cache; rows without a cached email (anonymous or pre-restart principals) are skipped. One POST per row; failures logged and dropped. |
| `SupplierResolver` | Returns `worker.namespace` as the supplier id at dispatch time. |

`install()` is an `@asynccontextmanager`: it opens the ACL SQLite connection, bootstraps the schema, prunes stale rows, yields the bindings, and closes the connection on FastAPI shutdown.

## Scope vocabulary

lum.id PATs mint against this list; the `PermissionChecker` reads it:

| Scope | Grants |
|---|---|
| `*` / `flowmesh:*` / `flowmesh:admin` | Admin bypass — all kinds, all actions. |
| `flowmesh:workflows:read` / `flowmesh:tasks:read` / `flowmesh:results:read` / `flowmesh:nodes:read` / `flowmesh:workers:read` / `flowmesh:system:read` | Call kind-level READ endpoints. Returned resources are filtered to those the principal holds a grant on. |
| `flowmesh:workflows:write` | Create workflows. |
| `flowmesh:nodes:write` | Register nodes. |
| `flowmesh:workers:write` | Register workers. |

Concrete-id access requires a grant on the resource.

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `LUM_ID_BASE_URL` | no | `https://lum.id` | Identity provider base URL. |
| `RUNMESH_BILLING_BASE_URL` | yes (for billing) | — | e.g. `https://kv.run:8000/Runmesh`. Empty disables sink + guard. |
| `FLOWMESH_BRIDGE_SECRET` | yes (for billing) | — | Shared secret used as `X-Bridge-Secret`. |
| `LUMID_BALANCE_GUARD` | no | `off` | `on` to enable preflight balance check. |
| `LUMID_ORG_ID` | no | `lumid` | Stamped on the `PrincipalContext.org_id` returned by the IdentityProvider. Used by the SubmissionGuard to scope its check to lumid principals; the UsageSink ignores it (task records don't preserve the PrincipalContext's `org_id`). |
| `LUMID_ACL_DB_PATH` | no | `/app/plugin-data/lumid_acl.sqlite` | SQLite file for the `ResourceRegistrar` / `PermissionChecker` grants table. The default path lives under FlowMesh's `FLOWMESH_PLUGIN_DATA_DIR` mount. |

## Loading

Set the env vars:

```ini
FLOWMESH_PLUGINS=lumid_flowmesh_plugin
LUM_ID_BASE_URL=https://lum.id
RUNMESH_BILLING_BASE_URL=https://kv.run:8000/Runmesh
FLOWMESH_BRIDGE_SECRET=<shared-secret>
```

Drop the plugin's source tree under the host plugin directory (`${FLOWMESH_PLUGIN_DIR:-./plugins}`) so the server can import it from `/app/plugins`:

```bash
git clone --branch v<version> https://github.com/mlsys-io/lumid.flowmesh-plugin /tmp/lumid-flowmesh-plugin
cp -r /tmp/lumid-flowmesh-plugin/src/lumid_flowmesh_plugin plugins/

flowmesh stack up
```

Runtime deps (`httpx`, `pydantic`, `fastapi`, `lumid-hooks`, `flowmesh-hook`) ship with the FlowMesh server image; the ACL store uses the stdlib `sqlite3` module.

## Tests

```bash
uv sync --all-extras
uv run pytest
```
