"""NamespaceSupplierResolver — map a worker's namespace to its supplier id.

Synchronous, runs on the dispatcher hot path. Returns the namespace verbatim
when set, else None to defer to the next resolver.
"""

from flowmesh_hook import WorkerView


class NamespaceSupplierResolver:
    name = "lumid_flowmesh_plugin.supplier"

    def resolve(self, worker: WorkerView) -> str | None:
        return worker.namespace or None
