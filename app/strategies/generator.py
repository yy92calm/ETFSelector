"""
AI策略生成器
通过LLM将自然语言描述转换为可执行的策略代码
"""

import logging
import re
from typing import Optional, List
from openai import OpenAI
from app.config import get_settings
from app.strategies.base import BaseStrategy, Signal, StrategyContext

logger = logging.getLogger(__name__)
settings = get_settings()

SYSTEM_PROMPT = """你是一个ETF量化策略生成器。用户会用自然语言描述一个交易策略，你需要将其转换为Python代码。

## 要求
1. 生成一个名为 `CustomStrategy` 的类，继承 `BaseStrategy`
2. 必须实现 `generate_signals(self, ctx: StrategyContext) -> List[Signal]` 方法
3. ctx.history 是一个 pandas DataFrame，包含列: date, open, close, high, low, volume, amount, change_pct
4. 返回 Signal 列表，Signal 包含: trade_date, etf_code, direction("buy"/"sell"), strength(0~1), reason
5. 只使用 pandas 和 numpy 库进行计算
6. 代码必须安全，不能有文件操作、网络请求等危险操作

## 可用的导入
```python
from typing import List
import pandas as pd
import numpy as np
from app.strategies.base import BaseStrategy, Signal, StrategyContext
```

## 输出格式
只输出Python代码，不要包含其他说明文字。代码用 ```python 和 ``` 包裹。
"""


def generate_strategy_code(description: str) -> Optional[str]:
    """
    调用LLM生成策略代码
    返回可执行的Python代码字符串
    """
    if not settings.llm_api_key or settings.llm_api_key == "your-api-key-here":
        logger.warning("未配置LLM API Key，使用默认策略模板")
        return _fallback_code(description)

    try:
        client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base_url,
        )

        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请根据以下描述生成交易策略:\n\n{description}"},
            ],
            temperature=0.3,
            max_tokens=2000,
        )

        content = response.choices[0].message.content
        code = _extract_code(content)

        if code and _validate_code(code):
            return code
        else:
            logger.error("生成的策略代码验证失败")
            return None

    except Exception as e:
        logger.error(f"调用LLM生成策略失败: {e}")
        return None


def _extract_code(text: str) -> Optional[str]:
    """从LLM回复中提取Python代码"""
    pattern = r"```python\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 尝试直接提取
    if "class CustomStrategy" in text:
        return text.strip()
    return None


def _validate_code(code: str) -> bool:
    """基础安全检查"""
    dangerous = [
        "import os", "import sys", "import subprocess",
        "open(", "exec(", "eval(",
        "__import__", "os.system",
        "requests.", "urllib.",
    ]
    for d in dangerous:
        if d in code:
            logger.warning(f"策略代码包含危险操作: {d}")
            return False

    # 确保有必要的类定义
    if "class CustomStrategy" not in code:
        return False
    if "generate_signals" not in code:
        return False

    return True


def compile_strategy(code: str) -> Optional[BaseStrategy]:
    """将代码字符串编译为策略实例"""
    try:
        import pandas as pd
        import numpy as np
        from app.strategies.base import BaseStrategy, Signal, StrategyContext

        namespace = {
            "pd": pd,
            "np": np,
            "BaseStrategy": BaseStrategy,
            "Signal": Signal,
            "StrategyContext": StrategyContext,
            "List": List,
        }

        exec(code, namespace)

        strategy_cls = namespace.get("CustomStrategy")
        if strategy_cls and issubclass(strategy_cls, BaseStrategy):
            return strategy_cls()
        return None

    except Exception as e:
        logger.error(f"编译策略代码失败: {e}")
        return None


def _fallback_code(description: str) -> str:
    """当LLM不可用时的默认回退代码（简单均线策略）"""
    return '''
from typing import List
import pandas as pd
import numpy as np
from app.strategies.base import BaseStrategy, Signal, StrategyContext


class CustomStrategy(BaseStrategy):
    """AI生成策略（回退版本）"""

    name = "ai_custom"
    description = """ + repr(description) + """
    default_params = {"short_window": 5, "long_window": 20}

    def generate_signals(self, ctx: StrategyContext) -> List[Signal]:
        df = ctx.history.copy()
        if len(df) < self.params.get("long_window", 20) + 1:
            return []

        short_w = self.params.get("short_window", 5)
        long_w = self.params.get("long_window", 20)

        df["ma_short"] = df["close"].rolling(window=short_w).mean()
        df["ma_long"] = df["close"].rolling(window=long_w).mean()
        df = df.dropna()

        if len(df) < 2:
            return []

        prev = df.iloc[-2]
        curr = df.iloc[-1]
        signals = []

        if prev["ma_short"] <= prev["ma_long"] and curr["ma_short"] > curr["ma_long"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="buy",
                reason="均线金叉买入",
            ))

        if prev["ma_short"] >= prev["ma_long"] and curr["ma_short"] < curr["ma_long"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="sell",
                reason="均线死叉卖出",
            ))

        return signals
'''
