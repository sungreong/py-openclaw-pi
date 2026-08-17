from __future__ import annotations

from .deps import *


NO_USER_VISIBLE_ANSWER = "The model returned no user-visible answer after internal reasoning was removed."

def _shorten(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    keep = max(1, limit - 80)
    return text[:keep] + f"\n\n...[truncated {len(text) - keep} chars]"


def _text_from_content_blocks(blocks: Any) -> str:
    parts: list[str] = []
    if not isinstance(blocks, list):
        return ""
    for block in blocks:
        if isinstance(block, dict):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block.get("content"), str):
                parts.append(block["content"])
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def extract_text(message_or_chunk: Any) -> str:
    if message_or_chunk is None:
        return ""
    if isinstance(message_or_chunk, str):
        return message_or_chunk
    content = getattr(message_or_chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text = _text_from_content_blocks(content)
        if text:
            return text
    content_blocks = getattr(message_or_chunk, "content_blocks", None)
    if isinstance(content_blocks, list):
        text = _text_from_content_blocks(content_blocks)
        if text:
            return text
    return ""


def sanitize_final_text(text: str) -> str:
    """Remove common model-internal reasoning containers from user-visible output."""
    value = str(text or "")
    value = re.sub(
        r"^\s*(?:(?:<\s*final\s*)?<\|\s*(?:message|assistant|final)\s*\|>\s*|<\s*final\s*>\s*)+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    for tag in ("reasoning", "analysis", "thinking"):
        value = re.sub(
            rf"<\s*{tag}\b[^>]*>.*?<\s*/\s*{tag}\s*>",
            "",
            value,
            flags=re.IGNORECASE | re.DOTALL,
        )
        value = re.sub(
            rf"<\s*{tag}\b[^>]*>.*$",
            "",
            value,
            flags=re.IGNORECASE | re.DOTALL,
        )
        value = re.sub(rf"<\s*/\s*{tag}\s*>", "", value, flags=re.IGNORECASE)
    cleaned = value.strip()
    if cleaned:
        return cleaned
    return NO_USER_VISIBLE_ANSWER


def is_error_tool_result(text: str) -> bool:
    value = str(text or "").lstrip().lower()
    return value.startswith("error:") or value.startswith("error fetching url ")


def _is_tool_like(candidate: Any) -> bool:
    if candidate is None:
        return False
    name = str(getattr(candidate, "name", "")).strip()
    if not name:
        return False
    return callable(getattr(candidate, "invoke", None)) or callable(getattr(candidate, "run", None))


def _safe_tool_name(value: str) -> str:
    # OpenAI function/tool name pattern: ^[a-zA-Z0-9_-]+$
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(value or "").strip()).strip("_-")


def _tool_name_keys(value: str) -> set[str]:
    raw = str(value or "").strip().lower()
    safe = _safe_tool_name(raw).lower()
    return {k for k in {raw, safe} if k}


def _to_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _to_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [item.strip() for item in re.split(r"[,\n]", raw) if item.strip()]
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for item in raw:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(raw).strip()
    return [text] if text else []


def _yaml_scalar(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        body = text[1:-1].strip()
        if not body:
            return []
        return [part.strip().strip("'").strip('"') for part in body.split(",") if part.strip()]
    low = text.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    return text


def _parse_simple_yaml_frontmatter(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    active_list_key: Optional[str] = None
    active_block_key: Optional[str] = None
    block_lines: list[str] = []
    lines = str(text or "").splitlines()
    for raw in lines:
        line = raw.rstrip()
        if active_block_key:
            if line.startswith("  ") or line.startswith("\t"):
                block_lines.append(line[2:] if line.startswith("  ") else line.lstrip("\t"))
                continue
            data[active_block_key] = "\n".join(block_lines).strip()
            active_block_key = None
            block_lines = []
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if m:
            key = m.group(1).strip().lower()
            value = m.group(2).strip()
            if not value:
                data[key] = []
                active_list_key = key
                active_block_key = None
            else:
                if value in {"|", ">"}:
                    active_block_key = key
                    block_lines = []
                    active_list_key = None
                else:
                    data[key] = _yaml_scalar(value)
                    active_list_key = None
                    active_block_key = None
            continue
        m_item = re.match(r"^\s*-\s+(.*)$", line)
        if m_item and active_list_key:
            item = _yaml_scalar(m_item.group(1).strip())
            bucket = data.get(active_list_key)
            if not isinstance(bucket, list):
                bucket = []
                data[active_list_key] = bucket
            bucket.append(item)
    if active_block_key:
        data[active_block_key] = "\n".join(block_lines).strip()
    return data


def _split_frontmatter(content: str) -> tuple[Optional[str], str]:
    text = str(content or "")
    if not text.startswith("---"):
        return None, text
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", text, flags=re.DOTALL)
    if not m:
        return None, text
    return m.group(1), m.group(2)


def _normalize_skill_mode(raw: Any) -> str:
    mode = str(raw or "auto").strip().lower()
    return mode if mode in {"auto", "manual", "off"} else "auto"


def _normalize_plan_mode(raw: Any) -> str:
    mode = str(raw or "off").strip().lower()
    return mode if mode in {"on", "off"} else "off"


def _tool_clone_with_name(tool_obj: Any, new_name: str) -> Any:
    current = str(getattr(tool_obj, "name", "")).strip()
    target = str(new_name or "").strip()
    if not target:
        raise ValueError("tool name cannot be empty")
    if current == target:
        return tool_obj

    clone_candidates = ["model_copy", "copy"]
    for method_name in clone_candidates:
        method = getattr(tool_obj, method_name, None)
        if callable(method):
            try:
                clone = method(deep=True)
                setattr(clone, "name", target)
                return clone
            except Exception:
                pass
    try:
        setattr(tool_obj, "name", target)
        return tool_obj
    except Exception as exc:
        raise ValueError(f"tool rename failed ({current} -> {target}): {exc}") from exc


def _json_schema_type_to_python(spec: Any, model_name: str = "Nested") -> Any:
    if not isinstance(spec, dict):
        return Any
    if isinstance(spec.get("enum"), list) and spec.get("enum"):
        vals = tuple(spec["enum"])
        try:
            return Literal[vals]  # type: ignore[index]
        except Exception:
            return str

    if "anyOf" in spec and isinstance(spec.get("anyOf"), list):
        subs = [
            _json_schema_type_to_python(item, f"{model_name}AnyOf{i}")
            for i, item in enumerate(spec.get("anyOf") or [])
        ]
        if not subs:
            return Any
        base = subs[0]
        for extra in subs[1:]:
            try:
                base = base | extra
            except Exception:
                return Any
        return base

    kind = spec.get("type")
    nullable = False
    if isinstance(kind, list):
        kinds = [k for k in kind if k != "null"]
        nullable = len(kinds) != len(kind)
        kind = kinds[0] if kinds else "string"

    py_type: Any = Any
    if kind == "string":
        py_type = str
    elif kind == "integer":
        py_type = int
    elif kind == "number":
        py_type = float
    elif kind == "boolean":
        py_type = bool
    elif kind == "array":
        item_type = _json_schema_type_to_python(spec.get("items", {}), model_name=f"{model_name}Item")
        py_type = list[item_type]
    elif kind == "object" or ("properties" in spec):
        props = spec.get("properties", {})
        if isinstance(props, dict) and props:
            required = set(spec.get("required", [])) if isinstance(spec.get("required"), list) else set()
            fields: dict[str, tuple[Any, Any]] = {}
            for raw_key, child in props.items():
                key = str(raw_key).strip()
                if not key:
                    continue
                child_t = _json_schema_type_to_python(child, model_name=f"{model_name}_{key}")
                desc = str(child.get("description", "")).strip() if isinstance(child, dict) else ""
                if key in required:
                    fields[key] = (child_t, Field(description=desc))
                else:
                    fields[key] = (Optional[child_t], Field(default=None, description=desc))
            if fields:
                safe_model_name = re.sub(r"[^a-zA-Z0-9_]", "_", model_name)
                py_type = create_model(safe_model_name, **fields)
            else:
                py_type = dict[str, Any]
        else:
            py_type = dict[str, Any]

    if nullable:
        return Optional[py_type]
    return py_type


def _build_args_schema_from_json_schema(schema: Any, model_name: str):
    if not isinstance(schema, dict):
        return None
    if schema.get("type") not in {None, "object"}:
        return None
    props = schema.get("properties", {})
    if not isinstance(props, dict) or not props:
        return None
    required = set(schema.get("required", [])) if isinstance(schema.get("required", []), list) else set()
    fields: dict[str, tuple[Any, Any]] = {}
    for raw_key, spec in props.items():
        key = str(raw_key).strip()
        if not key:
            continue
        py_type = _json_schema_type_to_python(spec, model_name=f"{model_name}_{key}")
        description = ""
        if isinstance(spec, dict):
            description = str(spec.get("description", "")).strip()
        if key in required:
            fields[key] = (py_type, Field(description=description))
        else:
            fields[key] = (Optional[py_type], Field(default=None, description=description))
    if not fields:
        return None
    safe_model_name = re.sub(r"[^a-zA-Z0-9_]", "_", model_name)
    return create_model(safe_model_name, **fields)


def _render_mcp_result(result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)
    items = result.get("content")
    rows: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("type") == "text":
                rows.append(str(item.get("text", "")))
            else:
                rows.append(json.dumps(item, ensure_ascii=False))
    if not rows:
        rows.append(json.dumps(result, ensure_ascii=False))
    output = "\n".join(x for x in rows if str(x).strip()) or "(empty result)"
    if bool(result.get("isError")):
        return f"Error: {output}"
    return output

__all__ = [name for name in globals() if not name.startswith("__")]
