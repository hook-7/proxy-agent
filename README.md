# proxy-agent

将本地 **Agent CLI** 以 **OpenAI Chat Completions** 兼容的 HTTP API 暴露出来，便于用 OpenAI SDK、LiteLLM 等客户端调用。

## 安装

```bash
cd /path/to/proxy-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

或使用 [uv](https://github.com/astral-sh/uv)：

```bash
uv venv && source .venv/bin/activate && uv pip install -e .
```

开发依赖（测试）：

```bash
pip install -e ".[dev]"
# 或
uv pip install -e ".[dev]"
```

## 目录结构

```
proxy-agent/
├── pyproject.toml
├── README.md
├── src/proxy_agent/
│   ├── main.py           # create_app()、默认 app、CLI 入口
│   ├── api/              # HTTP 路由与依赖（鉴权、取 Settings）
│   ├── core/             # 配置（Settings / get_settings）
│   ├── schemas/          # OpenAI 兼容模型、messages→prompt（多轮）、SSE 片段
│   └── services/         # 子进程、SSE 保活注释行
└── tests/                # pytest
```

## 测试

```bash
pytest
```

## 运行

```bash
uvicorn proxy_agent.main:app --host 0.0.0.0 --port 8000
```

或：

```bash
proxy-agent
```

（等价于在默认 `0.0.0.0:8000` 上启动 uvicorn。）

## 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `AGENT_COMMAND` | 可执行文件名或路径 | `agent` |
| `AGENT_ARGS_STANDARD_TEMPLATE` | **非流式**参数模板（`stream: false` / 省略），`{prompt}` 为拼好的多轮/单轮文本；与 [Cursor Headless CLI](https://cursor.com/cn/docs/cli/headless) 一致时建议使用 `--print` + `--output-format text` | `-p --output-format text {prompt}` |
| `AGENT_ARGS_STREAM_TEMPLATE` | **流式**（`stream: true`）专用模板；默认与标准模板一致（**纯文本 stdout**）。使用 Cursor `--output-format stream-json` 时请改为含 `stream-json` / `--stream-partial-output` 的模板，并把 `AGENT_STREAM_PROTOCOL` 设为 `cursor_ndjson` | `-p --output-format text {prompt}` |
| `AGENT_STREAM_PROTOCOL` | **`passthrough`**：按块/行直接转发 stdout 为 `delta.content`（默认，兼容多数 CLI 与 OpenClaw）；**`cursor_ndjson`**：按行解析 Cursor `stream-json` NDJSON，抽取 `type: assistant` 文本增量 | `passthrough` |
| `AGENT_STANDARD_OUTPUT_FORMAT` | **非流式**下如何解读 stdout：**`text`** 原样作为回复；**`json`** 要求整段 stdout 为 JSON 且含字符串字段 **`result`**（对应 `--output-format json` + `jq -r .result` 的用法） | `text` |
| `AGENT_CWD` | 子进程工作目录（分析代码库时常设为项目根） | 未设置（继承服务进程 cwd） |
| `AGENT_TIMEOUT_SEC` | 子进程超时（秒） | `300` |
| `AGENT_STREAM_STDOUT_CHUNK_SIZE` | **`passthrough`** 时：流式从 stdout 每次读取的字节数；**`0` 表示按行**。**`cursor_ndjson`** 时强制按行读取，此变量无效 | `4096` |
| `AGENT_USE_STDBUF` | 为 `true`（默认）且系统 PATH 中有 GNU **`stdbuf`** 时，实际执行 `stdbuf -oL -eL <你的 agent ...>`，让多数基于 glibc stdio 的程序**按行刷管道**，减轻「整段跑完才出字」 | `true` |
| `AGENT_MESSAGES_FORMAT` | **`transcript`**：把整条 `messages` 转成 `System:/User:/Assistant:` 文本块再交给 CLI（**多轮连续对话**）；**`last_user_only`**：只取最后一条 user（旧行为） | `transcript` |
| `AGENT_MAX_PROMPT_CHARS` | `transcript` 模式下单段 prompt 最大字符数，超出返回 400；**`0`** 表示不限制 | `0` |
| `AGENT_SSE_COMMENT_INTERVAL_SEC` | 流式时在**等待子进程输出**的间隙按秒发送 SSE 注释行（`:` 开头），减轻网关空闲断连；**`0`** 关闭（默认，避免少数客户端解析异常）。经 Nginx 反代且长思考无输出时可设为 `15`–`30` | `0` |
| `AGENT_STREAM_EOF_PROCESS_WAIT_SEC` | **流式专用**：stdout 已读完（管道 EOF）后，最多再等子进程退出多少秒；超时则 **SIGKILL** 结束子进程。若此时**已经向客户端推过正文**，视为成功收尾并发 `[DONE]`，避免「字已出完但界面一直 streaming」。**`0`** 表示不单独限制，仅用总超时 `AGENT_TIMEOUT_SEC`（旧行为） | `30` |
| `API_KEY` | 若设置，则要求 `Authorization: Bearer <key>` | 未设置（不校验） |
| `DEFAULT_MODEL` | 未在请求中指定 `model` 时使用的模型名 | `auto` |

也可使用项目根目录下的 `.env`（`pydantic-settings` 会读取）。

## API

- `POST /v1/chat/completions`：请求体与 **OpenAI Chat Completions** 一致（`messages` 数组等）。`messages` 中每条可带 **字符串** 或 **多模态 `content` 数组**；非文本块（如图片）在拼进 transcript 时会被跳过。
  - **默认 `AGENT_MESSAGES_FORMAT=transcript`**：按时间顺序把 `system` / `user` / `assistant` / `tool` / `developer` 拼成一段带角色标签的纯文本（与多数 OpenAI 兼容本地代理的「整段 prompt」思路一致，行为说明见 [`messages_prompt.py`](src/proxy_agent/schemas/messages_prompt.py) 内注释与 [LiteLLM](https://github.com/BerriAI/litellm) 等生态的常见做法），再作为 `{prompt}` 交给 CLI，从而支持 **多轮上下文**。
  - **`last_user_only`**：仅把 **最后一条 user** 的文本交给 CLI（与早期版本相同）。
  - **非流式**（默认，`stream` 省略或为 `false`）：使用 **`AGENT_ARGS_STANDARD_TEMPLATE`** 启动子进程，结束后返回完整 `chat.completion` JSON；若 **`AGENT_STANDARD_OUTPUT_FORMAT=json`**，会把 stdout 解析为 JSON 并取 **`result`** 字符串作为助手回复。
  - **流式**（`"stream": true`）：使用 **`AGENT_ARGS_STREAM_TEMPLATE`** 启动子进程。默认 **`AGENT_STREAM_PROTOCOL=passthrough`**：把子进程 **stdout 原文**切成 SSE `delta.content`（按行或按块，见 `AGENT_STREAM_STDOUT_CHUNK_SIZE`），适合 **OpenClaw / pi-ai** 与普通文本型 CLI。若使用 Cursor **`--output-format stream-json`**，请设置 **`AGENT_STREAM_PROTOCOL=cursor_ndjson`** 并相应修改流式参数模板；此时 **非 JSON 行**仍会按纯文本转发，减轻客户端 **`terminated`**。
  - **流式 + CLI 非 0 退出**：HTTP 仍为 **200**（流已开始），会在正文末尾多推一段 `delta.content`，内含 `[agent exited with code N]` 与 stderr 截断；随后正常 `finish_reason: stop` 与 `[DONE]`。
  - **流式 + 超时**：会再推一条含超时说明的 `delta.content`，然后结束流。
- `GET /v1/models`：返回配置的 `DEFAULT_MODEL` 条目，供部分客户端探测。

## 示例：`curl`

```bash
export AGENT_COMMAND=echo
export AGENT_ARGS_STANDARD_TEMPLATE='{prompt}'
export AGENT_ARGS_STREAM_TEMPLATE='{prompt}'
export AGENT_STREAM_PROTOCOL=passthrough

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "proxy-agent",
    "messages": [{"role": "user", "content": "hello"}]
  }'
```

若设置了 `API_KEY`：

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"messages":[{"role":"user","content":"hello"}]}'
```

流式（需禁用 curl 缓冲，例如 `-N`）：

```bash
curl -N -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello"}],"stream":true}'
```

## 示例：OpenAI Python SDK

将 `base_url` 指向本服务，并设置与 `API_KEY` 一致的密钥（若启用校验）：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="your-secret",  # 与服务端 API_KEY 一致；未设置 API_KEY 时可填任意占位字符串
)

r = client.chat.completions.create(
    model="proxy-agent",
    messages=[{"role": "user", "content": "What does this codebase do?"}],
)
print(r.choices[0].message.content)

stream = client.chat.completions.create(
    model="proxy-agent",
    messages=[{"role": "user", "content": "ping"}],
    stream=True,
)
for ev in stream:
    if ev.choices[0].delta.content:
        print(ev.choices[0].delta.content, end="")
```

## 错误与退出码

- **非流式**：CLI 非零退出或超时 → **502**，JSON 中含 OpenAI 风格的 `error`（含 `exit_code`、`stderr` 等，若有）。
- **流式**：见上文「流式 + CLI 非 0 退出 / 超时」。

## OpenClaw / 客户端一直显示「streaming」收不到结束？

常见原因不是缺少 `[DONE]`，而是 **子进程在你看到完整回复之后仍然没有退出**（例如在等输入、驻留后台、或 TUI 未关）。此时服务端 **stdout 管道不会 EOF**，流式读会一直挂起，**不会发 `finish_reason` + `[DONE]`**，界面就会一直转圈。

**处理建议**：让 `agent` 在答完一轮后 **正常退出**（非交互 / headless）；本服务已对子进程使用 `stdin=DEVNULL`。若仍不退出，需在 agent 侧加「单轮 / CI」类参数，或换用会退出的包装脚本。

已做的协议侧兼容：最终 chunk 带 **`"usage": null`**（与 OpenAI 流式常见收尾形状一致）、`Content-Type` 带 **`charset=utf-8`**；默认 **关闭** SSE 注释保活（`AGENT_SSE_COMMENT_INTERVAL_SEC=0`），避免个别解析器对 `:` 注释行处理异常。

另外默认启用 **`AGENT_STREAM_EOF_PROCESS_WAIT_SEC=30`**：很多 CLI **打完字不关进程**，stdout 已 EOF 但 `wait()` 一直挂，服务端发不出 `[DONE]`，客户端就会一直显示 streaming；该限制会在「已有输出」时杀进程并正常结束流。

流式单次读 stdout 若长时间无数据，会一直等到 **`AGENT_TIMEOUT_SEC`** 总超时为止；需要更快失败可把 **`AGENT_TIMEOUT_SEC`** 调小（例如 `60`）。

子进程使用 **新会话 + 进程组**，结束流时会 **`killpg`**，避免 `stdbuf`/`bash -c` 只杀掉父进程、孙进程仍占着管道导致 **stderr 排空永远挂死**、客户端收不到 `[DONE]`。stdout 结束后 stderr 排空另有 **上限等待**（约 12s），超时则放弃排空并照常收尾。

## 现象说明：为什么 Ctrl+C 后访问日志才出现？

Uvicorn 对一次请求打的 **`POST ... 200`** 往往在**响应完全结束**（非流式要等子进程结束；流式要等 SSE 发完）后才写日志。请求一直挂着时，你可能看不到这条日志；**按 Ctrl+C 关掉服务**会断开连接，此时才把该请求记成结束，所以看起来像「退出程序才返回」。

若要**边生成边收**：请客户端使用 **`"stream": true`**，并用支持 SSE 的方式读（例如 `curl -N`、OpenAI SDK `stream=True`）。若仍几乎无增量输出，多半是子进程 **stdout 全缓冲**；可保持默认 **`AGENT_USE_STDBUF`**（依赖 GNU `stdbuf`），或让 agent 自身支持行缓冲 / 无缓冲。Go、Rust 等自管缓冲的二进制 **`stdbuf` 可能无效**，需在 agent 侧加 flush 或包装脚本。

## 安全说明

提示词通过 **`asyncio.create_subprocess_exec` 的参数列表** 传给子进程，**不使用 shell**，避免注入。请仍将本服务部署在受信网络或配合 TLS、防火墙与 `API_KEY` 使用。
