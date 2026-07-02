"""Security modules."""

from zingclaw.security.sensitive_paths import sensitive_path_reason
from zingclaw.security.workspace_access import WorkspaceAccessPolicy
from zingclaw.security.workspace_policy import (
    SENSITIVE_PATH_NOTE,
    WORKSPACE_BOUNDARY_NOTE,
    WorkspaceBoundaryError,
    is_path_allowed,
    is_path_within,
    resolve_allowed_path,
    resolve_path,
)

__all__ = [
    "SENSITIVE_PATH_NOTE",
    "WORKSPACE_BOUNDARY_NOTE",
    "WorkspaceAccessPolicy",
    "WorkspaceBoundaryError",
    "is_path_allowed",
    "is_path_within",
    "resolve_allowed_path",
    "resolve_path",
    "sensitive_path_reason",
]
