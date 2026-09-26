"""Prompt 模板渲染回归测试

背景：`EXPERIENCE_GENERATION_PROMPT` 与 `SENTIMENT_ANALYSIS_PROMPT` 都曾因 JSON 字面量
花括号未转义（`{` 应为 `{{`），导致 `.format()` 抛 KeyError、功能静默降级（经验从未生成、
舆情 LLM 分析全部回退关键词）。本测试自动发现 app/ 下所有 PROMPT/TEMPLATE/INSTRUCTION
常量并逐一渲染，防止同类问题再次发生。
"""
import ast
import importlib
import pathlib
import re
import unittest

APP_DIR = pathlib.Path("app")


def _is_template_name(name: str) -> bool:
    return name.endswith(("PROMPT", "TEMPLATE", "INSTRUCTION"))


def discover_templates():
    """AST 扫描：返回 [(模块路径, 类名或None, 常量名)]

    仅收录**实际使用 .format() 渲染**的模板：字面量 JSON 花括号在纯文本 prompt 中合法，
    只有走 .format() 的模板才要求转义。
    """
    found = []
    for path in sorted(APP_DIR.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except SyntaxError:
            continue
        module = ".".join(path.with_suffix("").parts)

        def used_with_format(name: str) -> bool:
            return f"{name}.format(" in source

        for node in tree.body:                      # 模块级常量
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if getattr(t, "id", "") and _is_template_name(t.id) and used_with_format(t.id):
                        found.append((module, None, t.id))
            if isinstance(node, ast.ClassDef):      # 类属性
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for t in item.targets:
                            name = getattr(t, "id", "")
                            if name and _is_template_name(name) and used_with_format(name):
                                found.append((module, node.name, name))
    return sorted(set(found), key=lambda x: (x[0], x[1] or "", x[2]))


def load_template(module: str, cls_name, attr: str) -> str:
    mod = importlib.import_module(module)
    target = getattr(mod, cls_name) if cls_name else mod
    return getattr(target, attr)


class TestPromptTemplatesRender(unittest.TestCase):
    """所有模板必须能被 .format() 渲染（未转义的花括号会直接报错）"""

    def test_render_all_templates(self):
        failures = []
        checked = 0
        for module, cls_name, attr in discover_templates():
            try:
                tpl = load_template(module, cls_name, attr)
            except Exception as e:      # 依赖缺失等，跳过但不掩盖
                failures.append(f"{module}.{cls_name or ''}.{attr}: 加载失败 {e}")
                continue
            if not isinstance(tpl, str):
                continue

            named = set(re.findall(r"\{([a-zA-Z_]\w*)\}", tpl))
            positional = len(re.findall(r"\{(?::[^{}]*)?\}", tpl))
            kwargs = {n: f"<{n}>" for n in named}
            args = ["<arg>"] * max(positional, 0)
            checked += 1
            try:
                tpl.format(*args, **kwargs)
            except Exception as e:
                failures.append(
                    f"{module}.{cls_name or ''}.{attr}: {type(e).__name__}: {str(e)[:80]}"
                )

        self.assertGreater(checked, 20, "模板发现数量异常，检查扫描逻辑")
        self.assertEqual(failures, [], "存在无法渲染的 prompt 模板（花括号未转义）:\n" + "\n".join(failures))


class TestKnownTemplateContent(unittest.TestCase):
    """关键模板渲染后应保留 JSON 输出结构（防止"修好了但内容丢了"）"""

    def test_sentiment_prompt_keeps_json(self):
        from app.services.sentiment_service import SentimentService
        rendered = SentimentService.SENTIMENT_ANALYSIS_PROMPT.format(
            title="标题", content="内容", available_etfs="510300"
        )
        self.assertIn('"sentiment_score"', rendered)
        self.assertIn('"related_etfs"', rendered)
        self.assertIn("510300", rendered)

    def test_experience_prompt_keeps_json_and_data(self):
        from app.services.review_service import ReviewService
        rendered = ReviewService.EXPERIENCE_GENERATION_PROMPT.format(
            period_days=7, total_count=3, success_count=1, failure_count=2,
            avg_return=-0.5, max_loss=-2.0, failure_cases="[]", success_cases="[]",
            sentiment_patterns="{}",
        )
        self.assertIn('"experience_type"', rendered)
        self.assertIn("分析周期: 7天", rendered)


if __name__ == "__main__":
    unittest.main()