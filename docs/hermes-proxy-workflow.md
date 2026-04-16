# Hermes + proxy-agent 协作工作流

这份文档定义一个简单、稳定的协作模式：

- **Hermes** 负责统筹任务、拆解问题、读写文件、跑测试、做最终判断
- **proxy-agent** 负责把请求转发给 **Cursor CLI `agent`**
- **Cursor agent** 适合承担外部辅助推理、代码草稿、替代视角、流式长回答

目标不是让 proxy-agent 取代 Hermes，而是让它成为一个**可随时调用的外部协作 agent**。

---

## 1. 角色分工

### Hermes 适合做的事

- 读取和修改本地文件
- 精确执行 shell / 测试 / git 操作
- 做多步任务编排
- 验证 Cursor agent 的回答是否正确
- 将结果落地到代码、文档和系统状态

### proxy-agent / Cursor agent 适合做的事

- 快速给出第二意见
- 总结代码、日志、错误信息
- 生成候选实现方案
- 流式输出较长回答
- 在 OpenAI 兼容接口场景里被其他脚本/工具消费

---

## 2. 推荐工作模式

### 模式 A：Hermes 主导，proxy-agent 做“外脑”

适合：调试、架构讨论、代码评审、方案比较。

流程：
1. Hermes 先读仓库 / 读日志 / 跑测试
2. Hermes 把压缩后的上下文发给 proxy-agent
3. Cursor agent 给出建议、草稿、替代解释
4. Hermes 验证建议是否靠谱
5. Hermes 决定是否真正改代码 / 执行命令

这也是默认推荐模式。

---

### 模式 B：Hermes 先做，proxy-agent 做复核

适合：
- 改完代码后再要一轮审查
- 写完文档后再要一轮润色
- 修完 bug 后再要一轮根因复盘

流程：
1. Hermes 先完成实现
2. 将 diff / 文件片段 / 测试结果发给 proxy-agent
3. 让 Cursor agent 做 review
4. Hermes 根据 review 决定是否继续修订

---

### 模式 C：proxy-agent 先出草稿，Hermes 落地

适合：
- 需求拆解
- prompt 草案
- 文档初稿
- 小型代码脚手架

流程：
1. Hermes 给 proxy-agent 一个清晰目标
2. Cursor agent 输出草稿
3. Hermes 把草稿转成真实文件、命令和验证步骤

注意：
- 不要直接信任草稿
- 必须由 Hermes 验证后再落地

---

## 3. 调用方式

### 3.1 直接用 curl

```bash
curl -sS -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [
      {"role":"system","content":"You are a coding copilot helping Hermes."},
      {"role":"user","content":"Summarize the likely root cause of this error..."}
    ],
    "stream": true
  }'
```

### 3.2 用仓库内辅助脚本

项目已经提供：

```bash
python3 scripts/hermes_proxy_chat.py "Summarize this repository"
```

带 system prompt：

```bash
python3 scripts/hermes_proxy_chat.py \
  --system "You are Cursor agent assisting Hermes with code review." \
  "Review the API error handling design."
```

走 stdin：

```bash
cat /tmp/context.txt | python3 scripts/hermes_proxy_chat.py --stdin
```

非流式：

```bash
python3 scripts/hermes_proxy_chat.py --no-stream "Reply with exactly: ok"
```

指定另一实例：

```bash
python3 scripts/hermes_proxy_chat.py --base-url http://127.0.0.1:8088 "Hello"
```

可用环境变量：

- `PROXY_AGENT_URL`
- `PROXY_AGENT_API_KEY`
- `PROXY_AGENT_MODEL`

例如：

```bash
export PROXY_AGENT_URL=http://127.0.0.1:8000
export PROXY_AGENT_MODEL=auto
python3 scripts/hermes_proxy_chat.py "Review this stack trace"
```

---

## 4. 给 proxy-agent 的高质量输入模板

为了减少幻觉，Hermes 发给 proxy-agent 的内容应尽量压缩成：

1. **任务目标**：要它做什么
2. **约束**：不能做什么 / 风格要求
3. **上下文**：必要代码、日志、报错、diff
4. **输出格式**：要 bullet list、patch 建议、还是根因分析

建议模板：

```text
You are assisting Hermes.

Task:
- Find the most likely root cause of the failure.

Constraints:
- Do not assume files that are not shown.
- Prefer concrete observations over generic advice.
- If uncertain, say what you would verify next.

Context:
- Command: pytest tests/test_api.py -q
- Error:
  ...
- Relevant code:
  ...

Output:
- Root cause
- Evidence
- Minimal fix
- Risks / unknowns
```

---

## 5. 推荐场景

### 调试协作

- Hermes：收集失败命令、堆栈、关键代码
- proxy-agent：归纳可能根因与候选修复
- Hermes：验证并实施修复

### 代码评审协作

- Hermes：拿到 diff
- proxy-agent：从可读性、边界条件、异常处理角度 review
- Hermes：确认问题是否成立

### 文档协作

- Hermes：给出真实代码背景
- proxy-agent：先生成初稿/摘要
- Hermes：补全事实和项目约束

### 方案比较

- Hermes：提出 2~3 个方案和约束
- proxy-agent：比较 trade-off
- Hermes：结合仓库现状最终拍板

---

## 6. 不推荐场景

- 需要直接改本地文件但未给 Hermes 落地步骤
- 需要高精度系统状态判断但没有先采集事实
- 需要执行破坏性命令时让 proxy-agent 单独“拍板”
- 把整个超大仓库原样塞给 proxy-agent，不做上下文压缩

---

## 7. 最小协作例子

### 例 1：让 Cursor agent 帮 Hermes 看报错

```bash
python3 scripts/hermes_proxy_chat.py --system \
  "You are a debugging assistant helping Hermes. Prefer concrete root causes." \
  "Command failed: pytest tests/test_api.py -q\n\nError:\nAssertionError: expected 200 got 500\n\nRelevant code:\n@app.post('/v1/chat/completions') ..."
```

### 例 2：让 Cursor agent 帮 Hermes 做 review

```bash
git diff -- src/proxy_agent/app.py | python3 scripts/hermes_proxy_chat.py --stdin --system \
  "You are reviewing a Python FastAPI diff for Hermes. Return only concrete issues and risks."
```

---

## 8. 操作约定

推荐默认约定：

- **Hermes 是 controller**
- **proxy-agent 是 advisor / collaborator**
- 所有真正的文件修改、命令执行、测试验证，最终由 Hermes 决定并落地
- 如果 proxy-agent 结论与本地事实冲突，以 Hermes 现场验证结果为准

---

## 9. 快速检查清单

在一次协作前，先确认：

- [ ] proxy-agent 服务可访问
- [ ] 选择正确实例（如 `8000` 或 `8088`）
- [ ] 给出的上下文足够且不过载
- [ ] 输出格式要求明确
- [ ] Hermes 会做最终验证

可用健康检查：

```bash
curl -sS http://127.0.0.1:8000/v1/models
python3 scripts/hermes_proxy_chat.py --no-stream "Reply with exactly: ok"
```
