# Update Log

## 2026-06-13

### tests/test_chat.py — 新增 Faithfulness 断言

新增 5 个 faithfulness 测试，验证最终回答中的事实必须与工具实际返回的数据一致：

- `test_faithfulness_global_total` — 全球总销量（3300）与工具结果 `total_sales` 吻合
- `test_faithfulness_country_sales_consistent` — China 销量（2200）与工具结果一致
- `test_faithfulness_share_pct_consistent` — 各国份额百分比（China 66.7%，Germany 33.3%）在 0.5% 误差内
- `test_faithfulness_no_hallucinated_countries` — 回答不得提及工具结果中不存在的国家（幻觉检测）
- `test_faithfulness_top_model_name` — 最畅销车型名称和销量与工具第一条记录一致

同步新增 `_get_tool_result` 辅助函数，从第二次 LLM create 调用中提取工具结果 JSON。

测试总数：8 → 13，全部通过。
