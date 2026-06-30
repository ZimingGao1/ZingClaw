"""Security modules."""

from zingclaw.security.workspace_access import WorkspaceAccessPolicy
from zingclaw.security.workspace_policy import (
    WORKSPACE_BOUNDARY_NOTE,
    WorkspaceBoundaryError,
    is_path_allowed,
    is_path_within,
    resolve_allowed_path,
    resolve_path,
)

__all__ = [
    "WORKSPACE_BOUNDARY_NOTE",
    "WorkspaceAccessPolicy",
    "WorkspaceBoundaryError",
    "is_path_allowed",
    "is_path_within",
    "resolve_allowed_path",
    "resolve_path",
]
