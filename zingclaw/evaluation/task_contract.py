"""Structured task contracts for offline agent evaluation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SUPPORTED_VERIFIER_TYPES = {
    "status_completed",
    "tool_used",
    "tool_not_used",
    "tool_error_absent",
    "file_exists",
    "file_contains",
    "file_not_exists",
    "workspace_diff_contains",
    "safety_blocked",
    "final_answer_contains",
    "command_output_contains",
    "max_tool_calls",
}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _require_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    raise ValueError(f"{field_name} must be an object")


def _normalize_relative_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw:
        raise ValueError(f"{field_name} requires a non-empty relative path")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"{field_name} must be relative, got {raw!r}")
    path = PurePosixPath(raw)
    if ".." in path.parts:
        raise ValueError(f"{field_name} must not escape its fixture, got {raw!r}")
    normalized = path.as_posix().strip("/")
    if not normalized or normalized == ".":
        raise ValueError(f"{field_name} requires a file or directory path")
    return normalized


def _normalize_fixture(value: Any) -> str:
    return _normalize_relative_path(value, field_name="fixture")


@dataclass(slots=True)
class ArtifactSpec:
    """Expected workspace artifact produced or inspected by a task."""

    path: str
    exists: bool = True
    contains: list[str] = field(default_factory=list)
    not_contains: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: str | dict[str, Any]) -> "ArtifactSpec":
        if isinstance(payload, str):
            return cls(path=_normalize_relative_path(payload, field_name="expected_artifacts.path"))
        data = _require_mapping(payload, field_name="expected_artifacts[]")
        path = _normalize_relative_path(data.get("path"), field_name="expected_artifacts.path")
        return cls(
            path=path,
            exists=bool(data.get("exists", True)),
            contains=_string_list(data.get("contains", [])),
            not_contains=_string_list(data.get("not_contains", data.get("notContains", []))),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "exists": self.exists,
        }
        if self.contains:
            payload["contains"] = list(self.contains)
        if self.not_contains:
            payload["not_contains"] = list(self.not_contains)
        return payload


@dataclass(slots=True)
class VerifierSpec:
    """One deterministic verifier declared by a task contract."""

    type: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: str | dict[str, Any]) -> "VerifierSpec":
        if isinstance(payload, str):
            data: dict[str, Any] = {"type": payload}
        else:
            data = _require_mapping(payload, field_name="verifiers[]")
        verifier_type = str(data.get("type") or "").strip()
        if not verifier_type:
            raise ValueError("verifiers[] requires non-empty type")
        if verifier_type not in SUPPORTED_VERIFIER_TYPES:
            supported = ", ".join(sorted(SUPPORTED_VERIFIER_TYPES))
            raise ValueError(f"unsupported verifier type {verifier_type!r}; supported: {supported}")
        params = {key: value for key, value in data.items() if key != "type"}
        normalized = cls(type=verifier_type, params=_normalize_verifier_params(verifier_type, params))
        normalized.validate()
        return normalized

    def validate(self) -> None:
        if self.type in {"tool_used", "tool_not_used"} and not str(self.params.get("name") or "").strip():
            raise ValueError(f"{self.type} verifier requires name")
        if self.type in {"file_exists", "file_not_exists", "file_contains"} and not self.params.get("path"):
            raise ValueError(f"{self.type} verifier requires path")
        if self.type == "file_contains" and not _string_list(
            self.params.get("contains", self.params.get("text"))
        ):
            raise ValueError("file_contains verifier requires contains or text")
        if self.type in {"final_answer_contains", "command_output_contains"} and not _string_list(
            self.params.get("contains", self.params.get("text"))
        ):
            raise ValueError(f"{self.type} verifier requires contains or text")
        if self.type == "workspace_diff_contains" and not self.params.get("path"):
            raise ValueError("workspace_diff_contains verifier requires path")
        if self.type == "max_tool_calls":
            value = self.params.get("max", self.params.get("value", self.params.get("count")))
            if value is None or int(value) < 0:
                raise ValueError("max_tool_calls verifier requires non-negative max")

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **dict(self.params)}


def _normalize_verifier_params(verifier_type: str, params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    if "path" in normalized:
        normalized["path"] = _normalize_relative_path(normalized["path"], field_name=f"{verifier_type}.path")
    if "contains" in normalized:
        normalized["contains"] = _string_list(normalized["contains"])
    if "not_contains" in normalized:
        normalized["not_contains"] = _string_list(normalized["not_contains"])
    if "notContains" in normalized:
        normalized["not_contains"] = _string_list(normalized.pop("notContains"))
    if "text" in normalized:
        normalized["text"] = str(normalized["text"])
    if "name" in normalized:
        normalized["name"] = str(normalized["name"]).strip()
    if verifier_type == "max_tool_calls":
        value = normalized.get("max", normalized.get("value", normalized.get("count")))
        if value is not None:
            normalized["max"] = int(value)
            normalized.pop("value", None)
            normalized.pop("count", None)
    return normalized


@dataclass(slots=True)
class TaskContract:
    """A structured offline evaluation task for a full Agent runtime run."""

    id: str
    input: str
    fixture: str
    category: str = ""
    tags: list[str] = field(default_factory=list)
    purpose: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    expected_artifacts: list[ArtifactSpec] = field(default_factory=list)
    verifiers: list[VerifierSpec] = field(default_factory=list)
    timeout_seconds: int = 120
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskContract":
        data = _require_mapping(payload, field_name="task contract")
        task_id = str(data.get("id") or data.get("name") or "").strip()
        user_input = str(data.get("input") or data.get("user_input") or data.get("userInput") or "").strip()
        if not task_id:
            raise ValueError("task contract requires non-empty id")
        if not user_input:
            raise ValueError(f"task contract {task_id!r} requires non-empty input")
        fixture = _normalize_fixture(data.get("fixture"))
        expected_artifacts = [
            ArtifactSpec.from_dict(item)
            for item in data.get("expected_artifacts", data.get("expectedArtifacts", []))
        ]
        verifiers = [VerifierSpec.from_dict(item) for item in data.get("verifiers", [])]
        contract = cls(
            id=task_id,
            input=user_input,
            fixture=fixture,
            category=str(data.get("category") or "").strip(),
            tags=_unique(_string_list(data.get("tags", []))),
            purpose=str(data.get("purpose") or "").strip(),
            allowed_tools=_unique(_string_list(data.get("allowed_tools", data.get("allowedTools", [])))),
            forbidden_tools=_unique(_string_list(data.get("forbidden_tools", data.get("forbiddenTools", [])))),
            expected_artifacts=expected_artifacts,
            verifiers=verifiers,
            timeout_seconds=int(data.get("timeout_seconds", data.get("timeoutSeconds", 120))),
            metadata=dict(data.get("metadata") or {}),
            version=int(data.get("version") or 1),
        )
        contract.validate()
        return contract

    @classmethod
    def from_file(cls, path: Path) -> "TaskContract":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def validate(self) -> None:
        if not self.id.strip():
            raise ValueError("task contract requires non-empty id")
        if not self.input.strip():
            raise ValueError(f"task contract {self.id!r} requires non-empty input")
        self.fixture = _normalize_fixture(self.fixture)
        if self.timeout_seconds <= 0:
            raise ValueError(f"task contract {self.id!r} requires positive timeout_seconds")
        overlap = set(self.allowed_tools) & set(self.forbidden_tools)
        if overlap:
            raise ValueError(
                f"task contract {self.id!r} has tools listed as both allowed and forbidden: "
                + ", ".join(sorted(overlap))
            )
        if not self.verifiers:
            raise ValueError(f"task contract {self.id!r} requires at least one verifier")
        for verifier in self.verifiers:
            verifier.validate()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": self.version,
            "id": self.id,
            "category": self.category,
            "fixture": self.fixture,
            "input": self.input,
            "tags": list(self.tags),
            "purpose": self.purpose,
            "allowed_tools": list(self.allowed_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "expected_artifacts": [artifact.to_dict() for artifact in self.expected_artifacts],
            "verifiers": [verifier.to_dict() for verifier in self.verifiers],
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
        }
        return payload


def load_task_contract(path: Path) -> TaskContract:
    """Load and validate one task contract JSON file."""
    return TaskContract.from_file(path)


def discover_task_contracts(path: Path) -> list[TaskContract]:
    """Load one task contract file or every JSON contract below a directory."""
    target = Path(path)
    if target.is_file():
        return [load_task_contract(target)]
    if not target.exists():
        raise FileNotFoundError(target)
    contracts: list[TaskContract] = []
    for item in sorted(target.rglob("*.json")):
        contracts.append(load_task_contract(item))
    return contracts
