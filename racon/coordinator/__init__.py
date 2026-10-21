"""RACoN Coordinator Package."""
from racon.coordinator.invites import InviteManager
from racon.coordinator.registry import NodeRegistry, NodeRecord
from racon.coordinator.tasks import TaskManager
from racon.coordinator.ingress import PublicIngress, TailscaleFunnelIngress, IngressStatus

def __getattr__(name: str):
    if name in ("create_worker_api", "create_admin_api"):
        from racon.coordinator import server
        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "InviteManager",
    "NodeRegistry",
    "NodeRecord",
    "TaskManager",
    "PublicIngress",
    "TailscaleFunnelIngress",
    "IngressStatus",
    "create_worker_api",
    "create_admin_api",
]
