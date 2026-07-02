"""Base abstractions and schema helpers for ZingClaw tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from copy import deepcopy
from typing import Any, TypeVar

_ToolT = TypeVar("_ToolT", bound="Tool")

_JSON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class Schema(ABC):
    """Abstract base for JSON Schema fragments used by tools."""

    @staticmethod
    def resolve_json_schema_type(raw_type: Any) -> str | None:
        if isinstance(raw_type, list):
            return next((item for item in raw_type if item != "null"), None)
        return raw_type if isinstance(raw_type, str) else None

    @staticmethod
    def subpath(path: str, key: str) -> str:
        return f"{path}.{key}" if path else key

    @staticmethod
    def validate_json_schema_value(
        value: Any,
        schema: dict[str, Any],
        path: str = "",
    ) -> list[str]:
        """Validate a value against a JSON Schema fragment."""
        raw_type = schema.get("type")
        nullable = (isinstance(raw_type, list) and "null" in raw_type) or schema.get(
            "nullable",
            False,
        )
        schema_type = Schema.resolve_json_schema_type(raw_type)
        label = path or "parameter"

        if nullable and value is None:
            return []

        if schema_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return [f"{label} should be integer"]
        if schema_type == "number" and (
            not isinstance(value, _JSON_TYPE_MAP["number"]) or isinstance(value, bool)
        ):
            return [f"{label} should be number"]
        if (
            schema_type in _JSON_TYPE_MAP
            and schema_type not in {"integer", "number"}
            and not isinstance(value, _JSON_TYPE_MAP[schema_type])
        ):
            return [f"{label} should be {schema_type}"]

        errors: list[str] = []
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")

        if schema_type in {"integer", "number"}:
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")

        if schema_type == "string":
            if "minLength" in schema and len(value) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")

        if schema_type == "object":
            properties = schema.get("properties", {})
            for required_key in schema.get("required", []):
                if required_key not in value:
                    errors.append(f"missing required {Schema.subpath(path, required_key)}")
            additional = schema.get("additionalProperties", True)
            for key, item in value.items():
                if key in properties:
                    errors.extend(
                        Schema.validate_json_schema_value(
                            item,
                            properties[key],
                            Schema.subpath(path, key),
                        )
                    )
                elif additional is False:
                    errors.append(f"unexpected parameter {Schema.subpath(path, key)}")
                elif isinstance(additional, dict):
                    errors.extend(
                        Schema.validate_json_schema_value(
                            item,
                            additional,
                            Schema.subpath(path, key),
                        )
                    )

        if schema_type == "array":
            if "minItems" in schema and len(value) < schema["minItems"]:
                errors.append(f"{label} must have at least {schema['minItems']} items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append(f"{label} must be at most {schema['maxItems']} items")
            if "items" in schema:
                prefix = f"{path}[{{}}]" if path else "[{}]"
                for index, item in enumerate(value):
                    errors.extend(
                        Schema.validate_json_schema_value(item, schema["items"], prefix.format(index))
                    )

        return errors

    @staticmethod
    def fragment(value: Any) -> dict[str, Any]:
        """Normalize a Schema instance or a raw schema dict."""
        to_json_schema = getattr(value, "to_json_schema", None)
        if callable(to_json_schema):
            return to_json_schema()
        if isinstance(value, dict):
            return value
        raise TypeError(f"Expected schema object or dict, got {type(value).__name__}")

    @abstractmethod
    def to_json_schema(self) -> dict[str, Any]:
        """Return the JSON Schema fragment."""

    def validate_value(self, value: Any, path: str = "") -> list[str]:
        return Schema.validate_json_schema_value(value, self.to_json_schema(), path)


class Tool(ABC):
    """Base class for all ZingClaw tools."""

    _TYPE_MAP = _JSON_TYPE_MAP
    _BOOL_TRUE = frozenset({"true", "1", "yes"})
    _BOOL_FALSE = frozenset({"false", "0", "no"})
    config_key: str = ""
    _plugin_discoverable: bool = True
    _scopes: set[str] = {"core"}

    @staticmethod
    def _resolve_type(raw_type: Any) -> str | None:
        return Schema.resolve_json_schema_type(raw_type)

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in tool calls."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description shown to the model."""

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema describing tool parameters."""

    @property
    def read_only(self) -> bool:
        return False

    @property
    def concurrency_safe(self) -> bool:
        return self.read_only and not self.exclusive

    @property
    def exclusive(self) -> bool:
        return False

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        """Return whether this tool should be registered for the current context."""
        return True

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        """Create one tool instance for the current runtime context."""
        del ctx
        return cls()

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Run the tool."""

    def _cast_object(self, obj: Any, schema: dict[str, Any]) -> dict[str, Any] | Any:
        if not isinstance(obj, dict):
            return obj
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties")
        casted: dict[str, Any] = {}
        for key, value in obj.items():
            if key in properties:
                casted[key] = self._cast_value(value, properties[key])
            elif isinstance(additional, dict):
                casted[key] = self._cast_value(value, additional)
            else:
                casted[key] = value
        return casted

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Apply safe schema-guided casts before validation."""
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            return params
        return self._cast_object(params, schema)

    def _cast_value(self, value: Any, schema: dict[str, Any]) -> Any:
        schema_type = self._resolve_type(schema.get("type"))

        if schema_type == "boolean" and isinstance(value, bool):
            return value
        if schema_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return value
        if schema_type in self._TYPE_MAP and schema_type not in {"boolean", "integer", "array", "object"}:
            expected_type = self._TYPE_MAP[schema_type]
            if isinstance(value, expected_type):
                return value

        if isinstance(value, str) and schema_type in {"integer", "number"}:
            try:
                return int(value) if schema_type == "integer" else float(value)
            except ValueError:
                return value

        if schema_type == "string":
            return value if value is None else str(value)

        if schema_type == "boolean" and isinstance(value, str):
            lowered = value.lower()
            if lowered in self._BOOL_TRUE:
                return True
            if lowered in self._BOOL_FALSE:
                return False
            return value

        if schema_type == "array" and isinstance(value, list):
            item_schema = schema.get("items")
            return [self._cast_value(item, item_schema) for item in value] if item_schema else value

        if schema_type == "object" and isinstance(value, dict):
            return self._cast_object(value, schema)

        return value

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """Validate tool parameters using the local JSON Schema subset."""
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return Schema.validate_json_schema_value(params, {**schema, "type": "object"}, "")

    def parameter_names(self) -> list[str]:
        """Return declared top-level parameter names in stable order."""
        properties = (self.parameters or {}).get("properties", {})
        if not isinstance(properties, dict):
            return []
        return sorted(str(name) for name in properties)

    def format_parameter_hint(self) -> str:
        """Render a short user-facing hint describing accepted parameters."""
        names = self.parameter_names()
        if not names:
            return "This tool takes no parameters."
        return "Accepted keys: " + ", ".join(names) + "."

    @staticmethod
    def error(message: str) -> str:
        """Return a consistently prefixed tool error string."""
        text = str(message).strip()
        if text.startswith("Error:"):
            return text
        return f"Error: {text}"

    @staticmethod
    def ok(message: str) -> str:
        """Return a consistently trimmed success/info string."""
        return str(message).strip()

    def to_schema(self) -> dict[str, Any]:
        """Return the OpenAI-compatible function schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool_parameters(schema: dict[str, Any]) -> Callable[[type[_ToolT]], type[_ToolT]]:
    """Attach a JSON Schema to a Tool subclass via a decorator."""

    def decorator(cls: type[_ToolT]) -> type[_ToolT]:
        frozen = deepcopy(schema)

        @property
        def parameters(self: Any) -> dict[str, Any]:
            return deepcopy(frozen)

        cls.parameters = parameters  # type: ignore[assignment]

        abstract_methods = getattr(cls, "__abstractmethods__", None)
        if abstract_methods is not None and "parameters" in abstract_methods:
            cls.__abstractmethods__ = frozenset(abstract_methods - {"parameters"})  # type: ignore[misc]

        return cls

    return decorator
