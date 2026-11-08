# 示例配置

`grid.json` 是 `ma_cross` 策略的参数网格示例。

```bash
GRID=$(python -c 'import json;print(json.dumps(json.load(open("examples/grid.json"))))')
chronostock optimize AAPL --strategy ma_cross --grid "$GRID" --top 5

# 同一网格做滚动样本外验证
chronostock optimize AAPL --strategy ma_cross --grid "$GRID" \
  --walk-forward --train-bars 500 --test-bars 120

# 查看所有策略及其内置参数空间
chronostock strategies --json
```

自定义参数也可以直接内联：

```bash
chronostock backtest AAPL --strategy rsi_reversion --params '{"lower":25,"upper":60}'
chronostock backtest 600519.SH --strategy dual_momentum --as-of 2024-06-28 --stop-loss 0.08
```

> 网格搜索是样本内结果，务必结合 walk-forward 的样本外净值与过拟合比一起判断。
