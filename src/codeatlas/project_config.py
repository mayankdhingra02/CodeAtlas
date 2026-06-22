from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DEFAULT_IGNORE_DIRS, SUPPORTED_EXTENSIONS, resolve_repo_root


CONFIG_NAMES = (".codeatlas.yml", ".codeatlas.yaml", ".codeatlas.json")
LIST_KEYS = {
    "paths",
    "dirs",
    "suppressions",
    "owned_prefixes",
    "team_prefixes",
    "company_prefixes",
    "third_party_packages",
    "hide_packages",
    "show_packages",
}


@dataclass(frozen=True)
class RulesConfig:
    enabled: bool = True
    tests_lower_severity: bool = True
    suppressions: tuple[dict[str, str], ...] = ()
    severity_overrides: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UiConfig:
    default_lens: str = "overview"
    node_budget: int = 180
    min_edge_weight: int = 1
    connected_only: bool = True
    edge_contrast: int = 64


@dataclass(frozen=True)
class ClassificationConfig:
    owned_prefixes: tuple[str, ...] = ()
    team_prefixes: tuple[str, ...] = ()
    company_prefixes: tuple[str, ...] = ()
    third_party_packages: tuple[str, ...] = ()
    hide_packages: tuple[str, ...] = ()
    show_packages: tuple[str, ...] = ()


@dataclass(frozen=True)
class CacheConfig:
    enabled: bool = True
    ttl_seconds: int = 300


@dataclass(frozen=True)
class CodeAtlasProjectConfig:
    path: Path | None
    raw: dict[str, Any]
    languages: dict[str, bool]
    ignore_dirs: frozenset[str]
    ignore_paths: tuple[str, ...]
    rules: RulesConfig
    ui: UiConfig
    classification: ClassificationConfig
    cache: CacheConfig
    fingerprint: str

    def public_payload(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else "",
            "fingerprint": self.fingerprint,
            "languages": self.languages,
            "ignore": {
                "dirs": sorted(self.ignore_dirs),
                "paths": list(self.ignore_paths),
            },
            "rules": {
                "enabled": self.rules.enabled,
                "tests_lower_severity": self.rules.tests_lower_severity,
                "suppressions": list(self.rules.suppressions),
                "severity_overrides": self.rules.severity_overrides,
            },
            "ui": {
                "default_lens": self.ui.default_lens,
                "node_budget": self.ui.node_budget,
                "min_edge_weight": self.ui.min_edge_weight,
                "connected_only": self.ui.connected_only,
                "edge_contrast": self.ui.edge_contrast,
            },
            "classification": {
                "owned_prefixes": list(self.classification.owned_prefixes),
                "team_prefixes": list(self.classification.team_prefixes),
                "company_prefixes": list(self.classification.company_prefixes),
                "third_party_packages": list(self.classification.third_party_packages),
                "hide_packages": list(self.classification.hide_packages),
                "show_packages": list(self.classification.show_packages),
            },
            "cache": {
                "enabled": self.cache.enabled,
                "ttl_seconds": self.cache.ttl_seconds,
            },
        }


def load_project_config(repo_path: str | Path) -> CodeAtlasProjectConfig:
    repo_root = resolve_repo_root(repo_path)
    config_path = next((repo_root / name for name in CONFIG_NAMES if (repo_root / name).exists()), None)
    raw = parse_config_file(config_path) if config_path else {}
    fingerprint = config_fingerprint(raw, config_path)
    languages_raw = raw.get("languages") if isinstance(raw.get("languages"), dict) else {}
    languages = {
        "python": bool_value(languages_raw.get("python"), True),
        "javascript": bool_value(languages_raw.get("javascript"), True),
    }
    ignore_raw = raw.get("ignore") if isinstance(raw.get("ignore"), dict) else {}
    ignore_dirs = frozenset(
        str(item)
        for item in list(DEFAULT_IGNORE_DIRS) + list_value(ignore_raw.get("dirs"))
        if str(item).strip()
    )
    ignore_paths = tuple(str(item) for item in list_value(ignore_raw.get("paths")) if str(item).strip())
    rules_raw = raw.get("rules") if isinstance(raw.get("rules"), dict) else {}
    ui_raw = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
    classification_raw = raw.get("classification") if isinstance(raw.get("classification"), dict) else {}
    cache_raw = raw.get("cache") if isinstance(raw.get("cache"), dict) else {}
    return CodeAtlasProjectConfig(
        path=config_path,
        raw=raw,
        languages=languages,
        ignore_dirs=ignore_dirs,
        ignore_paths=ignore_paths,
        rules=RulesConfig(
            enabled=bool_value(rules_raw.get("enabled"), True),
            tests_lower_severity=bool_value(rules_raw.get("tests_lower_severity"), True),
            suppressions=tuple(
                normalize_suppression(item)
                for item in list_value(rules_raw.get("suppressions"))
                if isinstance(item, dict)
            ),
            severity_overrides={
                str(key): str(value)
                for key, value in (
                    rules_raw.get("severity_overrides")
                    if isinstance(rules_raw.get("severity_overrides"), dict)
                    else {}
                ).items()
            },
        ),
        ui=UiConfig(
            default_lens=str(ui_raw.get("default_lens") or "overview"),
            node_budget=max(0, int_value(ui_raw.get("node_budget"), 180)),
            min_edge_weight=max(1, int_value(ui_raw.get("min_edge_weight"), 1)),
            connected_only=bool_value(ui_raw.get("connected_only"), True),
            edge_contrast=max(25, min(100, int_value(ui_raw.get("edge_contrast"), 64))),
        ),
        classification=ClassificationConfig(
            owned_prefixes=string_tuple(classification_raw.get("owned_prefixes")),
            team_prefixes=string_tuple(classification_raw.get("team_prefixes")),
            company_prefixes=string_tuple(classification_raw.get("company_prefixes")),
            third_party_packages=string_tuple(classification_raw.get("third_party_packages")),
            hide_packages=string_tuple(classification_raw.get("hide_packages")),
            show_packages=string_tuple(classification_raw.get("show_packages")),
        ),
        cache=CacheConfig(
            enabled=bool_value(cache_raw.get("enabled"), True),
            ttl_seconds=max(0, int_value(cache_raw.get("ttl_seconds"), 300)),
        ),
        fingerprint=fingerprint,
    )


def enabled_extensions(config: CodeAtlasProjectConfig) -> dict[str, str]:
    enabled: dict[str, str] = {}
    for extension, language in SUPPORTED_EXTENSIONS.items():
        if config.languages.get(language, True):
            enabled[extension] = language
    return enabled


def path_is_ignored(relative_path: str, config: CodeAtlasProjectConfig) -> bool:
    parts = Path(relative_path).parts
    if any(part in config.ignore_dirs for part in parts):
        return True
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in config.ignore_paths)


def update_classification_config(repo_path: str | Path, package_name: str, category: str) -> CodeAtlasProjectConfig:
    repo_root = resolve_repo_root(repo_path)
    package = package_name.strip()
    if not package:
        raise ValueError("Package name is required.")
    target_category = category.strip().lower().replace("-", "_")
    target_keys = {
        "owned": "owned_prefixes",
        "team": "show_packages",
        "third_party": "third_party_packages",
        "hide": "hide_packages",
        "docs_config": "hide_packages",
    }
    if target_category not in target_keys:
        raise ValueError(f"Unsupported classification category: {category}")

    config_path = next((repo_root / name for name in CONFIG_NAMES if (repo_root / name).exists()), repo_root / ".codeatlas.yml")
    raw = parse_config_file(config_path) if config_path.exists() else {}
    if not isinstance(raw, dict):
        raw = {}
    classification = raw.get("classification") if isinstance(raw.get("classification"), dict) else {}
    raw["classification"] = classification
    list_keys = (
        "owned_prefixes",
        "team_prefixes",
        "company_prefixes",
        "third_party_packages",
        "hide_packages",
        "show_packages",
    )
    package_key = package.lower()
    for key in list_keys:
        items = unique_strings(list_value(classification.get(key)))
        classification[key] = [item for item in items if item.lower() != package_key]
    target_key = target_keys[target_category]
    classification[target_key] = unique_strings([*list_value(classification.get(target_key)), package])
    config_path.write_text(dump_simple_yaml(raw), encoding="utf-8")
    return load_project_config(repo_root)


def restore_classification_config(repo_path: str | Path, classification_payload: dict[str, Any]) -> CodeAtlasProjectConfig:
    repo_root = resolve_repo_root(repo_path)
    config_path = next((repo_root / name for name in CONFIG_NAMES if (repo_root / name).exists()), repo_root / ".codeatlas.yml")
    raw = parse_config_file(config_path) if config_path.exists() else {}
    if not isinstance(raw, dict):
        raw = {}
    raw["classification"] = {
        "owned_prefixes": unique_strings(list_value(classification_payload.get("owned_prefixes"))),
        "team_prefixes": unique_strings(list_value(classification_payload.get("team_prefixes"))),
        "company_prefixes": unique_strings(list_value(classification_payload.get("company_prefixes"))),
        "third_party_packages": unique_strings(list_value(classification_payload.get("third_party_packages"))),
        "hide_packages": unique_strings(list_value(classification_payload.get("hide_packages"))),
        "show_packages": unique_strings(list_value(classification_payload.get("show_packages"))),
    }
    config_path.write_text(dump_simple_yaml(raw), encoding="utf-8")
    return load_project_config(repo_root)


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def dump_simple_yaml(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in payload.items():
        append_yaml_value(lines, key, value, 0)
    return "\n".join(lines).rstrip() + "\n"


def append_yaml_value(lines: list[str], key: str, value: Any, indent: int) -> None:
    prefix = " " * indent
    if isinstance(value, dict):
        lines.append(f"{prefix}{key}:")
        for child_key, child_value in value.items():
            append_yaml_value(lines, str(child_key), child_value, indent + 2)
        return
    if isinstance(value, list):
        lines.append(f"{prefix}{key}:")
        if not value:
            return
        for item in value:
            if isinstance(item, dict):
                pairs = list(item.items())
                if not pairs:
                    lines.append(f"{prefix}  - {{}}")
                    continue
                first_key, first_value = pairs[0]
                if isinstance(first_value, (dict, list)):
                    lines.append(f"{prefix}  - {first_key}:")
                    append_nested_yaml_value(lines, first_value, indent + 4)
                else:
                    lines.append(f"{prefix}  - {first_key}: {yaml_scalar(first_value)}")
                for child_key, child_value in pairs[1:]:
                    append_yaml_value(lines, str(child_key), child_value, indent + 4)
            else:
                lines.append(f"{prefix}  - {yaml_scalar(item)}")
        return
    lines.append(f"{prefix}{key}: {yaml_scalar(value)}")


def append_nested_yaml_value(lines: list[str], value: Any, indent: int) -> None:
    prefix = " " * indent
    if isinstance(value, dict):
        for key, child in value.items():
            append_yaml_value(lines, str(key), child, indent)
        return
    if isinstance(value, list):
        for item in value:
            lines.append(f"{prefix}- {yaml_scalar(item)}")
        return
    lines.append(f"{prefix}{yaml_scalar(value)}")


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if not text or text.strip() != text or any(char in text for char in ":#[]{}&,*!|>'\"%@`"):
        return json.dumps(text)
    if text.lower() in {"true", "false", "yes", "no", "on", "off", "null", "none"}:
        return json.dumps(text)
    return text


def parse_config_file(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    content = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        payload = json.loads(content)
        return payload if isinstance(payload, dict) else {}
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(content) or {}
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return parse_simple_yaml(content)


def parse_simple_yaml(content: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    section_stack: list[tuple[int, Any]] = [(-1, result)]
    pending_list_item: dict[str, Any] | None = None
    pending_list_indent = -1
    for raw_line in content.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        while section_stack and indent <= section_stack[-1][0]:
            section_stack.pop()
        parent = section_stack[-1][1]
        if line.startswith("- "):
            value_text = line[2:].strip()
            if not isinstance(parent, list):
                continue
            if ":" in value_text:
                key, value = split_key_value(value_text)
                item = {key: parse_scalar(value)}
                parent.append(item)
                pending_list_item = item
                pending_list_indent = indent
            else:
                parent.append(parse_scalar(value_text))
                pending_list_item = None
            continue
        if pending_list_item is not None and indent > pending_list_indent and ":" in line:
            key, value = split_key_value(line)
            pending_list_item[key] = parse_scalar(value)
            continue
        if ":" not in line or not isinstance(parent, dict):
            continue
        key, value = split_key_value(line)
        if value == "":
            next_container: Any = [] if key in LIST_KEYS else {}
            parent[key] = next_container
            section_stack.append((indent, next_container))
            pending_list_item = None
        else:
            parent[key] = parse_scalar(value)
            pending_list_item = None
    return result


def split_key_value(line: str) -> tuple[str, str]:
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def parse_scalar(value: str) -> Any:
    clean = value.strip()
    if not clean:
        return ""
    if clean.lower() in {"true", "yes", "on"}:
        return True
    if clean.lower() in {"false", "no", "off"}:
        return False
    if clean.lower() in {"null", "none"}:
        return None
    if clean == "[]":
        return []
    if clean.startswith("[") and clean.endswith("]"):
        inner = clean[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part.strip()) for part in inner.split(",")]
    if (clean.startswith('"') and clean.endswith('"')) or (clean.startswith("'") and clean.endswith("'")):
        return clean[1:-1]
    try:
        return int(clean)
    except ValueError:
        return clean


def list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def string_tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in list_value(value) if str(item).strip())


def bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_suppression(item: dict[str, Any]) -> dict[str, str]:
    return {
        "rule": str(item.get("rule") or item.get("rule_id") or "*"),
        "path": str(item.get("path") or item.get("file_path") or "*"),
        "reason": str(item.get("reason") or ""),
    }


def config_fingerprint(raw: dict[str, Any], path: Path | None) -> str:
    payload = {
        "path": str(path) if path else "",
        "raw": raw,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
