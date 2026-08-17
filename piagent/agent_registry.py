from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient

_WORKSPACE_EXTENSION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _skill_match_tokens(value: str, *, ascii_min_length: int) -> list[str]:
    tokens = re.findall(r"[^\W_]+", str(value or "").lower(), flags=re.UNICODE)
    return [
        token
        for token in tokens
        if len(token) >= (ascii_min_length if token.isascii() else 2)
    ]

class AgentRegistryMixin:
    def _resolve_workspace_extension_root(self) -> Path:
        raw = str(self.config.workspace_extension_dir or ".piagent").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace_dir)
        except ValueError as exc:
            raise ValueError(f"workspace_extension_dir escapes workspace: {raw}") from exc
        return resolved

    def _workspace_tool_module_refs(self) -> list[str]:
        if not self.config.workspace_extensions_enabled:
            return []
        tools_root = self._resolve_workspace_extension_root() / "tools"
        if not tools_root.is_dir():
            return []
        refs: list[str] = []
        for tool_dir in sorted(path for path in tools_root.iterdir() if path.is_dir()):
            if not _WORKSPACE_EXTENSION_NAME_RE.fullmatch(tool_dir.name):
                continue
            entrypoint = tool_dir / "tool.py"
            if entrypoint.is_file():
                refs.append(str(entrypoint))
        return refs

    def _build_tool_registry(self, extra_tools: Optional[Sequence[Any]]) -> list[Any]:
        builtin_tools = self._build_default_tools()
        self._register_tool_batch(builtin_tools, source="builtin")
        builtin_names = {str(getattr(t, "name", "")).strip() for t in builtin_tools}

        custom_tools = self._load_custom_tools(builtin_names=builtin_names)
        self._register_tool_batch(custom_tools, source="custom")

        used_custom_names = set(builtin_names)
        used_custom_names.update(str(getattr(t, "name", "")).strip() for t in custom_tools)
        raw_inline_custom = [t for t in (extra_tools or []) if _is_tool_like(t)]
        inline_custom = self._normalize_custom_tool_names(
            raw_tools=raw_inline_custom,
            module_short="inline",
            builtin_names=builtin_names,
            used_names=used_custom_names,
        )
        self._register_tool_batch(inline_custom, source="custom")

        mcp_tools = self._load_mcp_tools()
        self._register_tool_batch(mcp_tools, source="mcp")

        all_tools = [*builtin_tools, *custom_tools, *inline_custom, *mcp_tools]
        summary = {
            "total": len(all_tools),
            "builtin": len(builtin_tools),
            "custom": len(custom_tools) + len(inline_custom),
            "mcp": len(mcp_tools),
            "tools": [str(getattr(t, "name", "")) for t in all_tools],
        }
        self._queue_audit("tool_registry_summary", summary)
        return all_tools

    def _register_tool_batch(self, tools: Sequence[Any], source: str) -> None:
        for tool_obj in tools:
            name = str(getattr(tool_obj, "name", "")).strip()
            if not name:
                continue
            self._tool_sources[name] = source

    def _module_name_for_path(self, file_path: Path) -> str:
        base = _safe_tool_name(file_path.stem) or "custom"
        stamp = str(int(time.time() * 1000))
        return f"pi_custom_{base}_{stamp}"

    def _import_custom_module(self, module_ref: str):
        ref = str(module_ref or "").strip()
        if not ref:
            raise ValueError("empty module reference")
        is_file_ref = ref.endswith(".py") or "/" in ref or "\\" in ref
        if is_file_ref:
            candidate = Path(ref)
            if not candidate.is_absolute():
                candidate = self.workspace_dir / candidate
            resolved = candidate.resolve()
            try:
                resolved.relative_to(self.workspace_dir)
            except ValueError as exc:
                raise ValueError(f"custom tool module path escapes workspace: {ref}") from exc
            self.guard.assert_allowed(resolved)
            if not resolved.exists() or not resolved.is_file():
                raise ValueError(f"custom tool module file not found: {resolved}")
            spec = importlib.util.spec_from_file_location(self._module_name_for_path(resolved), resolved)
            if spec is None or spec.loader is None:
                raise ValueError(f"unable to create module spec: {resolved}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        sys.path.insert(0, str(self.workspace_dir))
        try:
            spec = importlib.util.find_spec(ref)
            origin = getattr(spec, "origin", None) if spec is not None else None
            if origin and origin not in {"built-in", "frozen"}:
                origin_path = Path(origin).resolve()
                try:
                    origin_path.relative_to(self.workspace_dir)
                except Exception:
                    pass
                else:
                    self.guard.assert_allowed(origin_path)
            return importlib.import_module(ref)
        finally:
            if sys.path and sys.path[0] == str(self.workspace_dir):
                sys.path.pop(0)

    def _extract_custom_tools_from_module(self, module: types.ModuleType) -> list[Any]:
        if callable(getattr(module, "get_tools", None)):
            loaded = module.get_tools()
        elif hasattr(module, "TOOLS"):
            loaded = getattr(module, "TOOLS")
        else:
            raise ValueError("custom tool module requires get_tools() or TOOLS")
        if not isinstance(loaded, (list, tuple)):
            raise ValueError("custom tool module output must be a list/tuple of tools")
        out = []
        for item in loaded:
            if _is_tool_like(item):
                out.append(item)
        if not out:
            raise ValueError("no valid tools found in module")
        return out

    def _normalize_custom_tool_names(
        self,
        raw_tools: Sequence[Any],
        module_short: str,
        builtin_names: set[str],
        used_names: set[str],
    ) -> list[Any]:
        normalized: list[Any] = []
        safe_module_short = _safe_tool_name(module_short) or "custom"
        used_keys = {_safe_tool_name(name).lower() for name in used_names if str(name).strip()}
        builtin_keys = {_safe_tool_name(name).lower() for name in builtin_names if str(name).strip()}
        for tool_obj in raw_tools:
            original_name = str(getattr(tool_obj, "name", "")).strip()
            if not original_name:
                continue
            target_name = _safe_tool_name(original_name) or "tool"
            if target_name.lower() in builtin_keys:
                raise ValueError(f"custom tool name conflicts with builtin tool: {original_name}")
            if target_name.lower() in used_keys:
                base = f"custom_{safe_module_short}_{_safe_tool_name(original_name) or 'tool'}"
                target_name = base
                suffix = 2
                while target_name.lower() in used_keys:
                    target_name = f"{base}_{suffix}"
                    suffix += 1
                tool_obj = _tool_clone_with_name(tool_obj, target_name)
            elif original_name != target_name:
                tool_obj = _tool_clone_with_name(tool_obj, target_name)
            used_names.add(target_name)
            used_keys.add(target_name.lower())
            normalized.append(tool_obj)
        return normalized

    def _load_custom_tools(self, builtin_names: set[str]) -> list[Any]:
        custom_refs = [x for x in (self.config.custom_tool_modules or []) if str(x).strip()]
        custom_refs.extend(self._workspace_tool_module_refs())
        custom_refs = list(dict.fromkeys(str(item) for item in custom_refs))
        if not custom_refs:
            return []
        used_names = set(builtin_names)
        loaded_tools: list[Any] = []
        for module_ref in custom_refs:
            try:
                module = self._import_custom_module(str(module_ref))
                raw_tools = self._extract_custom_tools_from_module(module)
                module_name = _safe_tool_name(getattr(module, "__name__", "custom")) or "custom"
                module_short = module_name.split(".")[-1]
                loaded_tools.extend(
                    self._normalize_custom_tool_names(
                        raw_tools=raw_tools,
                        module_short=module_short,
                        builtin_names=builtin_names,
                        used_names=used_names,
                    )
                )
                self._queue_audit(
                    "custom_tool_load_ok",
                    {
                        "module": str(module_ref),
                        "tool_count": len(raw_tools),
                        "tools": [str(getattr(t, "name", "")) for t in raw_tools],
                    },
                )
            except Exception as e:
                self._queue_audit(
                    "custom_tool_load_fail",
                    {"module": str(module_ref), "error": str(e)},
                )
                raise
        return loaded_tools

    def _resolve_mcp_config_path(self) -> Path:
        raw = str(self.config.mcp_config_path or "mcp_servers.json").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / candidate
        return candidate.resolve()

    def _load_mcp_config(self) -> list[dict[str, Any]]:
        if not self.config.mcp_enabled:
            return []
        config_path = self._resolve_mcp_config_path()
        if not config_path.exists():
            return []
        text = config_path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("mcp config must be a JSON object")
        servers = data.get("servers", [])
        if not isinstance(servers, list):
            raise ValueError("mcp config 'servers' must be a list")
        out: list[dict[str, Any]] = []
        names: set[str] = set()
        for row in servers:
            if not isinstance(row, dict):
                continue
            name = _safe_tool_name(row.get("name"))
            if not name:
                continue
            if name in names:
                raise ValueError(f"duplicate MCP server name: {name}")
            names.add(name)
            enabled = _to_bool(row.get("enabled", True), default=True)
            transport = str(row.get("transport", "stdio")).strip().lower()
            command = str(row.get("command", "")).strip()
            args = row.get("args", [])
            env = row.get("env", {})
            timeout_s = int(row.get("timeout_s", self.config.mcp_timeout_s))
            out.append(
                {
                    "name": name,
                    "enabled": enabled,
                    "transport": transport,
                    "command": command,
                    "args": args if isinstance(args, list) else [],
                    "env": env if isinstance(env, dict) else {},
                    "timeout_s": max(1, timeout_s),
                }
            )
        return out

    def _make_mcp_langchain_tool(
        self,
        server_name: str,
        remote_tool_name: str,
        remote_description: str,
        input_schema: Any,
    ):
        mcp_tool_name = f"mcp_{server_name}_{_safe_tool_name(remote_tool_name) or 'tool'}"
        args_schema = _build_args_schema_from_json_schema(
            input_schema,
            f"McpTool_{_safe_tool_name(server_name)}_{_safe_tool_name(remote_tool_name)}",
        )

        def _invoke_mcp_tool(**kwargs: Any) -> str:
            client = self._mcp_clients.get(server_name)
            if client is None:
                return f"Error: MCP server '{server_name}' is unavailable."
            self.audit_logger.log(
                str(self._active_session_id or "main"),
                "mcp_tool_call",
                {"server": server_name, "tool": remote_tool_name, "args_keys": sorted(kwargs.keys())},
            )
            result = client.call_tool(remote_tool_name, kwargs or {})
            return _render_mcp_result(result)

        description = (remote_description or "").strip() or f"MCP tool {remote_tool_name} from server {server_name}."
        if args_schema is not None:
            return StructuredTool.from_function(
                func=_invoke_mcp_tool,
                name=mcp_tool_name,
                description=description,
                args_schema=args_schema,
            )

        @tool(mcp_tool_name)
        def _noarg_mcp_tool() -> str:
            """Call an MCP tool that does not require arguments."""
            return _invoke_mcp_tool()

        _noarg_mcp_tool.description = description
        return _noarg_mcp_tool

    def _load_mcp_tools(self) -> list[Any]:
        servers = self._load_mcp_config()
        if not servers:
            return []
        loaded: list[Any] = []
        for server in servers:
            if not server.get("enabled", True):
                continue
            name = str(server.get("name", "")).strip()
            transport = str(server.get("transport", "stdio")).strip().lower()
            if transport != "stdio":
                self._queue_audit(
                    "mcp_server_connect_fail",
                    {"server": name, "error": f"unsupported transport: {transport}"},
                )
                continue
            command = str(server.get("command", "")).strip()
            if not command:
                self._queue_audit("mcp_server_connect_fail", {"server": name, "error": "missing command"})
                continue
            try:
                client = McpStdioClient(
                    name=name,
                    command=command,
                    args=[str(x) for x in (server.get("args", []) or [])],
                    env={str(k): str(v) for k, v in (server.get("env", {}) or {}).items()},
                    timeout_s=int(server.get("timeout_s", self.config.mcp_timeout_s)),
                )
                client.start()
                self._mcp_clients[name] = client
                remote_tools = client.tools()
                for row in remote_tools:
                    remote_name = str(row.get("name", "")).strip()
                    if not remote_name:
                        continue
                    loaded.append(
                        self._make_mcp_langchain_tool(
                            server_name=name,
                            remote_tool_name=remote_name,
                            remote_description=str(row.get("description", "")).strip(),
                            input_schema=row.get("inputSchema", {}),
                        )
                    )
                self._queue_audit(
                    "mcp_server_connect_ok",
                    {"server": name, "tool_count": len(remote_tools)},
                )
            except Exception as e:
                try:
                    client.close()
                except Exception:
                    pass
                self._queue_audit("mcp_server_connect_fail", {"server": name, "error": str(e)})
                if self.config.mcp_fail_fast:
                    raise
        return loaded

    def close(self) -> None:
        for _name, client in list(self._mcp_clients.items()):
            try:
                client.close()
            except Exception:
                pass
        self._mcp_clients = {}

    def _resolve_skills_root(self) -> Path:
        raw = str(self.config.skills_dir or "skills").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace_dir)
        except ValueError as exc:
            raise ValueError(f"skills_dir escapes workspace: {raw}") from exc
        return resolved

    def _load_skill_from_path(self, path: Path) -> SkillSpec:
        content = path.read_text(encoding="utf-8")
        frontmatter_raw, body = _split_frontmatter(content)
        if frontmatter_raw is None:
            raise ValueError("SKILL.md frontmatter is required")
        meta = _parse_simple_yaml_frontmatter(frontmatter_raw)
        skill_id = _safe_tool_name(meta.get("id") or path.parent.name)
        if not skill_id:
            raise ValueError("skill id is empty")
        name = str(meta.get("name") or skill_id).strip()
        description = str(meta.get("description") or "").strip()
        if not description:
            description = body.strip().splitlines()[0].strip() if body.strip() else f"Skill {skill_id}"
        triggers = _to_str_list(meta.get("triggers"))
        required_tools = _to_str_list(meta.get("required_tools"))
        required_env = _to_str_list(meta.get("required_env"))
        tool_allow = _to_str_list(meta.get("tool_allow"))
        tool_deny = _to_str_list(meta.get("tool_deny"))
        selection_hints = _to_str_list(meta.get("selection_hints"))
        required_paths = _to_str_list(meta.get("required_paths"))
        execution_steps = _to_str_list(meta.get("execution_steps"))
        tool_priority = _to_str_list(meta.get("tool_priority"))
        api_policy = str(meta.get("api_policy") or "tool_first").strip().lower() or "tool_first"
        workflow = str(meta.get("workflow") or "").strip()
        output_format = str(meta.get("output_format") or "").strip()
        body_text = body.strip()
        if body_text:
            if not workflow:
                workflow = body_text
            elif not output_format:
                output_format = body_text
        return SkillSpec(
            id=skill_id,
            name=name,
            description=description,
            triggers=triggers,
            required_tools=required_tools,
            required_env=required_env,
            tool_allow=tool_allow,
            tool_deny=tool_deny,
            api_policy=api_policy,
            workflow=workflow,
            output_format=output_format,
            selection_hints=selection_hints,
            required_paths=required_paths,
            execution_steps=execution_steps,
            tool_priority=tool_priority,
            source_path=str(path),
        )

    def _discover_skills(self) -> dict[str, SkillSpec]:
        if not self.config.skills_enabled:
            return {}
        skill_roots = [self._resolve_skills_root()]
        if self.config.workspace_extensions_enabled:
            skill_roots.append(self._resolve_workspace_extension_root() / "skills")
        skill_roots = list(dict.fromkeys(path.resolve() for path in skill_roots))
        discovered: dict[str, SkillSpec] = {}
        for skills_root in skill_roots:
            if not skills_root.is_dir():
                continue
            for skill_file in sorted(skills_root.glob("*/SKILL.md")):
                try:
                    skill = self._load_skill_from_path(skill_file)
                    if skill.id in discovered:
                        raise ValueError(f"duplicate skill id: {skill.id}")
                    discovered[skill.id] = skill
                    self._queue_audit(
                        "skill_discovered",
                        {"skill_id": skill.id, "name": skill.name, "path": skill.source_path},
                    )
                except Exception as e:
                    self._queue_audit(
                        "skill_invalid",
                        {"path": str(skill_file), "error": str(e)},
                    )
        return discovered

    def list_skills(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for skill_id in sorted(self.skills_by_id.keys()):
            skill = self.skills_by_id[skill_id]
            rows.append(
                {
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "triggers": list(skill.triggers),
                    "required_tools": list(skill.required_tools),
                    "required_env": list(skill.required_env),
                    "api_policy": skill.api_policy,
                    "required_paths": list(skill.required_paths),
                    "selection_hints": list(skill.selection_hints),
                }
            )
        return rows

    def _find_skill_by_name(self, name: str) -> Optional[SkillSpec]:
        target = _safe_tool_name(name).lower()
        if not target:
            return None
        if target in self.skills_by_id:
            return self.skills_by_id[target]
        for skill in self.skills_by_id.values():
            if _safe_tool_name(skill.name).lower() == target:
                return skill
        return None

    def _score_skill(self, skill: SkillSpec, prompt: str) -> int:
        text = str(prompt or "").lower()
        if not text:
            return 0
        score = 0
        name_tokens = _skill_match_tokens(skill.name, ascii_min_length=3)
        for token in name_tokens:
            if token in text:
                score += 2
        desc_tokens = _skill_match_tokens(skill.description, ascii_min_length=4)
        for token in desc_tokens[:8]:
            if token in text:
                score += 1
        for trigger in skill.triggers:
            trig = str(trigger).strip().lower()
            if trig and trig in text:
                score += 5
        for hint in skill.selection_hints:
            token = str(hint).strip().lower()
            if token and token in text:
                score += 3
        return score

    def _select_skill(
        self,
        prompt: str,
        skill_name: Optional[str],
        skill_mode: Optional[str],
        session_id: str,
    ) -> Optional[SkillSpec]:
        if not self.config.skills_enabled:
            self.audit_logger.log(session_id, "skill_not_selected", {"reason": "skills_disabled"})
            return None
        mode = _normalize_skill_mode(skill_mode or self.config.skill_mode)
        explicit = str(skill_name or self.config.skill_name or "").strip()
        if mode == "off":
            self.audit_logger.log(session_id, "skill_not_selected", {"reason": "skill_mode_off"})
            return None
        if explicit:
            selected = self._find_skill_by_name(explicit)
            if not selected:
                self.audit_logger.log(
                    session_id,
                    "skill_not_selected",
                    {"reason": "skill_name_not_found", "skill_name": explicit, "mode": mode},
                )
                return None
            self.audit_logger.log(
                session_id,
                "skill_selected",
                {"skill_id": selected.id, "skill_name": selected.name, "mode": "manual"},
            )
            return selected
        if mode == "manual":
            self.audit_logger.log(session_id, "skill_not_selected", {"reason": "manual_without_skill_name"})
            return None
        best: Optional[SkillSpec] = None
        best_score = 0
        for skill in self.skills_by_id.values():
            score = self._score_skill(skill, prompt)
            if score > best_score:
                best = skill
                best_score = score
        if not best or best_score <= 0:
            self.audit_logger.log(session_id, "skill_not_selected", {"reason": "auto_no_match"})
            return None
        self.audit_logger.log(
            session_id,
            "skill_selected",
            {"skill_id": best.id, "skill_name": best.name, "mode": "auto", "score": best_score},
        )
        return best

    def _apply_skill_tool_policy(self, tools: Sequence[Any], skill: SkillSpec) -> list[Any]:
        out = list(tools)
        if skill.tool_allow:
            allow_keys: set[str] = set()
            for name in skill.tool_allow:
                allow_keys.update(_tool_name_keys(str(name)))
            out = [
                tool_obj
                for tool_obj in out
                if _tool_name_keys(str(getattr(tool_obj, "name", ""))).intersection(allow_keys)
            ]
        if skill.tool_deny:
            deny_keys: set[str] = set()
            for name in skill.tool_deny:
                deny_keys.update(_tool_name_keys(str(name)))
            out = [
                tool_obj
                for tool_obj in out
                if not _tool_name_keys(str(getattr(tool_obj, "name", ""))).intersection(deny_keys)
            ]
        return out

    def _auto_skill_tool_conflicts(
        self,
        prompt: str,
        base_tools: Sequence[Any],
        skill_tools: Sequence[Any],
    ) -> list[str]:
        """Return explicitly requested tools hidden only by an auto-selected skill.

        The caller supplies the already-filtered base tool set, so this never
        restores a tool removed by user allow/deny filters. Plan policy is
        applied afterwards and remains authoritative for mutating tools.
        """
        text = str(prompt or "")
        active_names = {
            str(getattr(tool_obj, "name", "")).strip().lower()
            for tool_obj in skill_tools
            if str(getattr(tool_obj, "name", "")).strip()
        }
        conflicts: list[str] = []
        for tool_obj in base_tools:
            name = str(getattr(tool_obj, "name", "")).strip()
            if not name or name.lower() in active_names:
                continue
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
            if re.search(pattern, text, flags=re.IGNORECASE):
                conflicts.append(name)
        return conflicts

    def _skill_precheck(self, tools: Sequence[Any], skill: SkillSpec) -> tuple[bool, str]:
        tool_map: dict[str, Any] = {}
        for tool_obj in tools:
            for key in _tool_name_keys(str(getattr(tool_obj, "name", ""))):
                tool_map[key] = tool_obj
        missing_tools: list[str] = []
        for required in skill.required_tools:
            req_keys = _tool_name_keys(str(required))
            if req_keys and not any(key in tool_map for key in req_keys):
                missing_tools.append(required)
        missing_env: list[str] = []
        for env_name in skill.required_env:
            key = str(env_name).strip()
            if key and not os.getenv(key):
                missing_env.append(key)
        missing_paths: list[str] = []
        skill_dir = Path(skill.source_path).resolve().parent
        for rel_path in skill.required_paths:
            target = (skill_dir / rel_path).resolve() if not Path(rel_path).is_absolute() else Path(rel_path).resolve()
            try:
                target.relative_to(self.workspace_dir)
            except ValueError:
                missing_paths.append(rel_path + " (escapes workspace)")
                continue
            if not target.exists():
                missing_paths.append(rel_path)
        for key_dir in ("scripts", "references", "assets"):
            expected = skill_dir / key_dir
            if expected.exists() and not expected.is_dir():
                missing_paths.append(f"{key_dir} (not a directory)")
        if not missing_tools and not missing_env and not missing_paths:
            return True, ""
        rows = [f"Skill precheck failed: {skill.id}"]
        if missing_tools:
            rows.append("Missing required tools: " + ", ".join(missing_tools))
        if missing_env:
            rows.append("Missing required env vars: " + ", ".join(missing_env))
        if missing_paths:
            rows.append("Missing required paths/resources: " + ", ".join(missing_paths))
        rows.append("Tip: enable matching MCP/custom tools or adjust allow/deny settings.")
        return False, "\n".join(rows)

__all__ = [name for name in globals() if not name.startswith("__")]
