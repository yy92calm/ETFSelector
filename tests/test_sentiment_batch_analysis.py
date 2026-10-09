"""舆情批量情感分析：LLM 往返从「每条一次」降为「每批一次」，且结论不错位"""
import json
import re
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models.etf import ETFBasic
from app.models.sentiment import SentimentData
from app.services.sentiment_service import SentimentService

DAY = date(2026, 9, 25)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_llm(payload):
    """构造返回固定内容的假 LLM 客户端，并暴露调用次数"""
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


def news(n, prefix="新闻"):
    return [{"title": f"{prefix}{i} 芯片板块大涨", "content": f"内容{i}", "source": "eastmoney"}
            for i in range(1, n + 1)]


class TestBatchCallCount(unittest.TestCase):
    def setUp(self):
        self.svc = SentimentService()
        self.svc.llm_client = None
        self.db = make_db()
        self.db.add(ETFBasic(etf_code="512480", etf_name="半导体ETF"))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_calls_scale_with_batches_not_items(self):
        """20条 / batch=8 → 3 次调用，而不是 20 次"""
        items = news(20)
        captured = []

        def fake_create(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            captured.append(prompt)
            count = int(re.search(r"分析以下 (\d+) 条财经新闻", prompt).group(1))
            payload = [{"index": j, "sentiment_score": 0.6,
                        "sentiment_label": "positive", "key_factors": []}
                       for j in range(1, count + 1)]
            return make_llm(payload).chat.completions.create.return_value

        client = MagicMock()
        client.chat.completions.create.side_effect = fake_create
        self.svc.llm_client = client

        out = self.svc._analyze_sentiment_batch(items, self.db)

        self.assertEqual(len(out), 20, "结果必须与入参等长")
        self.assertEqual(client.chat.completions.create.call_count, 3,
                         f"应为3批，实际调用 {client.chat.completions.create.call_count} 次")
        self.assertEqual([p.count("标题:") for p in captured], [8, 8, 4],
                         "三批应分别打包 8/8/4 条新闻")
        self.assertTrue(all(o["sentiment_score"] == 0.6 for o in out))

    def test_available_etfs_resolved_once(self):
        """可用ETF清单不得在每条新闻里重复拼进 prompt"""
        self.svc.llm_client = make_llm([])
        with patch.object(self.svc, "_get_available_etfs",
                          wraps=self.svc._get_available_etfs) as spy:
            self.svc._analyze_sentiment_batch(news(20), self.db)
            self.assertEqual(spy.call_count, 1)

    def test_batch_size_one_behaves_like_per_item(self):
        self.svc.llm_client = make_llm([{"index": 1, "sentiment_score": 0.2,
                                         "sentiment_label": "positive"}])
        with patch("app.services.sentiment_service.settings") as st:
            st.sentiment_analysis_batch_size = 1
            st.llm_model = "m"
            out = self.svc._analyze_sentiment_batch(news(4), self.db)
        self.assertEqual(len(out), 4)
        self.assertEqual(self.svc.llm_client.chat.completions.create.call_count, 4)


class TestAlignment(unittest.TestCase):
    """LLM 回包的 index 必须严格对齐到对应新闻，错一个都不能脏数据"""

    def setUp(self):
        self.svc = SentimentService()
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def _batch(self, items, payload, side_effect=None):
        client = MagicMock()
        if side_effect:
            client.chat.completions.create.side_effect = side_effect
        else:
            client.chat.completions.create.return_value = make_llm(payload).chat.completions.create()
        self.svc.llm_client = client
        return self.svc._analyze_sentiment_batch(items, self.db)

    def test_index_maps_to_right_item(self):
        items = [{"title": "A利好", "content": ""}, {"title": "B利空", "content": ""}]
        out = self._batch(items, [
            {"index": 2, "sentiment_score": -0.9, "sentiment_label": "negative", "key_factors": ["跌"]},
            {"index": 1, "sentiment_score": 0.9, "sentiment_label": "positive", "key_factors": ["涨"]},
        ])
        self.assertEqual(out[0]["sentiment_score"], 0.9)
        self.assertEqual(out[1]["sentiment_score"], -0.9)

    def test_missing_slot_falls_back_to_keywords(self):
        """LLM 只答了第1条，第2条必须退回关键词评分而不是留空"""
        items = [{"title": "政策利好来袭", "content": ""}, {"title": "板块暴跌", "content": ""}]
        out = self._batch(items, [{"index": 1, "sentiment_score": 0.8,
                                   "sentiment_label": "positive"}])
        self.assertEqual(out[0]["sentiment_score"], 0.8, "已答条目采信LLM结论")
        self.assertIsNotNone(out[1]["sentiment_score"], "缺位必须有回退结论")
        self.assertLess(out[1]["sentiment_score"], 0, "关键词应把「暴跌」判为负面")
        self.assertEqual(out[1]["sentiment_label"], "negative")

    def test_out_of_range_index_ignored(self):
        items = [{"title": "A", "content": ""}]
        out = self._batch(items, [{"index": 99, "sentiment_score": 0.5,
                                   "sentiment_label": "positive"}])
        self.assertEqual(out[0]["sentiment_label"], "neutral", "越界index不得采信")

    def test_duplicate_index_uses_first(self):
        items = [{"title": "芯片大涨", "content": ""}, {"title": "白酒", "content": ""}]
        out = self._batch(items, [
            {"index": 1, "sentiment_score": 0.7, "sentiment_label": "positive"},
            {"index": 1, "sentiment_score": -0.7, "sentiment_label": "negative"},
        ])
        self.assertEqual(out[0]["sentiment_score"], 0.7)

    def test_single_item_batch_without_index_accepted(self):
        """末批只剩1条时模型常省 index，应能采信而非白退回关键词"""
        items = [{"title": "央行降息利好", "content": ""}]
        out = self._batch(items, {"sentiment_score": 0.8, "sentiment_label": "positive"})
        self.assertEqual(out[0]["sentiment_score"], 0.8)

    def test_non_list_entries_skipped(self):
        items = [{"title": "芯片大涨", "content": ""}, {"title": "下跌", "content": ""}]
        out = self._batch(items, ["垃圾字符串", {"index": 2, "sentiment_score": -0.5,
                                                 "sentiment_label": "negative"}])
        self.assertEqual(out[1]["sentiment_score"], -0.5)
        self.assertIsNotNone(out[0]["sentiment_score"])

    def test_json_wrapped_in_prose(self):
        items = [{"title": "A", "content": ""}]
        client = MagicMock()
        client.chat.completions.create.return_value = make_llm(
            "好的，分析如下：\n[{\"index\": 1, \"sentiment_score\": 0.4, \"sentiment_label\": \"positive\"}]\n以上。"
        ).chat.completions.create()
        self.svc.llm_client = client
        out = self.svc._analyze_sentiment_batch(items, self.db)
        self.assertEqual(out[0]["sentiment_score"], 0.4)


class TestFailureFallback(unittest.TestCase):
    def setUp(self):
        self.svc = SentimentService()
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def test_llm_exception_degrades_to_keywords_for_whole_batch(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("timeout")
        self.svc.llm_client = client
        items = [{"title": "板块暴跌", "content": ""}, {"title": "利好来袭", "content": ""}]
        out = self.svc._analyze_sentiment_batch(items, self.db)
        self.assertEqual(len(out), 2)
        self.assertLess(out[0]["sentiment_score"], 0)
        self.assertGreater(out[1]["sentiment_score"], 0)

    def test_unparseable_response_degrades_to_keywords(self):
        self.svc.llm_client = make_llm("对不起我无法分析")
        out = self.svc._analyze_sentiment_batch([{"title": "大幅下跌", "content": ""}], self.db)
        self.assertLess(out[0]["sentiment_score"], 0)

    def test_no_llm_client_uses_keywords_only(self):
        self.svc.llm_client = None
        out = self.svc._analyze_sentiment_batch(
            [{"title": "涨停", "content": ""}, {"title": "违约", "content": ""}], self.db)
        self.assertGreater(out[0]["sentiment_score"], 0)
        self.assertLess(out[1]["sentiment_score"], 0)

    def test_empty_input_returns_empty(self):
        self.svc.llm_client = make_llm([])
        self.assertEqual(self.svc._analyze_sentiment_batch([], self.db), [])


class TestCollectPersistence(unittest.TestCase):
    """collect_daily_sentiment 改造后仍需完整入库、去重、评分"""

    def setUp(self):
        self.svc = SentimentService()
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def _collect(self, fetched, llm_payload):
        self.svc.llm_client = make_llm(llm_payload)
        with patch.object(self.svc, "_fetch_financial_news", return_value=fetched):
            return self.svc.collect_daily_sentiment(DAY, self.db)

    def test_all_items_saved_with_scores(self):
        result = self._collect(
            [{"title": f"消息{i}", "content": f"内容{i}", "source": "eastmoney"} for i in (1, 2, 3)],
            [{"index": i, "sentiment_score": 0.5, "sentiment_label": "positive"} for i in (1, 2, 3)],
        )
        self.assertEqual(result["news_count"], 3)
        saved = self.db.query(SentimentData).filter(SentimentData.data_date == DAY).all()
        self.assertEqual(len(saved), 3)
        self.assertTrue(all(s.sentiment_score == 0.5 for s in saved))
        self.assertTrue(all(s.sentiment_label == "positive" for s in saved))

    def test_dedup_by_title_still_works(self):
        self._collect([{"title": "重复标题", "content": "a"}],
                      [{"index": 1, "sentiment_score": 0.1, "sentiment_label": "neutral"}])
        result = self._collect([{"title": "重复标题", "content": "b"}],
                               [{"index": 1, "sentiment_score": 0.1, "sentiment_label": "neutral"}])
        saved = self.db.query(SentimentData).filter(SentimentData.data_date == DAY).all()
        self.assertEqual(len(saved), 1, "同日同标题不应重复入库")
        self.assertEqual(result["news_count"], 1)

    def test_blank_title_skipped(self):
        self._collect([{"title": "   ", "content": "x"}], [])
        self.assertEqual(self.db.query(SentimentData).count(), 0)

    def test_llm_absent_items_still_get_scores(self):
        """未配置 LLM 时采集仍须逐条给出关键词评分，不留 null"""
        self.svc.llm_client = None
        with patch.object(self.svc, "_fetch_financial_news",
                          return_value=[{"title": "板块跌停", "content": ""}]):
            self.svc.collect_daily_sentiment(DAY, self.db)
        saved = self.db.query(SentimentData).filter(SentimentData.data_date == DAY).all()
        self.assertEqual(len(saved), 1)
        self.assertIsNotNone(saved[0].sentiment_score)
        self.assertLess(saved[0].sentiment_score, 0)

    def test_related_etfs_persisted(self):
        self._collect([{"title": "半导体爆发", "content": ""}],
                      [{"index": 1, "sentiment_score": 0.9, "sentiment_label": "positive",
                        "related_etfs": ["512480"], "key_factors": ["国产替代"]}])
        row = self.db.query(SentimentData).first()
        self.assertEqual(row.related_etfs, ["512480"])
        self.assertEqual(row.key_factors, ["国产替代"])


if __name__ == "__main__":
    unittest.main()
