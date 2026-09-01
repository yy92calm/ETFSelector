"""Skill 文档管理 - 类 Claude 的 Markdown 技能文件，按需注入系统提示

skill 文件约定：skills/{name}.md
```
---
name: web_search
description: 网络搜索外部信息（新闻、政策、数据），用于市场环境补充分析
model_invocable: true    # 可选，是否出现在模型侧技能目录（默认 true）
user_invocable: true     # 可选，是否出现在用户侧命令目录（默认 true）
---
<skill 正文：使用说明、调用建议、注意事项>
```

设计（对齐 deepseek-harness skill 能力族模式）：
- 多根目录 + 优先级：项目 skills/（rank 100）> 配置追加目录（rank 300）
  > 用户 ~/.etfselector/skills/（rank 400），同名低 rank 胜出
- 热更新：list_skills() 时按目录 mtime 检测变化，变化才重扫（无需重启进程）
- 系统提示只注入 skill 的 name+description 摘要列表，避免 prompt 膨胀
- LLM 需要时调用 load_skill(name) 工具加载全文，作为 tool result 回填
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
USER_SKILLS_DIR = Path.home() / ".etfselector" / "skills"

# 目录优先级 rank（数值越小越优先，同名低 rank 胜出）
_PROJECT_RANK = 100
_EXTRA_RANK = 300
_USER_RANK = 400

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_META_KEY_RE = re.compile(r"^(\w+):\s*(.*)$")


def _parse_bool(value: str, default: bool = True) -> bool:
    """frontmatter 布尔字段解析（true/false/1/0，其余回退默认值）"""
    if value is None or value == "":
        return default
    return value.strip().lower() in ("true", "1", "yes")


class SkillManager:
    """扫描多级技能目录，构建 skill 索引，支持按名加载全文与热更新"""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = Path(skills_dir) if skills_dir else SKILLS_DIR
        self._skills: Dict[str, dict] = {}
        self._sig: Optional[tuple] = None
        self._scan()

    # ---- 根目录与热更新 ----

    def _roots(self) -> List[tuple]:
        """技能根目录列表：(路径, rank)，按 rank 升序（低 rank 先扫描即优先）"""
        roots = [(self.skills_dir, _PROJECT_RANK)]
        for extra in self._extra_dirs():
            roots.append((Path(extra), _EXTRA_RANK))
        roots.append((USER_SKILLS_DIR, _USER_RANK))
        return roots

    @staticmethod
    def _extra_dirs() -> List[str]:
        """解析 SKILL_EXTRA_DIRS 配置（JSON 数组或 os.pathsep 分隔）"""
        raw = getattr(get_settings(), "skill_extra_dirs", "") or ""
        if not raw.strip():
            return []
        raw = raw.strip()
        if raw.startswith("["):
            try:
                data = json.loads(raw)
                return [str(p) for p in data if str(p).strip()] if isinstance(data, list) else []
            except json.JSONDecodeError:
                logger.warning("SKILL_EXTRA_DIRS JSON 解析失败，忽略")
                return []
        return [p for p in raw.split(os.pathsep) if p.strip()]

    def _dir_signature(self) -> tuple:
        """所有根目录的 (路径, mtime_ns) 签名，用于热更新检测"""
        sig = []
        for path, _rank in self._roots():
            try:
                sig.append((str(path), path.stat().st_mtime_ns))
            except OSError:
                sig.append((str(path), None))
        return tuple(sig)

    def _ensure_fresh(self):
        """目录 mtime 变化时重扫（轻量热更新，无 watchdog 依赖）"""
        try:
            current = self._dir_signature()
        except Exception as e:
            logger.warning(f"技能目录状态检测失败，跳过热更新: {e}")
            return
        if current != self._sig:
            logger.info("检测到技能目录变化，重新扫描")
            self._scan()

    # ---- 扫描与解析 ----

    def _scan(self):
        """扫描所有根目录下 .md skill 文件，解析 frontmatter（同名低 rank 胜出）"""
        self._sig = self._dir_signature()
        skills: Dict[str, dict] = {}
        for path, rank in self._roots():
            if not path.is_dir():
                continue
            for file_path in sorted(path.glob("*.md")):
                try:
                    meta, _ = self._parse_file(file_path)
                except OSError as e:
                    logger.warning(f"读取 skill 文件失败，跳过: {file_path} ({e})")
                    continue
                name = meta.get("name") or file_path.stem
                if name in skills:
                    # 低 rank 先扫到，保留先者
                    continue
                skills[name] = {
                    "name": name,
                    "description": meta.get("description", ""),
                    "model_invocable": _parse_bool(meta.get("model_invocable"), True),
                    "user_invocable": _parse_bool(meta.get("user_invocable"), True),
                    "rank": rank,
                    "path": str(file_path),
                }
        self._skills = skills
        if self._skills:
            logger.info(f"Skill 扫描完成，共 {len(self._skills)} 个: {list(self._skills.keys())}")

    def _parse_file(self, path: Path) -> tuple:
        """解析 skill 文件，返回 (frontmatter dict, 正文内容)"""
        content = path.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(content)
        if not match:
            return {}, content
        meta = {}
        for line in match.group(1).strip().splitlines():
            km = _META_KEY_RE.match(line.strip())
            if km:
                meta[km.group(1)] = km.group(2).strip().strip('"')
        return meta, content[match.end():].strip()

    # ---- 对外接口 ----

    def list_skills(self, audience: Optional[str] = None) -> List[dict]:
        """返回 skill 摘要列表，供系统提示注入

        Args:
            audience: 过滤调用方 —— "model" 只返回 model_invocable，
                "user" 只返回 user_invocable，None 返回全部
        """
        self._ensure_fresh()
        result = []
        for s in self._skills.values():
            if audience == "model" and not s["model_invocable"]:
                continue
            if audience == "user" and not s["user_invocable"]:
                continue
            result.append({"name": s["name"], "description": s["description"]})
        return result

    def get_skill_names(self) -> List[str]:
        """返回所有 skill 名称"""
        self._ensure_fresh()
        return list(self._skills.keys())

    def load_skill(self, name: str) -> Optional[str]:
        """加载指定 skill 全文（正文部分），不存在返回 None"""
        self._ensure_fresh()
        skill = self._skills.get(name)
        if not skill:
            return None
        try:
            _, body = self._parse_file(Path(skill["path"]))
            return body
        except OSError as e:
            logger.error(f"读取 skill {name} 失败: {e}")
            return None

    def get_description(self, name: str) -> str:
        """获取 skill 描述（供错误提示用）"""
        self._ensure_fresh()
        return self._skills.get(name, {}).get("description", "")


# 模块级单例
_skill_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
    return _skill_manager
