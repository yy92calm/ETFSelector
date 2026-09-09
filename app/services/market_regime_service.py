"""市场状态刻画服务 - 风险偏好/风格轮动/基金收益率分化度 → 中期市场状态与仓位信号

数据来源：
- 代理ETF组动量/热度/波动：本地 ETFQuotation / ETFDailyIndicator（每日管道 market_scan 阶段产出）
- 偏股基金收益率分化度：efinance 基金净值（DataSourceManager.fetch_fund_nav_batch）
"""

import logging
import random
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.etf import ETFDailyIndicator, ETFQuotation
from app.models.market_regime import MarketRegimeSnapshot

logger = logging.getLogger(__name__)

# 代理ETF组（代码在库中无行情时自动跳过该代码；整组无数据则跳过该维度）
BETA_PROXIES = ["512880", "159915", "588000"]           # 高beta权益：券商/创业板/科创
DEFENSIVE_PROXIES = ["511010", "511260", "511020",
                     "518880", "518850", "159934"]      # 防御：国债/货币/黄金
LARGE_CAP_PROXIES = ["510050", "510300", "510500"]      # 大盘宽基
SMALL_CAP_PROXIES = ["512100"]                          # 小盘
GROWTH_PROXIES = ["588000", "159915"]                   # 成长
VALUE_PROXIES = ["510880"]                              # 红利/价值

MOMENTUM_WINDOW = 20            # 代理动量回看交易日数
STYLE_SPREAD_THRESHOLD = 3.0    # 风格占优的动量价差阈值（%）
HEAT_WINDOW = 250               # 热度/波动分位回看交易日数
MIN_PERCENTILE_SAMPLES = 60     # 分位计算最少样本天数
FUND_SAMPLE_SIZE = 100          # 偏股基金抽样数量
FUND_RETURN_WINDOW = 60         # 基金收益回看交易日数
MIN_FUND_SAMPLES = 30           # 分化度最少有效基金样本数
# 分化度阈值（60日收益截面标准差%），初期用绝对阈值，样本积累后可改为历史分位
DISPERSION_THRESHOLDS = [(8.0, "一致"), (15.0, "适度")]


_fund_sample_cache: Optional[List[str]] = None


class MarketRegimeService:
    """中期市场状态刻画：风险偏好 / 风格轮动 / 基金收益率分化度 → 仓位信号"""

    # ---------------- 对外入口 ----------------

    def compute(self, trade_date: date, db: Session) -> MarketRegimeSnapshot:
        """计算并落库当日市场状态快照（幂等：同日更新覆盖）"""
        details: Dict = {}

        risk_appetite, risk_label, appetite_detail = self._compute_risk_appetite(db)
        details["risk_appetite"] = appetite_detail

        style_rotation = self._compute_style_rotation(db)
        details["style_rotation"] = style_rotation

        fund_dispersion, dispersion_label, dispersion_detail = self._compute_fund_dispersion()
        details["fund_dispersion"] = dispersion_detail

        equity_range = self._suggest_equity_range(risk_appetite, dispersion_label)
        state, state_label, note = self._build_state(
            risk_appetite, risk_label, style_rotation, dispersion_label, equity_range
        )

        snap = db.query(MarketRegimeSnapshot).filter(
            MarketRegimeSnapshot.trade_date == trade_date
        ).first()
        if snap is None:
            snap = MarketRegimeSnapshot(trade_date=trade_date)
            db.add(snap)

        snap.risk_appetite = risk_appetite
        snap.risk_label = risk_label
        snap.style_rotation = style_rotation or {}
        snap.fund_dispersion = fund_dispersion
        snap.dispersion_label = dispersion_label
        snap.market_state = state
        snap.state_label = state_label
        snap.state_note = note
        snap.suggested_equity_range = equity_range
        snap.details = details
        db.commit()
        logger.info(f"[MarketRegime] {trade_date} 风险偏好={risk_appetite}({risk_label}) "
                    f"分化度={fund_dispersion}({dispersion_label}) 状态={state_label} "
                    f"建议权益{equity_range[0]:.0%}-{equity_range[1]:.0%}")
        return snap

    def get_latest(self, db: Session) -> Optional[MarketRegimeSnapshot]:
        return db.query(MarketRegimeSnapshot).order_by(
            MarketRegimeSnapshot.trade_date.desc()
        ).first()

    def get_history(self, db: Session, days: int = 30) -> List[MarketRegimeSnapshot]:
        cutoff = date.today() - timedelta(days=days)
        return db.query(MarketRegimeSnapshot).filter(
            MarketRegimeSnapshot.trade_date >= cutoff
        ).order_by(MarketRegimeSnapshot.trade_date.desc()).all()

    # ---------------- 风险偏好 ----------------

    def _compute_risk_appetite(self, db: Session) -> Tuple[Optional[float], Optional[str], Dict]:
        """风险偏好 = 0.6×高beta-防御动量差映射 + 0.25×成交热度分位 + 0.15×低波动分位"""
        detail: Dict = {}
        beta_mom = self._group_momentum(db, BETA_PROXIES)
        def_mom = self._group_momentum(db, DEFENSIVE_PROXIES)
        detail["beta_momentum"] = beta_mom
        detail["defensive_momentum"] = def_mom

        components: List[Tuple[float, float]] = []  # (value, weight)
        if beta_mom is not None and def_mom is not None:
            # ±20% 动量差映射为 ±50 分
            components.append((50 + (beta_mom - def_mom) * 2.5, 0.6))
        heat = self._heat_percentile(db)
        detail["turnover_heat_percentile"] = heat
        if heat is not None:
            components.append((heat, 0.25))
        vol_pct = self._volatility_percentile(db)
        detail["volatility_percentile"] = vol_pct
        if vol_pct is not None:
            components.append((100 - vol_pct, 0.15))

        if not components:
            return None, None, detail
        appetite = round(sum(v * w for v, w in components) / sum(w for _, w in components), 1)
        appetite = max(0.0, min(100.0, appetite))
        return appetite, self._appetite_label(appetite), detail

    @staticmethod
    def _appetite_label(appetite: float) -> str:
        if appetite < 35:
            return "保守"
        if appetite < 65:
            return "中性"
        return "进取"

    def _group_momentum(self, db: Session, codes: List[str]) -> Optional[float]:
        """组内各ETF近N日动量均值（%），无可用数据返回 None"""
        moms = []
        for code in codes:
            quotes = db.query(ETFQuotation.close_price).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(MOMENTUM_WINDOW + 1).all()
            if len(quotes) < MOMENTUM_WINDOW + 1 or not quotes[-1].close_price:
                continue
            mom = (quotes[0].close_price / quotes[-1].close_price - 1) * 100
            moms.append(mom)
        if not moms:
            return None
        return round(float(np.mean(moms)), 2)

    def _heat_percentile(self, db: Session) -> Optional[float]:
        """全池日成交额合计的最新值在近250个交易日中的分位（0-100）"""
        rows = db.query(
            ETFQuotation.trade_date, func.sum(ETFQuotation.amount)
        ).group_by(ETFQuotation.trade_date).order_by(
            ETFQuotation.trade_date.desc()
        ).limit(HEAT_WINDOW).all()
        series = [float(r[1]) for r in rows if r[1] is not None]
        return self._percentile_of_latest(series)

    def _volatility_percentile(self, db: Session) -> Optional[float]:
        """全池平均20日年化波动率的最新值在近250个交易日中的分位（0-100）"""
        rows = db.query(
            ETFDailyIndicator.trade_date, func.avg(ETFDailyIndicator.volatility_20d)
        ).group_by(ETFDailyIndicator.trade_date).order_by(
            ETFDailyIndicator.trade_date.desc()
        ).limit(HEAT_WINDOW).all()
        series = [float(r[1]) for r in rows if r[1] is not None]
        return self._percentile_of_latest(series)

    @staticmethod
    def _percentile_of_latest(series: List[float]) -> Optional[float]:
        if len(series) < MIN_PERCENTILE_SAMPLES:
            return None
        latest = series[0]  # 调用方按日期倒序取数
        arr = np.array(series)
        return round(float(np.searchsorted(np.sort(arr), latest) / len(arr) * 100), 1)

    # ---------------- 风格轮动 ----------------

    def _compute_style_rotation(self, db: Session) -> Dict:
        """大小盘 / 成长价值 两组动量价差"""
        rotation: Dict = {}
        size = self._pair_spread(db, LARGE_CAP_PROXIES, SMALL_CAP_PROXIES, "大盘", "小盘")
        if size:
            rotation["size"] = size
        gv = self._pair_spread(db, GROWTH_PROXIES, VALUE_PROXIES, "成长", "价值")
        if gv:
            rotation["growth_value"] = gv
        return rotation

    def _pair_spread(self, db: Session, a_codes: List[str], b_codes: List[str],
                     a_label: str, b_label: str) -> Optional[Dict]:
        ma = self._group_momentum(db, a_codes)
        mb = self._group_momentum(db, b_codes)
        if ma is None or mb is None:
            return None
        spread = round(ma - mb, 2)
        if spread > STYLE_SPREAD_THRESHOLD:
            leading = a_label
        elif spread < -STYLE_SPREAD_THRESHOLD:
            leading = b_label
        else:
            leading = "均衡"
        return {"leading": leading, "spread": spread,
                f"{a_label}_momentum": ma, f"{b_label}_momentum": mb}

    # ---------------- 基金收益率分化度 ----------------

    def _compute_fund_dispersion(self) -> Tuple[Optional[float], Optional[str], Dict]:
        """偏股基金近60日收益截面标准差（%），数据不可得返回 None"""
        detail: Dict = {}
        try:
            codes = self._sample_fund_codes()
            detail["sample_target"] = len(codes)
            if not codes:
                detail["error"] = "偏股基金名单获取失败"
                return None, None, detail
            from app.services.data_sources import get_data_source_manager
            navs = get_data_source_manager().fetch_fund_nav_batch(
                codes, pz=FUND_RETURN_WINDOW + 20
            )
            returns = []
            for code, df in (navs or {}).items():
                if df is None or len(df) < FUND_RETURN_WINDOW + 1:
                    continue
                df = df.sort_values("日期")
                base = df["累计净值"].iloc[-(FUND_RETURN_WINDOW + 1)]
                last = df["累计净值"].iloc[-1]
                if not base or not last:
                    continue
                returns.append((float(last) / float(base) - 1) * 100)
            detail["valid_samples"] = len(returns)
            if len(returns) < MIN_FUND_SAMPLES:
                detail["error"] = "有效基金样本不足"
                return None, None, detail
            std = round(float(np.std(returns)), 2)
            detail["mean_return"] = round(float(np.mean(returns)), 2)
            return std, self._dispersion_label(std), detail
        except Exception as e:
            logger.warning(f"[MarketRegime] 基金分化度计算失败: {e}")
            detail["error"] = str(e)
            return None, None, detail

    @staticmethod
    def _dispersion_label(std: float) -> str:
        for threshold, label in DISPERSION_THRESHOLDS:
            if std < threshold:
                return label
        return "分化"

    @staticmethod
    def _sample_fund_codes() -> List[str]:
        """偏股型（股票型+混合型）基金名单固定种子抽样，结果进程内缓存保证每日同一样本"""
        global _fund_sample_cache
        if _fund_sample_cache:
            return _fund_sample_cache
        try:
            import pandas as pd
            import efinance as ef
            dfs = []
            for ft in ("gp", "hh"):
                df = ef.fund.get_fund_codes(ft)
                if df is not None and not df.empty:
                    dfs.append(df)
            if not dfs:
                return []
            codes = pd.concat(dfs)["基金代码"].astype(str).unique().tolist()
            rng = random.Random(42)
            _fund_sample_cache = rng.sample(codes, min(FUND_SAMPLE_SIZE, len(codes)))
            return _fund_sample_cache
        except Exception as e:
            logger.warning(f"[MarketRegime] 偏股基金名单获取失败: {e}")
            return []

    # ---------------- 仓位建议与状态合成 ----------------

    @staticmethod
    def _suggest_equity_range(risk_appetite: Optional[float],
                              dispersion_label: Optional[str]) -> List[float]:
        """中期权益仓位建议区间"""
        if risk_appetite is None:
            return [0.4, 0.7]
        if risk_appetite >= 65:
            # 分化度高=α行情，结构重于仓位，上限略降
            return [0.6, 0.8] if dispersion_label == "分化" else [0.7, 0.9]
        if risk_appetite >= 35:
            return [0.4, 0.7]
        return [0.1, 0.4]

    def _build_state(self, risk_appetite: Optional[float], risk_label: Optional[str],
                     style_rotation: Dict, dispersion_label: Optional[str],
                     equity_range: List[float]) -> Tuple[str, str, str]:
        """合成市场状态与说明"""
        styles = "、".join(
            f"{'大小盘' if key == 'size' else '成长价值'}{v['leading']}占优({v['spread']:+.1f}%)"
            for key, v in (style_rotation or {}).items() if v.get("leading") not in (None, "均衡")
        ) or "风格均衡"
        dispersion_meaning = {
            "一致": "基金收益趋同，β行情为主",
            "适度": "基金收益适度分化",
            "分化": "基金收益分化明显，α行情为主，结构重于仓位",
        }.get(dispersion_label or "", "分化度数据缺失")

        if risk_appetite is None:
            return ("neutral", "中性", f"核心数据缺失，维持中性假设，建议权益仓位"
                    f"{equity_range[0]:.0%}-{equity_range[1]:.0%}。{styles}")
        if risk_appetite >= 65:
            state, label = "opportunity", "机会"
        elif risk_appetite < 35:
            state, label = "risk", "风险"
        else:
            state, label = "neutral", "中性"
        note = (f"风险偏好{risk_label}（{risk_appetite}），{styles}，{dispersion_meaning}，"
                f"建议权益仓位{equity_range[0]:.0%}-{equity_range[1]:.0%}。")
        return state, label, note


_service = None


def get_market_regime_service() -> MarketRegimeService:
    global _service
    if _service is None:
        _service = MarketRegimeService()
    return _service
