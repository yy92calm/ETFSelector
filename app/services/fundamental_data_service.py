"""个股基本面数据服务 - baostock 估值/季度增速/行业分类同步

池口径：沪深300 + 中证500（机构核心交易池，控制取数规模）。
估值日频增量走每日管道；历史回填与增速/行业刷新走手动触发（较慢）。
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.stock_fundamental import StockFundamental

logger = logging.getLogger(__name__)


class FundamentalDataService:
    """baostock 数据同步：池成员 / 行业分类 / 日频估值 / 季度增速"""

    def __init__(self):
        self._logged_in = False

    # ---------------- baostock 会话与查询原语 ----------------

    def _login(self):
        import baostock as bs
        if not self._logged_in:
            lg = bs.login()
            if lg.error_code != "0":
                raise RuntimeError(f"baostock登录失败: {lg.error_msg}")
            self._logged_in = True

    def _ensure_logout(self):
        import baostock as bs
        if self._logged_in:
            self._logged_in = False
            try:
                bs.logout()
            except Exception:  # noqa: BLE001
                pass

    def _query_rows(self, result_set, limit: Optional[int] = None) -> List[list]:
        rows = []
        while result_set.error_code == "0" and result_set.next():
            rows.append(result_set.get_row_data())
            if limit and len(rows) >= limit:
                break
        return rows

    # ---------------- 基础数据获取 ----------------

    def fetch_pool_members(self) -> Dict[str, str]:
        """沪深300 + 中证500 成分 {bs_code: name}"""
        self._login()
        import baostock as bs
        pool: Dict[str, str] = {}
        for fn in (bs.query_hs300_stocks, bs.query_zz500_stocks):
            for row in self._query_rows(fn()):
                if len(row) >= 3:
                    pool[row[1]] = row[2]
        return pool

    def fetch_industry_map(self) -> Dict[str, str]:
        """全市场行业分类 {bs_code: 行业中文名}（接口较慢，约数分钟）"""
        self._login()
        import baostock as bs
        result: Dict[str, str] = {}
        for row in self._query_rows(bs.query_stock_industry()):
            if len(row) >= 4:
                # [updateDate, code, code_name, "J66货币金融服务", ...]
                industry = row[3].split(" ", 1)[-1] if " " in row[3] else row[3]
                industry = industry.split("）")[-1] if "）" in industry else industry
                result[row[1]] = industry
        return result

    def fetch_valuation(self, bs_code: str, start: date, end: date) -> List[dict]:
        """个股日频估值 [{date, close, pe_ttm, pb_mrq}]"""
        self._login()
        import baostock as bs
        rows = self._query_rows(bs.query_history_k_data_plus(
            bs_code, "date,close,peTTM,pbMRQ",
            start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"),
            frequency="d", adjustflag="3",
        ))
        out = []
        for r in rows:
            try:
                out.append({
                    "date": date.fromisoformat(r[0]),
                    "close": float(r[1]) if r[1] else None,
                    "pe_ttm": float(r[2]) if r[2] else None,
                    "pb_mrq": float(r[3]) if r[3] else None,
                })
            except (ValueError, IndexError):
                continue
        return out

    def fetch_growth(self, bs_code: str, year: int, quarter: int) -> Optional[dict]:
        """单股单季成长 {yoy_ni%, stat_date}，无数据返回 None"""
        self._login()
        import baostock as bs
        rows = self._query_rows(
            bs.query_growth_data(code=bs_code, year=year, quarter=quarter), limit=1
        )
        if not rows or len(rows[0]) < 6:
            return None
        r = rows[0]
        try:
            yoy_ni = float(r[5]) * 100 if r[5] not in ("", None) else None
        except ValueError:
            yoy_ni = None
        try:
            stat_date = date.fromisoformat(r[2]) if r[2] else None
        except ValueError:
            stat_date = None
        if yoy_ni is None:
            return None
        return {"yoy_ni": round(yoy_ni, 2), "stat_date": stat_date}

    @staticmethod
    def recent_quarters(ref: date) -> List[Tuple[int, int]]:
        """按披露节奏返回最近两个应已披露的 (年, 季)，最新的在前"""
        if ref >= date(ref.year, 10, 31):
            return [(ref.year, 3), (ref.year, 2)]
        if ref >= date(ref.year, 8, 31):
            return [(ref.year, 2), (ref.year, 1)]
        if ref >= date(ref.year, 4, 30):
            return [(ref.year, 1), (ref.year - 1, 4)]
        return [(ref.year - 1, 3), (ref.year - 1, 2)]

    def sync_growth(self, pool_codes: List[str], ref: Optional[date] = None) -> Dict[str, dict]:
        """全池增速刷新：最新已披露季优先，缺失回退上一季"""
        quarters = self.recent_quarters(ref or date.today())
        result: Dict[str, dict] = {}
        for i, code in enumerate(pool_codes):
            for y, q in quarters:
                g = self.fetch_growth(code, y, q)
                if g:
                    result[code] = g
                    break
            if (i + 1) % 100 == 0:
                logger.info(f"[Fundamental] 增速刷新进度 {i + 1}/{len(pool_codes)}")
        logger.info(f"[Fundamental] 增速刷新完成: {len(result)}/{len(pool_codes)} 只有效")
        return result

    # ---------------- 落库同步 ----------------

    def _latest_growth_from_db(self, db: Session) -> Dict[str, dict]:
        """从表内最近一行提取增速缓存（避免每日重拉）"""
        rows = db.query(
            StockFundamental.stock_code,
            StockFundamental.ni_yoy,
            StockFundamental.growth_stat_date,
        ).order_by(StockFundamental.stock_code, StockFundamental.trade_date.desc()).all()
        result: Dict[str, dict] = {}
        seen = set()
        for code, ni_yoy, stat_date in rows:
            if code in seen:
                continue
            seen.add(code)
            if ni_yoy is not None:
                result[code] = {"yoy_ni": ni_yoy, "stat_date": stat_date}
        return result

    def sync_fundamentals(self, db: Session, days_back: int = 5,
                          refresh_growth: bool = False,
                          refresh_industry: bool = False) -> Dict:
        """同步池内估值并落库（幂等：同 (code, date) 覆盖更新）

        Args:
            days_back: 回看自然日数（管道增量传 5 即可）
            refresh_growth: True=全池重拉季度增速（慢，回填/每季用）
            refresh_industry: True=重拉全市场行业分类（慢，回填用）
        """
        started = date.today() - timedelta(days=days_back)
        pool = self.fetch_pool_members()
        if not pool:
            raise RuntimeError("池成员获取失败（baostock）")
        logger.info(f"[Fundamental] 池成员 {len(pool)} 只，回看 {started} 起")

        if refresh_industry:
            industries = self.fetch_industry_map()
            logger.info(f"[Fundamental] 行业分类刷新: {len(industries)} 只")
        else:
            industries = {}

        growth = (self.sync_growth(list(pool.keys())) if refresh_growth
                  else self._latest_growth_from_db(db))

        added, updated = 0, 0
        try:
            for i, (code, name) in enumerate(pool.items()):
                vals = self.fetch_valuation(code, started, date.today())
                if not vals:
                    continue
                ind = industries.get(code)
                g = growth.get(code, {})
                for v in vals:
                    existing = db.query(StockFundamental).filter(
                        StockFundamental.stock_code == code,
                        StockFundamental.trade_date == v["date"],
                    ).first()
                    if existing is None:
                        db.add(StockFundamental(
                            stock_code=code, stock_name=name, trade_date=v["date"],
                            industry=ind, close=v["close"], pe_ttm=v["pe_ttm"],
                            pb_mrq=v["pb_mrq"], ni_yoy=g.get("yoy_ni"),
                            growth_stat_date=g.get("stat_date"),
                        ))
                        added += 1
                    else:
                        existing.close = v["close"]
                        existing.pe_ttm = v["pe_ttm"]
                        existing.pb_mrq = v["pb_mrq"]
                        if ind:
                            existing.industry = ind
                        if refresh_growth and g.get("yoy_ni") is not None:
                            existing.ni_yoy = g["yoy_ni"]
                            existing.growth_stat_date = g.get("stat_date")
                        updated += 1
                if (i + 1) % 100 == 0:
                    db.commit()
                    logger.info(f"[Fundamental] 估值同步进度 {i + 1}/{len(pool)}")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            self._ensure_logout()

        summary = {"pool": len(pool), "added": added, "updated": updated,
                   "with_growth": len(growth)}
        logger.info(f"[Fundamental] 同步完成: {summary}")
        return summary


_service = None


def get_fundamental_data_service() -> FundamentalDataService:
    global _service
    if _service is None:
        _service = FundamentalDataService()
    return _service
