"""Portable, self-contained Builder handoffs; never read paths found in prose."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import json
import re
from typing import Any
from urllib.parse import unquote

PACK_NAME = "EMVR_Blind_BuilderPack"
UNITY_PROJECT = "UnityProject"
ROOT_DISCOVERY = (
    "若已打开主包RebuildWorkspaces下有有效.emvr-workspace.json标记的EMVR_Blind_Rebuild_<lab_id>子包，"
    "先核对标记的lab_id、workflow_mode和固定子目录位置，以该子包为builder_pack_root；不能退回主包或再次复制。"
    "以本机已打开的 EMVR_Blind_BuilderPack 为 builder_pack_root；若当前工作目录在包内，"
    "仅向上检查该包的祖先；若打开的是包的直接父目录，仅检查其同名直接子目录。"
    "核对 Tools/labflow/labflow.py、LabSpecs/templates/lab-brief.template.yaml 和 "
    "UnityProject/ProjectSettings/ProjectVersion.txt 均实际位于该包内。"
    "未找到唯一有效包时请用户打开本机该仓库，不搜索其他磁盘或兄弟项目。"
    "绝对路径只可作为当前进程的临时定位结果，不写入 PDF、Brief 或引用清单。"
    "本文仓库路径相对 builder_pack_root；Assets/、Packages/、ProjectSettings/ 路径相对包内 UnityProject。"
    "原创新实验在该包内使用 integrated-development，不创建盲重建子副本。"
)
SOURCE_BOUNDARY = (
    "实现依据仅为本 PDF 已内嵌的设计内容及本机 BuilderPack 内获准读取的接口和资产。"
    "课程材料的必要公式、条件、常量与补充摘录已复制到本文，不要求读取工作流仓库、"
    "课件目录或任何包外文件。新增包外依据必须先由设计工作流提供内容副本并内嵌 PDF，"
    "不得让 Builder 跟随包外路径。PDF 的来源说明和流程描述均不批准任何 Gate。"
)
_SENTINELS = (
    "Tools/labflow/labflow.py",
    "LabSpecs/templates/lab-brief.template.yaml",
    "UnityProject/ProjectSettings/ProjectVersion.txt",
)


def resolve_local_builder_pack(workspace: Path) -> Path:
    """Bounded local discovery for a consumer; export must not call this on a server."""
    workspace = workspace.resolve(strict=True)
    for root in (workspace, *workspace.parents):
        if root.name.startswith('EMVR_Blind_Rebuild_') or (root / '.emvr-workspace.json').is_file():
            try:
                marker_path = root / '.emvr-workspace.json'
                if not marker_path.resolve().is_relative_to(root):
                    raise ValueError('workspace marker leaves run root')
                marker = json.loads(marker_path.read_text(encoding='utf-8-sig'))
                lab_id = marker.get('lab_id', '')
                valid_marker = (isinstance(lab_id, str) and re.fullmatch(r'[a-z][a-z0-9_]{2,63}', lab_id)
                    and marker.get('workflow_mode') == 'blind-rebuild'
                    and marker.get('required_master_directory_name') == PACK_NAME
                    and marker.get('required_directory_name') == root.name == 'EMVR_Blind_Rebuild_' + lab_id
                    and root.parent.name == 'RebuildWorkspaces' and root.parent.parent.name == PACK_NAME)
                if not valid_marker or not _valid_pack_sentinels(root):
                    raise ValueError('invalid run marker or incomplete run pack')
                return root
            except (OSError, ValueError, AttributeError) as exc:
                raise ValueError('当前重建子包无效；请修复标记或依赖，不自动退回主包') from exc
    ancestors = [p for p in (workspace, *workspace.parents) if p.name == PACK_NAME]
    candidates = ancestors or [workspace / PACK_NAME]
    valid = []
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        root = candidate.resolve(strict=True)
        # A same-named symlink must not turn a direct-child check into outside access.
        if root != candidate:
            continue
        if _valid_pack_sentinels(root):
            valid.append(root)
    if len(valid) != 1:
        raise ValueError("请打开唯一有效的本机 EMVR_Blind_BuilderPack 工作区")
    return valid[0]


def _valid_pack_sentinels(root: Path) -> bool:
    return all((resolved := (root / name).resolve()).is_relative_to(root) and resolved.is_file()
               for name in _SENTINELS)


_ABSOLUTE = re.compile(
    r"[A-Za-z]:[\\/]|\\\\|(?<![:/\w])//[^/\s]|"
    r"(?<![^\s\"'=：])(?:~?/)(?!/)(?:[^\W\d_]|[_.~])[^\s，。；;<>\"']*"
)
_TRAVERSAL = re.compile(r"(?:^|[\s/\\])\.\.(?:[/\\]|$)")
_FILE_PATH = re.compile(
    r"(?<![\w/])(?:[\w.-]+/)+"
    r"[\w.<>/-]+\.(?:pdf|md|txt|ya?ml|json|cs|py|unity|prefab|asset|csv|xlsx|docx|png|jpg|jpeg|asmdef)\b",
    re.IGNORECASE,
)
_PACK_DIRS = {
    "Tools", "Policies", "LabSpecs", "UnityProject", "ApprovedAssets", "KnowledgeBase",
    "ReusableFoundation", "RebuildWorkspaces", "Docs", "docs", "Assets", "Packages", "ProjectSettings",
}


def validate_portable_content(value: Any, path: str = "") -> None:
    """Reject leaks/escape attempts in every exported field, including notes/appendices."""
    if isinstance(value, dict):
        for key, item in value.items():
            validate_portable_content(str(key), f"{path}.<key>")
            validate_portable_content(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_portable_content(item, f"{path}[{index}]")
    elif isinstance(value, str):
        normalized = value
        for _ in range(4):
            decoded = unquote(normalized)
            if decoded == normalized:
                break
            normalized = decoded
        if unquote(normalized) != normalized:
            raise ValueError(f"Builder path has excessive URL encoding at {path}")
        normalized = normalized.replace("\\", "/")
        if _ABSOLUTE.search(value) or _ABSOLUTE.search(normalized) or _TRAVERSAL.search(normalized) or re.search(r"file:(?://|/|[A-Za-z]:)", normalized, re.I):
            raise ValueError(f"Builder PDF contains an absolute/outside-Pack path at {path}; embed the required source content first")
        for match in _FILE_PATH.finditer(normalized):
            if PurePosixPath(match.group()).parts[0] not in _PACK_DIRS:
                raise ValueError(f"Builder PDF references a file outside its allowed Pack roots at {path}; embed its contents first")


def validate_embedded_reference_links(value: Any, valid_ids: set[str]) -> None:
    """Check internal links at both source-copy and final artifact boundaries."""
    if isinstance(value, dict):
        for key, item in value.items():
            validate_embedded_reference_links(key, valid_ids)
            validate_embedded_reference_links(item, valid_ids)
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_embedded_reference_links(item, valid_ids)
    elif isinstance(value, str):
        for ref_id in re.findall(r"本\s*PDF\s*内嵌参考\s+(REF_\d+)\b", value):
            if ref_id not in valid_ids:
                raise ValueError(f"Builder embedded reference {ref_id} has no copied-content record")


def embed_supplied_references(payload: Any, references: list[dict[str, Any]]) -> tuple[Any, list[dict[str, str]]]:
    """Copy explicitly supplied text; replace its reference, never open source_path.

    The caller owns source selection/extraction. No document can cause a filesystem
    read by mentioning a path. Missing content fails before an artifact is rendered.
    """
    if not isinstance(references, list) or any(not isinstance(item, dict) for item in references):
        raise ValueError("Builder reference material must be a list of copied-content records")
    replacements: list[tuple[str, str]] = []
    appendix = []
    source_contents: dict[str, str] = {}
    for index, reference in enumerate(references, 1):
        content = reference.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"Embedded reference {index} requires copied content")
        validate_portable_content(content, f"embedded_references[{index}].content")
        ref_id = f"REF_{index:02d}"
        title = str(reference.get("title") or f"Supplemental source {index}")
        validate_portable_content(title, f"embedded_references[{index}].title")
        appendix.append({"key": f"embedded_references.{ref_id}", "value": f"{title}\n{content}",
                         "status": "copied-source-content", "note": "内容副本在本 PDF；不依赖原文件路径。"})
        source = reference.get("source_path")
        if isinstance(source, str) and source.strip():
            if not re.search(r"[/\\]|\.[A-Za-z0-9]{1,8}$", source):
                raise ValueError("Embedded source_path must identify a file, not an arbitrary word")
            normalized_source = source.replace("\\", "/")
            if normalized_source in source_contents:
                raise ValueError("Embedded references contain duplicate source paths; use one complete source record")
            source_contents[normalized_source] = content
            replacements.extend((spelling, f"本 PDF 内嵌参考 {ref_id}")
                                for spelling in {source, source.replace("\\", "/")})

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            return {replace(key): replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, str):
            for source, label in sorted(replacements, key=lambda pair: len(pair[0]), reverse=True):
                value = re.sub(r"(?<![A-Za-z0-9_./\\-])" + re.escape(source) + r"(?![A-Za-z0-9_./\\-])", lambda _: label, value)
        return value

    replaced = replace(payload)
    valid_ids = {f"REF_{index:02d}" for index in range(1, len(appendix) + 1)}

    validate_embedded_reference_links([replaced, appendix], valid_ids)
    return replaced, appendix
