# PydanticAI provider streaming API 验证

## 结果

当前工作区的 `.venv` 没有安装 `pydantic_ai`。项目锁定版本是
`pydantic-ai-slim[openai]==2.15.0`，但本次实现按要求不安装依赖、不访问网络；因此本地
无法运行真实 `Agent.run_stream()` 验证。

## 实现约束

代码使用 PydanticAI 2.15 的标准异步流式接口形态：

```python
async with agent.run_stream(...) as result:
    async for delta in result.stream_text(delta=True):
        ...
```

真实 provider 回归需要在具备锁定依赖的开发环境中执行。当前提交先通过 provider-agnostic
的 `AnswerStreamPlan`、受控内部事件和 fake stream seam 覆盖事件顺序、Citation allow-list
和 unsupported 固定文本；如果目标 provider 不支持该接口，运行时必须回退到已有 one-delta
路径。
