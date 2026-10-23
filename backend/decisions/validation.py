import copy
import json
import math
import re
from functools import lru_cache

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from backend.decisions.schema import Condition, Transform, Workflow
from backend.errors import DomainError


def check_schema(schema: dict):
    # No remote references: contracts are local and validation must never perform network I/O.
    def inspect(value, depth=0):
        if depth > 20:
            raise DomainError("schema_depth", "Schema nesting is limited to 20 levels.")
        if isinstance(value, dict):
            if "pattern" in value or "patternProperties" in value:
                raise DomainError(
                    "schema_pattern",
                    "Regular expressions are unsupported in contracts. Use enums, length limits, or explicit properties.",
                )
            if "$ref" in value and not str(value["$ref"]).startswith("#/"):
                raise DomainError("remote_schema", "Use local schema references only.")
            if "$dynamicRef" in value:
                raise DomainError("remote_schema", "Dynamic schema references are not supported.")
            for item in value.values():
                inspect(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                inspect(item, depth + 1)

    inspect(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise DomainError("invalid_schema", "Invalid JSON Schema: " + error.message[:200]) from None


def validate_data(value, schema: dict):
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (ValueError, TypeError):
        raise DomainError("invalid_input", "Input must contain finite JSON values.") from None
    if len(encoded) > 262144:
        raise DomainError("input_too_large", "Decision state is limited to 256 KiB.", 413)
    check_schema(schema)
    try:
        Draft202012Validator(schema).validate(value)
    except (ValidationError, RecursionError) as error:
        path = ".".join(map(str, error.absolute_path)) if isinstance(error, ValidationError) else ""
        message = (
            f"does not satisfy the {error.validator} contract"
            if isinstance(error, ValidationError)
            else "Schema reference cycle"
        )
        raise DomainError("input_schema", f"Input {path or '(root)'}: {message}", 422) from None


def path_parts(path: str) -> list[str]:
    parts = path.removeprefix("$.").split(".")
    if not 1 <= len(parts) <= 12 or any(
        not re.fullmatch(r"[\w-]{1,80}", part) or part.startswith("__") for part in parts
    ):
        raise DomainError("invalid_path", "Use a dotted field path, such as sensor.temperature.")
    return parts


MISSING = object()


def lookup(value, path: str, default=None):
    for part in path_parts(path):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            return default
    return value


def assign(value: dict, path: str, item, remove=False):
    parts = path_parts(path)
    target = value
    for part in parts[:-1]:
        if part not in target:
            target[part] = {}
        if not isinstance(target[part], dict):
            raise DomainError("transform_path", "A transformation cannot traverse a non-object field.")
        target = target[part]
    if remove:
        target.pop(parts[-1], None)
    else:
        target[parts[-1]] = item


def condition_matches(condition: Condition | dict, context) -> bool:
    condition = Condition.model_validate(condition) if isinstance(condition, dict) else condition
    actual = lookup(context, condition.path, MISSING)
    expected = condition.value
    if condition.op == "exists":
        return (actual is not MISSING) == (expected is not False)
    if actual is MISSING:
        return False
    try:
        match condition.op:
            case "eq":
                return actual == expected
            case "ne":
                return actual != expected
            case "gt":
                return actual > expected
            case "gte":
                return actual >= expected
            case "lt":
                return actual < expected
            case "lte":
                return actual <= expected
            case "contains":
                return expected in actual
            case "in":
                return actual in expected
    except (TypeError, ValueError):
        return False
    return False


@lru_cache(maxsize=128)
def cached_transform(state_json, transforms_json):
    return _normalize(json.loads(state_json), json.loads(transforms_json))


def normalize(state, transforms: list[Transform | dict]):
    if not transforms:
        return copy.deepcopy(state)
    state_json = json.dumps(state, sort_keys=True, allow_nan=False)
    config_json = json.dumps(
        [item.model_dump() if isinstance(item, Transform) else item for item in transforms],
        sort_keys=True,
        allow_nan=False,
    )
    if len(state_json) + len(config_json) <= 16384:
        return copy.deepcopy(cached_transform(state_json, config_json))
    return _normalize(state, transforms)


def _normalize(state, transforms):
    result = copy.deepcopy(state)
    if transforms and not isinstance(result, dict):
        result = {"text": result}
    for raw in transforms:
        transform = Transform.model_validate(raw) if isinstance(raw, dict) else raw
        current = lookup(result, transform.source or transform.path)
        match transform.op:
            case "set":
                item = transform.value
            case "copy" | "rename":
                item = copy.deepcopy(current)
            case "remove":
                assign(result, transform.path, None, remove=True)
                continue
            case "number":
                try:
                    item = float(current)
                except (ValueError, TypeError):
                    raise DomainError("transform_type", f"Field {transform.path} is not numeric.") from None
                if not math.isfinite(item):
                    raise DomainError(
                        "transform_type", "Numeric transformations must produce finite numbers."
                    )
            case "string":
                item = str(current) if current is not None else ""
            case "boolean":
                item = current is True or str(current).lower() in {"true", "1", "yes"}
            case "pick":
                result = {path: lookup(result, path) for path in transform.value or []}
                continue
        assign(result, transform.path, item)
        if transform.op == "rename" and transform.source and transform.source != transform.path:
            assign(result, transform.source, None, remove=True)
    return result


def default_graph() -> tuple[list[dict], list[dict]]:
    types = ["input", "transform", "inference", "evaluate", "action", "log"]
    nodes = [
        {
            "id": kind,
            "type": kind,
            "name": {
                "inference": "Ask model",
                "evaluate": "Apply policy",
                "action": "Authorized actions",
            }.get(kind, kind.title()),
            "x": 80 + index * 220,
            "y": 140,
            "config": {},
        }
        for index, kind in enumerate(types)
    ]
    edges = [
        {"id": f"{a}-{b}", "source": a, "target": b, "condition": ""}
        for a, b in zip(types, types[1:], strict=False)
    ]
    return nodes, edges


def validate_workflow(value: dict, publish=False) -> tuple[Workflow, list[str]]:
    value = {
        key: item
        for key, item in value.items()
        if key not in {"id", "created_at", "updated_at", "validation", "notice"}
    }
    workflow = Workflow.model_validate(value)
    check_schema(workflow.input_schema)
    check_schema(workflow.output_schema)
    for name in workflow.questions:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", name):
            raise DomainError(
                "question_name",
                "Question identifiers start with a letter and use letters, numbers, or underscores.",
            )
    for transform in workflow.transforms:
        path_parts(transform.path)
        if transform.source:
            path_parts(transform.source)
    for action in workflow.actions:
        check_schema(action.input_schema)
        check_schema(action.output_schema)
        if action.type in {"http", "webhook", "mqtt"} and not action.connection_id:
            raise DomainError("action_connection", "External actions require a saved connection.")
    if not workflow.nodes:
        from backend.decisions.schema import Edge, Node

        nodes, edges = default_graph()
        workflow.nodes = [Node.model_validate(node) for node in nodes]
        workflow.edges = [Edge.model_validate(edge) for edge in edges]
    ids = [node.id for node in workflow.nodes]
    if len(set(ids)) != len(ids):
        raise DomainError("duplicate_node", "Workflow node identifiers must be unique.")
    inputs = [node.id for node in workflow.nodes if node.type == "input"]
    if len(inputs) != 1:
        raise DomainError("input_node", "A workflow needs exactly one input node.")
    outgoing = {node_id: [] for node_id in ids}
    for edge in workflow.edges:
        if edge.source not in outgoing or edge.target not in outgoing:
            raise DomainError("invalid_edge", "Every connection must refer to an existing node.")
        outgoing[edge.source].append(edge.target)
    visited, active = set(), set()

    def visit(node_id):
        if node_id in active:
            raise DomainError(
                "workflow_cycle",
                "Workflow cycles are not supported. Use an explicit bounded interval instead.",
            )
        if node_id in visited:
            return
        active.add(node_id)
        for target in outgoing[node_id]:
            visit(target)
        active.remove(node_id)
        visited.add(node_id)

    for node_id in ids:
        visit(node_id)
    reachable = set()

    def walk(node_id):
        if node_id not in reachable:
            reachable.add(node_id)
            for target in outgoing[node_id]:
                walk(target)

    walk(inputs[0])
    warnings = [f"Unreachable node: {node.name}" for node in workflow.nodes if node.id not in reachable]
    if publish and warnings:
        raise DomainError("unreachable_nodes", "; ".join(warnings))
    return workflow, warnings
