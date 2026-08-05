"""Skill 文档管理 - 类 Claude 的 Markdown 技能文件，按需注入系统提示

skill 文件约定：skills/{name}.md
```
---
name: web_search
description: 网络搜索外部信息（新闻、政策、数据），用于市场环境补充分析
---
<skill 正文：使用说明、调用建议、注意事项>
```

设计：
- 系统提示只注入 skill 的 name+description 摘要列表，避免 prompt 膨胀
- LLM 需要时调用 load_skill(name) 工具加载全文，作为 tool result 回填
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_META_KEY_RE = re.compile(r"^(\w+):\s*(.*)$")


class SkillManager:
    """扫描 skills/ 目录，构建 skill 索引，支持按名加载全文"""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = Path(skills_dir) if skills_dir else SKILLS_DIR
        self._skills: Dict[str, dict] = {}
        self._scan()

    def _scan(self):
        """扫描目录下所有 .md skill 文件，解析 frontmatter"""
        if not self.skills_dir.is_dir():
            logger.info(f"skills 目录不存在，跳过扫描: {self.skills_dir}")
            return
        for path in sorted(self.skills_dir.glob("*.md")):
            meta, _ = self._parse_file(path)
            name = meta.get("name") or path.stem
            self._skills[name] = {
                "name": name,
                "description": meta.get("description", ""),
                "path": str(path),
            }
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

    def list_skills(self) -> List[dict]:
        """返回所有 skill 摘要（name + description），供系统提示注入"""
        return [{"name": s["name"], "description": s["description"]} for s in self._skills.values()]

    def get_skill_names(self) -> List[str]:
        """返回所有 skill 名称"""
        return list(self._skills.keys())

    def load_skill(self, name: str) -> Optional[str]:
        """加载指定 skill 全文（正文部分），不存在返回 None"""
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
        return self._skills.get(name, {}).get("description", "")


# 模块级单例
_skill_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
    return _skill_manager
