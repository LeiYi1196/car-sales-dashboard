"""Tests for POST /chat — all offline, no real Anthropic key required.

Strategy
--------
- The Anthropic client is monkeypatched with a fake that returns pre-built
  response objects, so the full tool-use dispatch loop runs against real data.
- We seed the temp DB with a small fixture DataFrame so tool results are
  deterministic.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient


# ── Fixture: minimal sales data ───────────────────────────────────────────

SAMPLE_DF = pd.DataFrame(
    {
        "date": pd.to_datetime(["2019-01-01", "2019-02-01", "2019-01-01", "2019-03-01"]),
        "country": ["China", "China", "Germany", "Germany"],
        "display_name": ["China", "China", "Germany", "Germany"],
        "currency": ["CNY", "CNY", "EUR", "EUR"],
        "sales": [1000.0, 1200.0, 500.0, 600.0],
        "quantity": [10, 12, 5, 6],
        "model": ["Model X", "Model X", "Model Y", "Model Y"],
        "source_file": ["test.csv"] * 4,
    }
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """App with temp DB + ANTHROPIC_API_KEY stub (real Claude never called)."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-stub")

    # 重置模块缓存，让 DB 路径生效
    import src.db as db_mod
    db_mod._engine = None
    db_mod._SessionLocal = None

    from src import app as app_mod
    importlib.reload(app_mod)

    # 把样本数据预填进 DB（session_scope 是 generator，直接用 get_session）
    session = db_mod.get_session()
    try:
        db_mod.insert_batch(session, SAMPLE_DF, "fixture.csv")
        session.commit()
    finally:
        session.close()

    return TestClient(app_mod.app)


# ── Helpers: 构造假的 Anthropic response ──────────────────────────────────

def _text_response(text: str):
    """模拟 stop_reason='end_turn' 的纯文本回复。"""
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(stop_reason="end_turn", content=[block])


def _tool_then_text(tool_name: str, tool_input: dict, tool_id: str, final_text: str):
    """模拟两轮：先 tool_use，再 end_turn 纯文本。"""
    tool_block = SimpleNamespace(
        type="tool_use",
        id=tool_id,
        name=tool_name,
        input=tool_input,
    )
    first = SimpleNamespace(stop_reason="tool_use", content=[tool_block])
    second = _text_response(final_text)
    return [first, second]


# ── Tests ────────────────────────────────────────────────────────────────

def test_empty_question(client):
    """空问题应该返回 400。"""
    resp = client.post("/chat", json={"question": "   "})
    assert resp.status_code == 400


def test_missing_api_key(tmp_path, monkeypatch):
    """未设置 ANTHROPIC_API_KEY 时应返回 503。"""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import src.db as db_mod
    db_mod._engine = None
    db_mod._SessionLocal = None
    from src import app as app_mod
    importlib.reload(app_mod)

    no_key_client = TestClient(app_mod.app)
    resp = no_key_client.post("/chat", json={"question": "What is the total sales?"})
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_direct_text_answer(client):
    """模型直接回答（无工具调用）。"""
    fake_response = _text_response("Total sales across all countries is 3,300.")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.return_value = fake_response

        resp = client.post("/chat", json={"question": "What is the total sales?"})

    assert resp.status_code == 200
    assert "3,300" in resp.json()["answer"]


def test_tool_use_get_global_summary(client):
    """模型调用 get_global_summary 工具，获取真实数据后给出回答。"""
    tool_id = "toolu_01"
    responses = _tool_then_text(
        "get_global_summary", {}, tool_id,
        "Global total sales: 3,300. Top country: China with 2,200."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        # 两次调用依次返回 tool_use → end_turn
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "Which country has the most sales?"})

    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert len(data["answer"]) > 0

    # 验证工具确实被调用了（第二次 create 调用的 messages 包含 tool_result）
    second_call_messages = mock_instance.messages.create.call_args_list[1].kwargs["messages"]
    tool_result_msg = second_call_messages[-1]
    assert tool_result_msg["role"] == "user"
    # tool_result 里应该有真实数据（China 的销量来自 fixture DB）
    result_content = json.loads(tool_result_msg["content"][0]["content"])
    countries = [r["country"] for r in result_content["by_country"]]
    assert "China" in countries
    assert "Germany" in countries


def test_tool_use_get_country_detail(client):
    """模型调用 get_country_detail，应能返回 China 的数据。"""
    tool_id = "toolu_02"
    responses = _tool_then_text(
        "get_country_detail", {"country": "China", "granularity": "M"}, tool_id,
        "China had total sales of 2,200 with peak in February."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "How did China perform month by month?"})

    assert resp.status_code == 200
    second_call_messages = mock_instance.messages.create.call_args_list[1].kwargs["messages"]
    result_content = json.loads(second_call_messages[-1]["content"][0]["content"])
    # 真实数据：China 总销量应为 2200
    assert result_content["total_sales"] == 2200


def test_tool_use_compare_countries(client):
    """模型调用 compare_countries，结果应包含两个国家及其份额。"""
    tool_id = "toolu_03"
    responses = _tool_then_text(
        "compare_countries", {"countries": ["China", "Germany"]}, tool_id,
        "China leads with 66.7% share, Germany has 33.3%."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "Compare China and Germany sales"})

    assert resp.status_code == 200
    second_call_messages = mock_instance.messages.create.call_args_list[1].kwargs["messages"]
    result = json.loads(second_call_messages[-1]["content"][0]["content"])
    country_names = [r["country"] for r in result]
    assert "China" in country_names
    assert "Germany" in country_names
    china_row = next(r for r in result if r["country"] == "China")
    # China share should be ~66.7%
    assert abs(china_row["share_pct"] - 66.7) < 0.5


def test_tool_use_get_top_models(client):
    """模型调用 get_top_models，应返回样本数据里的两个车型。"""
    tool_id = "toolu_04"
    responses = _tool_then_text(
        "get_top_models", {"top_n": 5}, tool_id,
        "Top model is Model X with 2,200 in sales."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "What are the top selling models?"})

    assert resp.status_code == 200
    second_call_messages = mock_instance.messages.create.call_args_list[1].kwargs["messages"]
    result = json.loads(second_call_messages[-1]["content"][0]["content"])
    model_names = [r["model"] for r in result]
    assert "Model X" in model_names


def test_case_insensitive_country(client):
    """国家名大小写不敏感匹配（china → China）。"""
    tool_id = "toolu_05"
    responses = _tool_then_text(
        "get_country_detail", {"country": "china"}, tool_id,
        "China details: total 2200."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "tell me about china"})

    assert resp.status_code == 200
    second_call_messages = mock_instance.messages.create.call_args_list[1].kwargs["messages"]
    result = json.loads(second_call_messages[-1]["content"][0]["content"])
    # 应该匹配到真实数据而非返回空
    assert result["total_sales"] == 2200


# ── Faithfulness helpers ──────────────────────────────────────────────────
# Faithfulness = 最终回答中声称的事实必须与工具实际返回的数据一致，不能凭空捏造。
# 每个断言分两步：① 从第二次 create 调用拿到真实工具结果；② 验证回答文本中的关键
# 数字/实体与该结果匹配。

def _get_tool_result(mock_instance, call_index: int = 1) -> dict | list:
    """从第 call_index 次 create 调用中提取工具结果 JSON。"""
    call_messages = mock_instance.messages.create.call_args_list[call_index].kwargs["messages"]
    return json.loads(call_messages[-1]["content"][0]["content"])


# ── Faithfulness tests ────────────────────────────────────────────────────

def test_faithfulness_global_total(client):
    """回答声称的全球总销量必须与工具返回的 total_sales 一致。"""
    tool_id = "toolu_f01"
    # 样本数据：China 2200 + Germany 1100 = 3300
    responses = _tool_then_text(
        "get_global_summary", {}, tool_id,
        "Global total sales are 3,300."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "What is the total global sales?"})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    result = _get_tool_result(mock_instance)

    # 工具结果必须支持回答中的数字
    assert result["total_sales"] == 3300
    assert "3,300" in answer


def test_faithfulness_country_sales_consistent(client):
    """China 销量回答与工具结果中的 total_sales 必须一致。"""
    tool_id = "toolu_f02"
    responses = _tool_then_text(
        "get_country_detail", {"country": "China"}, tool_id,
        "China's total sales amount to 2,200."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "What are China's total sales?"})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    result = _get_tool_result(mock_instance)

    # 工具实际返回 2200；回答必须引用这个数字
    assert result["total_sales"] == 2200
    assert "2,200" in answer


def test_faithfulness_share_pct_consistent(client):
    """回答中各国份额百分比必须与工具计算结果在 0.5% 误差内。"""
    tool_id = "toolu_f03"
    # China 2200/3300 ≈ 66.7%，Germany 1100/3300 ≈ 33.3%
    responses = _tool_then_text(
        "compare_countries", {"countries": ["China", "Germany"]}, tool_id,
        "China holds 66.7% of total sales, Germany holds 33.3%."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "What is each country's market share?"})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    result = _get_tool_result(mock_instance)

    china_row = next(r for r in result if r["country"] == "China")
    germany_row = next(r for r in result if r["country"] == "Germany")

    # 工具结果的份额计算必须正确
    assert abs(china_row["share_pct"] - 66.7) < 0.5
    assert abs(germany_row["share_pct"] - 33.3) < 0.5

    # 回答中的百分比必须与工具结果一致
    assert "66.7" in answer
    assert "33.3" in answer


def test_faithfulness_no_hallucinated_countries(client):
    """回答不得提及工具结果中不存在的国家（幻觉检测）。"""
    tool_id = "toolu_f04"
    # 样本只有 China 和 Germany；回答正确地只提到这两个
    responses = _tool_then_text(
        "get_global_summary", {}, tool_id,
        "The two markets are China and Germany; no other countries have data."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "Which countries are in the dataset?"})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    result = _get_tool_result(mock_instance)

    # 工具结果里只有这两个国家
    known_countries = {r["country"] for r in result["by_country"]}
    assert known_countries == {"China", "Germany"}

    # 回答不应出现数据中没有的国家
    hallucinated = ["France", "Japan", "USA", "UK", "Korea", "India"]
    for country in hallucinated:
        assert country not in answer, f"Answer hallucinated country not in tool result: {country}"


def test_faithfulness_top_model_name(client):
    """回答中最畅销车型的名称和销量必须与工具结果第一条记录一致。"""
    tool_id = "toolu_f05"
    # Model X 销量 2200 > Model Y 1100，应排第一
    responses = _tool_then_text(
        "get_top_models", {"top_n": 3}, tool_id,
        "The best-selling model is Model X with 2,200 in sales."
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = responses

        resp = client.post("/chat", json={"question": "What is the top selling car model?"})

    assert resp.status_code == 200
    answer = resp.json()["answer"]
    result = _get_tool_result(mock_instance)

    # 工具返回的第一名必须是 Model X，销量 2200
    top_model = result[0]["model"]
    top_sales = result[0]["sales"]
    assert top_model == "Model X"
    assert top_sales == 2200

    # 回答中的车型名和销量必须与工具结果一致
    assert top_model in answer
    assert "2,200" in answer
