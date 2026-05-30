# lumid-plugins

lum.id host plugins for the two services that share its identity:

* **`lumid_flowmesh_plugin`** — full FlowMesh adapter: identity, permission checks, resource registrar, Runmesh billing, supplier attribution. Loaded via `FLOWMESH_PLUGINS=lumid_flowmesh_plugin`.
* **`lumid_lumilake_plugin`** — Lumilake adapter: identity only. Loaded via `LUMILAKE_PLUGINS=lumid_lumilake_plugin`. The same `LumidIdentityProvider` powers both, so the bearer Lumilake accepts is the bearer Lumilake forwards to FlowMesh — both sides re-introspect the same string.

Both modules ship in one wheel (`lumid-plugins`) so the lum.id core is implemented once.

## Repo layout

```
src/
├── _shared_core/                 ← physical source of truth (TTLCache,
│   ├── _cache.py                   LumidIdentityProvider, CoreSettings)
│   ├── config.py
│   ├── identity.py
│   └── __init__.py
├── lumid_flowmesh_plugin/
│   ├── _core → ../_shared_core   ← symlink
│   ├── __init__.py / acl.py / permissions.py / ...
└── lumid_lumilake_plugin/
    ├── _core → ../_shared_core   ← symlink
    └── __init__.py
```

Each plugin imports its shared sources as `from ._core import ...` — no plugin reaches across to a sibling plugin. The two `_core` symlinks point at the same physical directory, so editing `src/_shared_core/identity.py` is the single edit that ripples to both adapters.

`scripts/build_hook.py` materializes `_shared_core/` into real `_core/` subdirectories inside each plugin at wheel-build time, because hatchling's tree walk skips symlinks. The shipped wheel therefore contains two physical copies of the shared sources (one inside each plugin namespace) — pip-installed plugins remain self-contained, no top-level `lumid_plugin_core` package needed.

> **Note on cloning**: git stores symlinks as link blobs on POSIX, but Windows checkouts default to writing them as text files unless `git config --global core.symlinks true` was set before the clone. The `tests/test_shared_core.py::test_each_plugin_exposes_core_via_symlink` test catches a broken checkout before it ships.

## FlowMesh plugin: what it provides

| Hook | Behaviour |
|---|---|
| `IdentityProvider` | Resolves bearer tokens via `POST {LUM_ID_BASE_URL}/oauth/introspect`. Accepts lum.id JWT and `lm_pat_*` PATs. Caches active introspect responses for 60 s, sha256-keyed, capped at 10 k entries. lum.id scopes pass through verbatim onto `PrincipalContext.scopes`. Stashes `principal_id → email` for later use by the usage sink. |
| `PermissionChecker` | Admin-bypass + scope-driven kind-level checks + grant-driven concrete-id checks. See [Scope vocabulary](#scope-vocabulary) below. Reads grants from the SQLite ACL written by `ResourceRegistrar`. |
| `ResourceRegistrar` | Mirrors FlowMesh's resource lifecycle (`register` on create, `deregister` on hard-delete, `reconcile` at startup) into a SQLite grants table at `LUMID_ACL_DB_PATH`. The table is keyed by `(kind, id, principal_id)`, so multiple principals can hold grants on the same resource. `reconcile` runs as a single atomic transaction. Backed by the stdlib `sqlite3` module. |
| `SubmissionGuard` | Optional GPU-rental balance preflight against Runmesh. Off by default (`LUMID_BALANCE_GUARD=on` to enable). Fails open on Runmesh outage. |
| `UsageSink` | Mirrors usage rows to `POST {RUNMESH_BILLING_BASE_URL}/billing/flowmesh-entry` with `X-Bridge-Secret`. Forwards each row whose `principal_id` is in the email cache; rows without a cached email (anonymous or pre-restart principals) are skipped. One POST per row; failures logged and dropped. |
| `SupplierResolver` | Returns `worker.namespace` as the supplier id at dispatch time. |

`install()` is an `@asynccontextmanager`: it opens the ACL SQLite connection, bootstraps the schema, yields the bindings, and closes the connection on FastAPI shutdown. Stale grants are dropped by the host's startup `reconcile` sweep through `ResourceRegistrar`.

## Scope vocabulary

lum.id PATs mint against this list; the `PermissionChecker` reads it:

| Scope | Grants |
|---|---|
| `*` / `flowmesh:*` / `flowmesh:admin` | Admin bypass — all kinds, all actions. |
| `flowmesh:workflows:read` / `flowmesh:tasks:read` / `flowmesh:results:read` / `flowmesh:nodes:read` / `flowmesh:workers:read` / `flowmesh:system:read` | Call kind-level READ endpoints. Returned resources are filtered to those the principal holds a grant on. |
| `flowmesh:workflows:write` | Create workflows. |
| `flowmesh:nodes:write` | Register nodes. |
| `flowmesh:workers:write` | Register workers. |
| `flowmesh:results:write` | Upload task results and artifacts. |

Concrete-id access requires a grant on the resource.

## Compatibility

| Plugin | FlowMesh server | `flowmesh-hook` | `lumid-hooks` |
|---|---|---|---|
| 0.1.1 | 0.1.0, 0.1.1 | 0.1.0, 0.1.1 | 0.1.0 |
| 0.2.0 | ≥ 0.1.2 | ≥ 0.1.2 | ≥ 0.2.0 |

The FlowMesh server is not pip-enforceable — the plugin loads into a running server process — so it must be at least the version shown. Plugin 0.2.0 requires the FlowMesh server's `ResourceRegistrar.reconcile_resources` startup sweep, the `/app/plugin-data` writable mount, and the `RESULT`/`WRITE` gate on result and trace uploads, all of which are present from FlowMesh 0.1.2.

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

### Deploy paths

**Option 1 — pip install the built wheel** (recommended; the wheel bakes `_core/` into each plugin, no symlink handling required):

```bash
uv build                         # produces dist/lumid_plugins-<version>-py3-none-any.whl
pip install --no-deps dist/lumid_plugins-<version>-py3-none-any.whl
```

In a custom Dockerfile layer on top of `ghcr.io/mlsys-io/flowmesh_server:<tag>`:

```dockerfile
COPY lumid_plugins-<version>-py3-none-any.whl /tmp/
RUN pip install --no-deps /tmp/lumid_plugins-<version>-py3-none-any.whl
```

**Option 2 — source mount** (drop the source tree under `${FLOWMESH_PLUGIN_DIR:-./plugins}`). The `_core` symlink must be dereferenced by the copy tool or the import resolves to nothing inside the container:

```bash
git clone --branch v<version> https://github.com/mlsys-io/lumid.flowmesh-plugin /tmp/lumid-plugins

# `cp -rL` (`--dereference`) follows the symlink and copies the shared
# sources as real files inside the deployed `_core/`. Plain `cp -r`
# preserves the symlink, which then points outside the deployed tree
# and ImportError-s at FlowMesh startup.
cp -rL /tmp/lumid-plugins/src/lumid_flowmesh_plugin plugins/

flowmesh stack up
```

Equivalents for other tools that default to preserving symlinks:

| Tool | Right flag | Wrong (default) |
|---|---|---|
| `cp -r` | `cp -rL` / `cp -r --dereference` | `cp -r` |
| `rsync -r` | `rsync -rL` / `rsync -r --copy-links` | `rsync -r` |
| `tar c` | `tar c --dereference` | `tar c` |
| Docker classic builder | `COPY --link` (doesn't help) | — must enable BuildKit |
| Docker BuildKit `COPY` | follows symlinks by default | — |

Runtime deps (`httpx`, `pydantic`, `fastapi`, `lumid-hooks`, `flowmesh-hook`) ship with the FlowMesh server image; the ACL store uses the stdlib `sqlite3` module.

## Lumilake plugin: what it provides

| Hook | Behaviour |
|---|---|
| `IdentityProvider` | The same `LumidIdentityProvider` as the FlowMesh plugin — resolves bearers via `POST {LUM_ID_BASE_URL}/oauth/introspect`, returns a `lumid_hooks.PrincipalContext` with the token's scopes verbatim, caches introspect responses for 60 s. |

No `PermissionChecker`, `ResourceRegistrar`, `SubmissionGuard`, or `UsageSink` on the Lumilake side. Lumilake's resource kinds and usage-row shape differ from FlowMesh's; those hooks belong on a separate follow-up rather than reusing the FlowMesh-shaped implementations.

Lumilake forwards the user's bearer to FlowMesh post-submit (`docs/PLUGINS.md` "Runtime Credentials"), so the FlowMesh-side plugin still gates billing, supplier attribution, and permissions — the Lumilake plugin's only job is to resolve the principal up front so the request carries an authenticated identity into the rest of the stack.

### Loading on Lumilake

Set the env vars on the Lumilake server image:

```ini
LUMILAKE_PLUGINS=lumid_lumilake_plugin
LUM_ID_BASE_URL=https://lum.id
LUMID_ORG_ID=lumid
LUMILAKE_REQUIRE_IDENTITY_PROVIDER=1
```

Same two deploy options as the FlowMesh side.

**Option 1 — pip install the built wheel** (recommended). The single wheel ships both plugins, so the same artifact installs the FlowMesh adapter on one host and the Lumilake adapter on the other:

```dockerfile
COPY lumid_plugins-<version>-py3-none-any.whl /tmp/
RUN pip install --no-deps /tmp/lumid_plugins-<version>-py3-none-any.whl
```

**Option 2 — source mount** (see `lumilake_OSS/docs/PLUGINS.md` for the loader contract — plugins live inside the running server process, mounted from a local path on `PYTHONPATH`). Same `-L` rule as FlowMesh; without it, `_core` lands as a dangling symlink and import fails:

```bash
git clone --branch v<version> https://github.com/mlsys-io/lumid.flowmesh-plugin /tmp/lumid-plugins
cp -rL /tmp/lumid-plugins/src/lumid_lumilake_plugin plugins/

lumilake deploy -C ~/lumilake-deploy restart server
```

`lumilake-hook`, `lumid-hooks`, `httpx`, `pydantic`, and `fastapi` ship in the Lumilake server image already.

## Tests

```bash
uv sync --all-extras
uv run pytest
```
