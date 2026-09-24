"""申万一级行业板块轮动服务（数据源与评分口径借鉴 open-ai-workbench「宏观洞察」）

数据源（申万宏源研究所公开接口，免 key）：
  - index_publish/current                实时行情（31 行）
  - index_analysis/index_analysis_report 逐日分析（收盘/涨跌/换手/PE/PB/股息/成交占比/流通市值）

工程要点：
  - 站点只回叶证书：Python/Ubuntu 默认握手失败，用内置中间证书 + certifi 合成 bundle；无浏览器 UA 返回 508
  - 分析日报发布滞后：截止日取「覆盖 ≥ SW_MIN_INDUSTRIES 家的最新日期」
  - 一切失败独立降级：调用方捕获异常后使用最近已落库数据，不影响行情/策略管道
"""

import bisect
import logging
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.etf import ETFQuotation
from app.models.sw_industry import SwIndustryDaily

logger = logging.getLogger(__name__)

SW_CURRENT_URL = "https://www.swsresearch.com/institute-sw/api/index_publish/current/"
SW_ANALYSIS_URL = "https://www.swsresearch.com/institute-sw/api/index_analysis/index_analysis_report/"
SW_INDEX_TYPE = "一级行业"
SW_MIN_INDUSTRIES = 28        # 截止日最少覆盖家数（发布滞后时回退到上一完整日）
SW_WINDOW_DAYS = 120          # 分析日报抓取窗口（覆盖 60 日动量 + 缓冲）
SW_TIMEOUT = 30
BENCHMARK_ETF = "510300"      # 相对强度基准（沪深300ETF，复用本项目已有行情）
OVERWEIGHT_THRESHOLD = 67
UNDERWEIGHT_THRESHOLD = 33

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

CERT_PIN = "app/resources/certs/geotrust_g2_tls_cn_rsa4096_2022_ca1.pem"

# 申万一级行业 → ETF 名称关键词（板块 → 选型映射；宽基/跨行业 ETF 不在此表内）
SW_INDUSTRY_ETF_KEYWORDS: Dict[str, List[str]] = {
    "农林牧渔": ["农业", "畜牧", "养殖", "农牧"],
    "基础化工": ["化工"],
    "钢铁": ["钢铁"],
    "有色金属": ["有色金属", "有色", "稀有金属", "黄金"],
    "电子": ["半导体", "芯片", "消费电子", "电子"],
    "家用电器": ["家电"],
    "食品饮料": ["食品饮料", "白酒"],
    "纺织服饰": ["纺织", "服装"],
    "轻工制造": ["轻工", "造纸", "家居"],
    "医药生物": ["医药", "医疗", "创新药", "生物"],
    "公用事业": ["电力", "公用事业"],
    "交通运输": ["交通运输", "物流", "运输", "航空"],
    "房地产": ["房地产", "地产"],
    "商贸零售": ["商贸", "零售"],
    "社会服务": ["旅游", "酒店"],
    # 「综合」不设关键词：会误命中「信息技术综合ETF」这类名称（真实数据已踩到），未命中按中性处理
    "建筑材料": ["建材"],
    "建筑装饰": ["基建", "建筑"],
    "电力设备": ["新能源", "光伏", "电力设备", "电池", "储能"],
    "国防军工": ["军工", "国防", "高端装备"],
    "计算机": ["计算机", "信息技术", "软件", "云计算", "大数据", "人工智能"],
    "传媒": ["传媒", "游戏", "互联网"],
    "通信": ["通信", "5G", "通讯"],
    "银行": ["银行"],
    "非银金融": ["证券", "保险", "金融"],
    "汽车": ["新能源车", "智能汽车", "汽车"],
    "机械设备": ["机械", "装备", "机器人"],
    "煤炭": ["煤炭"],
    "石油石化": ["石油", "石化", "油气", "能源"],
    "环保": ["环保"],
    "美容护理": ["美容", "化妆"],
}

# 关键词索引：长词优先（避免「新能源车」被「新能源」抢走）
_KEYWORD_INDEX: List[Tuple[str, str]] = sorted(
    ((kw, sector) for sector, kws in SW_INDUSTRY_ETF_KEYWORDS.items() for kw in kws),
    key=lambda x: -len(x[0]),
)


# ---------------- 纯函数（可测） ----------------

def _to_float(value) -> Optional[float]:
    """字符串数字容错：''/'-'/None/异常值 → None"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in ("", "-", "—", "null", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_current(payload: dict) -> List[dict]:
    """实时行情：l3 昨收 / l8 最新 / l5 成交额（百万元）"""
    results = ((payload or {}).get("data") or {}).get("results") or []
    out = []
    for row in results:
        code = str(row.get("swindexcode") or "").strip()
        if not code:
            continue
        last = _to_float(row.get("l8"))
        prev = _to_float(row.get("l3"))
        change = (last / prev - 1) * 100 if (last and prev) else None
        out.append({
            "sw_code": code,
            "name": str(row.get("swindexname") or "").strip(),
            "live_close": last,
            "live_prev_close": prev,
            "live_change_pct": round(change, 2) if change is not None else None,
        })
    return out


def parse_analysis(payload: dict) -> List[dict]:
    """逐日分析：收盘 / 涨跌幅 / 换手 / PE / PB / 股息 / 成交占比 / 流通市值"""
    results = ((payload or {}).get("data") or {}).get("results") or []
    out = []
    for row in results:
        code = str(row.get("swindexcode") or "").strip()
        raw_date = str(row.get("bargaindate") or "")[:10]
        if not code or not raw_date:
            continue
        try:
            trade_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        out.append({
            "sw_code": code,
            "name": str(row.get("swindexname") or "").strip(),
            "trade_date": trade_date,
            "close": _to_float(row.get("closeindex")),
            "markup": _to_float(row.get("markup")),
            "turnover_rate": _to_float(row.get("turnoverrate")),
            "pe": _to_float(row.get("pe")),
            "pb": _to_float(row.get("pb")),
            "dividend_yield": _to_float(row.get("dp")),
            "amount_share": _to_float(row.get("bargainsumrate")),
            "float_mcap": _to_float(row.get("negotiablessharesum1")),
        })
    return out


def common_cutoff(rows: List[dict], min_industries: int = SW_MIN_INDUSTRIES) -> Optional[date]:
    """发布滞后处理：取覆盖 ≥ min_industries 家的最新日期"""
    counts = Counter(r["trade_date"] for r in rows)
    for day in sorted(counts.keys(), reverse=True):
        if counts[day] >= min_industries:
            return day
    return None


def _pct_rank(sorted_values: List[float], value: float) -> float:
    """value 在截面中的分位（0–1）"""
    if not sorted_values:
        return 0.5
    return bisect.bisect_right(sorted_values, value) / len(sorted_values)


def _ret60(closes: List[float]) -> Optional[float]:
    if len(closes) < 61 or not closes[-61]:
        return None
    return closes[-1] / closes[-61] - 1


def _vol60(closes: List[float]) -> Optional[float]:
    if len(closes) < 21:
        return None
    window = closes[-61:]
    rets = [window[i] / window[i - 1] - 1 for i in range(1, len(window)) if window[i - 1]]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def compute_rotation(
    series: Dict[str, List[Tuple[date, float]]],
    benchmark: List[Tuple[date, float]],
    as_of: date,
) -> Dict[str, dict]:
    """按截止日计算板块轮动指标：score = 100×(0.45×相对强度分位 + 0.35×动量分位 + 0.20×趋势)"""
    bench_closes = [c for d, c in benchmark if d <= as_of]
    bench_ret = _ret60(bench_closes)

    raw: Dict[str, dict] = {}
    for code, points in series.items():
        closes = [c for d, c in points if d <= as_of]
        closes = [c for c in closes if c]
        ret60 = _ret60(closes)
        rs60 = ret60 - bench_ret if (ret60 is not None and bench_ret is not None) else None
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        trend = (1.0 if closes[-1] > ma20 else 0.0) if (ma20 is not None and closes) else None
        raw[code] = {"ret60": ret60, "rs60": rs60, "trend": trend, "vol60": _vol60(closes)}

    rets = sorted(v["ret60"] for v in raw.values() if v["ret60"] is not None)
    if bench_ret is None and rets:
        # 基准缺失/过期时降级：以 31 行业等权平均收益作为「市场」基准
        bench_ret = sum(rets) / len(rets)
        for values in raw.values():
            if values["ret60"] is not None:
                values["rs60"] = values["ret60"] - bench_ret
    rss = sorted(v["rs60"] for v in raw.values() if v["rs60"] is not None)

    for code, values in raw.items():
        ret60, rs60, trend = values["ret60"], values["rs60"], values["trend"]
        if ret60 is None or rs60 is None or trend is None:
            values.update({"score": None, "signal": None})
            continue
        score = round(100 * (
            0.45 * _pct_rank(rss, rs60)
            + 0.35 * _pct_rank(rets, ret60)
            + 0.20 * trend
        ))
        values["score"] = score
        values["signal"] = (
            "overweight" if score >= OVERWEIGHT_THRESHOLD
            else "underweight" if score <= UNDERWEIGHT_THRESHOLD
            else "neutral"
        )
    return raw


def match_sector(etf_name: str) -> Optional[str]:
    """ETF 名称 → 申万一级行业（长关键词优先；未命中返回 None）"""
    if not etf_name:
        return None
    for keyword, sector in _KEYWORD_INDEX:
        if keyword in etf_name:
            return sector
    return None


# ---------------- 抓取（网络，独立降级） ----------------

_bundle_path: Optional[str] = None


def _cert_bundle() -> str:
    """certifi + 内置中间证书 → 合成 CA bundle（缓存到 app/data/）"""
    global _bundle_path
    if _bundle_path and Path(_bundle_path).exists():
        return _bundle_path

    import certifi

    runtime_dir = Path(get_settings().etf_list_cache_path).parent
    runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_dir / "sw_ca_bundle.pem"
    with open(certifi.where(), "r", encoding="utf-8") as base:
        content = base.read()
    with open(CERT_PIN, "r", encoding="utf-8") as extra:
        content += "\n" + extra.read()
    target.write_text(content, encoding="utf-8")
    _bundle_path = str(target)
    return _bundle_path


def _get_json(url: str, timeout: int = SW_TIMEOUT) -> dict:
    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": BROWSER_UA},
        verify=_cert_bundle(),
    )
    resp.raise_for_status()
    return resp.json()


def fetch_current() -> List[dict]:
    url = (
        f"{SW_CURRENT_URL}?page=1&page_size=50"
        f"&indextype={requests.utils.quote(SW_INDEX_TYPE)}"
    )
    return parse_current(_get_json(url))


def fetch_analysis(days: int = SW_WINDOW_DAYS) -> List[dict]:
    end = date.today()
    start = end - timedelta(days=days)
    url = (
        f"{SW_ANALYSIS_URL}?page=1&page_size=10000"
        f"&index_type={requests.utils.quote(SW_INDEX_TYPE)}"
        f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
        f"&type=DAY&swindexcode=all"
    )
    return parse_analysis(_get_json(url, timeout=SW_TIMEOUT + 15))


# ---------------- 落库与读取 ----------------

def _benchmark_series(db: Session, cutoff: date) -> List[Tuple[date, float]]:
    """基准（沪深300ETF）收盘序列：截止日往前取 90 个交易日

    基准最新数据落后截止日超过 5 个自然日时视为过期，返回空（由等权行业收益降级兜底）。
    """
    rows = (
        db.query(ETFQuotation.trade_date, ETFQuotation.close_price)
        .filter(ETFQuotation.etf_code == BENCHMARK_ETF, ETFQuotation.trade_date <= cutoff)
        .order_by(ETFQuotation.trade_date.desc())
        .limit(90)
        .all()
    )
    series = [(d, c) for d, c in reversed(rows) if c]
    if not series:
        return []
    if series[-1][0] < cutoff - timedelta(days=5):
        logger.warning(f"[SwIndustry] 基准 {BENCHMARK_ETF} 最新数据 {series[-1][0]} 落后截止日 {cutoff}，改用行业等权基准")
        return []
    return series


def _prev_scores(db: Session, cutoff: date) -> Dict[str, int]:
    prev_date = (
        db.query(func.max(SwIndustryDaily.trade_date))
        .filter(SwIndustryDaily.trade_date < cutoff)
        .scalar()
    )
    if not prev_date:
        return {}
    rows = db.query(SwIndustryDaily).filter(SwIndustryDaily.trade_date == prev_date).all()
    return {r.sw_code: r.score for r in rows if r.score is not None}


def sync(db: Session, days: int = SW_WINDOW_DAYS) -> dict:
    """抓取 → 解析 → 评分 → 落库（截止日）。失败抛异常，由调用方降级。"""
    current = fetch_current()
    analysis = fetch_analysis(days)
    cutoff = common_cutoff(analysis)
    if cutoff is None:
        raise RuntimeError(f"申万分析日报覆盖不足（{len(analysis)} 行，要求 ≥{SW_MIN_INDUSTRIES} 家/日）")

    series: Dict[str, List[Tuple[date, float]]] = {}
    meta: Dict[str, dict] = {}
    for row in analysis:
        if row["close"] is None:
            continue
        series.setdefault(row["sw_code"], []).append((row["trade_date"], row["close"]))
        if row["trade_date"] == cutoff:
            meta[row["sw_code"]] = row
    for points in series.values():
        points.sort()

    scores = compute_rotation(series, _benchmark_series(db, cutoff), cutoff)
    live = {c["sw_code"]: c for c in current}
    prev = _prev_scores(db, cutoff)

    existing = {
        r.sw_code: r for r in
        db.query(SwIndustryDaily).filter(SwIndustryDaily.trade_date == cutoff).all()
    }

    updated = 0
    overweight = underweight = 0
    for code, row in meta.items():
        values = scores.get(code) or {}
        if values.get("signal") == "overweight":
            overweight += 1
        elif values.get("signal") == "underweight":
            underweight += 1
        payload = {
            "name": row["name"],
            "close": row["close"],
            "markup": row["markup"],
            "turnover_rate": row["turnover_rate"],
            "pe": row["pe"],
            "pb": row["pb"],
            "dividend_yield": row["dividend_yield"],
            "amount_share": row["amount_share"],
            "float_mcap": row["float_mcap"],
            "ret60": values.get("ret60"),
            "rs60": values.get("rs60"),
            "trend": values.get("trend"),
            "vol60": values.get("vol60"),
            "score": values.get("score"),
            "signal": values.get("signal"),
            "score_delta": (
                values["score"] - prev[code]
                if (values.get("score") is not None and prev.get(code) is not None)
                else None
            ),
            "live_change_pct": (live.get(code) or {}).get("live_change_pct"),
        }
        target = existing.get(code)
        if target:
            for key, value in payload.items():
                setattr(target, key, value)
        else:
            db.add(SwIndustryDaily(trade_date=cutoff, sw_code=code, **payload))
        updated += 1

    db.commit()
    summary = {
        "cutoff": cutoff.isoformat(),
        "industries": updated,
        "overweight": overweight,
        "underweight": underweight,
        "amount_share_sum": round(sum(r["amount_share"] or 0 for r in meta.values()), 1),
        "prev_scored_date": None,
    }
    logger.info(f"[SwIndustry] 板块同步完成: {summary}")
    return summary


def get_sector_view(db: Session, trade_date: Optional[date] = None) -> dict:
    """最新板块视图（只读 DB）：按评分降序 + 元信息"""
    if trade_date is None:
        trade_date = db.query(func.max(SwIndustryDaily.trade_date)).scalar()
    if trade_date is None:
        return {"as_of": None, "rows": [], "overweight": 0, "underweight": 0, "total": 0}

    rows = (
        db.query(SwIndustryDaily)
        .filter(SwIndustryDaily.trade_date == trade_date)
        .all()
    )
    rows.sort(key=lambda r: (r.score is None, -(r.score or 0)))
    return {
        "as_of": trade_date.isoformat(),
        "rows": [{
            "sw_code": r.sw_code,
            "name": r.name,
            "close": r.close,
            "markup": r.markup,
            "live_change_pct": r.live_change_pct,
            "turnover_rate": r.turnover_rate,
            "pe": r.pe,
            "pb": r.pb,
            "dividend_yield": r.dividend_yield,
            "amount_share": r.amount_share,
            "float_mcap": r.float_mcap,
            "ret60": r.ret60,
            "rs60": r.rs60,
            "trend": r.trend,
            "vol60": r.vol60,
            "score": r.score,
            "signal": r.signal,
            "score_delta": r.score_delta,
            "rank": idx + 1,
        } for idx, r in enumerate(rows)],
        "overweight": sum(1 for r in rows if r.signal == "overweight"),
        "underweight": sum(1 for r in rows if r.signal == "underweight"),
        "total": len(rows),
    }


def get_etf_sector_signals(db: Session, etf_names: Dict[str, str]) -> Dict[str, dict]:
    """ETF → 申万板块信号（名称关键词反查最新板块评分）；无数据/未命中优雅降级"""
    if not etf_names:
        return {}
    view = get_sector_view(db)
    rows = view.get("rows") or []
    if not rows:
        return {}
    by_name = {r["name"]: r for r in rows}
    total = len(rows)
    signals: Dict[str, dict] = {}
    for code, name in etf_names.items():
        sector = match_sector(name or "")
        row = by_name.get(sector) if sector else None
        if not row:
            continue
        signals[code] = {
            "sector": sector,
            "score": row.get("score"),
            "signal": row.get("signal"),
            "rank": row.get("rank"),
            "total": total,
            "pe": row.get("pe"),
            "pb": row.get("pb"),
            "amount_share": row.get("amount_share"),
            "ret60": row.get("ret60"),
            "as_of": view.get("as_of"),
        }
    return signals


class SwIndustryService:
    """单例外观：抓取落库 + 只读视图（供管道/工具/依据层调用）"""

    def sync(self, db: Session, days: int = SW_WINDOW_DAYS) -> dict:
        return sync(db, days)

    def get_sector_view(self, db: Session, trade_date: Optional[date] = None) -> dict:
        return get_sector_view(db, trade_date)

    def get_etf_sector_signals(self, db: Session, etf_names: Dict[str, str]) -> Dict[str, dict]:
        return get_etf_sector_signals(db, etf_names)


_service: Optional[SwIndustryService] = None


def get_sw_industry_service() -> SwIndustryService:
    global _service
    if _service is None:
        _service = SwIndustryService()
    return _service