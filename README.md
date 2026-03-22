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
│   ├── schemas/          # OpenAI 兼容请求/响应模型与工具函数
│   └── services/         # 子进程调用 agent CLI
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
| `AGENT_ARGS_TEMPLATE` | 参数模板，用 `{prompt}` 插入用户文本（按参数列表传递，不经过 shell） | `-p {prompt}` |
| `AGENT_CWD` | 子进程工作目录（分析代码库时常设为项目根） | 未设置（继承服务进程 cwd） |
| `AGENT_TIMEOUT_SEC` | 子进程超时（秒） | `300` |
| `AGENT_STREAM_STDOUT_CHUNK_SIZE` | 流式（`stream: true`）时从子进程 stdout 每次读取的字节数；**`0` 表示按行**（`readline`，无换行时会一直等到进程结束） | `4096` |
| `AGENT_USE_STDBUF` | 为 `true`（默认）且系统 PATH 中有 GNU **`stdbuf`** 时，实际执行 `stdbuf -oL -eL <你的 agent ...>`，让多数基于 glibc stdio 的程序**按行刷管道**，减轻「整段跑完才出字」 | `true` |
| `API_KEY` | 若设置，则要求 `Authorization: Bearer <key>` | 未设置（不校验） |
| `DEFAULT_MODEL` | 未在请求中指定 `model` 时使用的模型名 | `proxy-agent` |

也可使用项目根目录下的 `.env`（`pydantic-settings` 会读取）。

## API

- `POST /v1/chat/completions`：请求体与 OpenAI 类似，需包含 `messages`。会从 **最后一条 `role` 为 `user` 且 `content` 为字符串** 的消息中取出提示词，交给 CLI。
  - **非流式**（默认，`stream` 省略或为 `false`）：等子进程结束后返回完整 `chat.completion` JSON。
  - **流式**（`"stream": true`）：响应为 **`text/event-stream`**（SSE），多条 `data: { ... "object":"chat.completion.chunk" ... }`，最后为 `data: [DONE]`。默认按 **`AGENT_STREAM_STDOUT_CHUNK_SIZE`（如 4096 字节）** 从 stdout 增量读取并解码为 UTF-8，不必等换行，适合 **全缓冲、整段打印** 的 CLI；设为 **`0`** 时改为按行（`readline`），无换行则会像阻塞一样直到进程结束。
  - **流式 + CLI 非 0 退出**：HTTP 仍为 **200**（流已开始），会在正文末尾多推一段 `delta.content`，内含 `[agent exited with code N]` 与 stderr 截断；随后正常 `finish_reason: stop` 与 `[DONE]`。
  - **流式 + 超时**：会再推一条含超时说明的 `delta.content`，然后结束流。
- `GET /v1/models`：返回配置的 `DEFAULT_MODEL` 条目，供部分客户端探测。

## 示例：`curl`

```bash
export AGENT_COMMAND=echo
export AGENT_ARGS_TEMPLATE='{prompt}'

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

## 现象说明：为什么 Ctrl+C 后访问日志才出现？

Uvicorn 对一次请求打的 **`POST ... 200`** 往往在**响应完全结束**（非流式要等子进程结束；流式要等 SSE 发完）后才写日志。请求一直挂着时，你可能看不到这条日志；**按 Ctrl+C 关掉服务**会断开连接，此时才把该请求记成结束，所以看起来像「退出程序才返回」。

若要**边生成边收**：请客户端使用 **`"stream": true`**，并用支持 SSE 的方式读（例如 `curl -N`、OpenAI SDK `stream=True`）。若仍几乎无增量输出，多半是子进程 **stdout 全缓冲**；可保持默认 **`AGENT_USE_STDBUF`**（依赖 GNU `stdbuf`），或让 agent 自身支持行缓冲 / 无缓冲。Go、Rust 等自管缓冲的二进制 **`stdbuf` 可能无效**，需在 agent 侧加 flush 或包装脚本。

## 安全说明

提示词通过 **`asyncio.create_subprocess_exec` 的参数列表** 传给子进程，**不使用 shell**，避免注入。请仍将本服务部署在受信网络或配合 TLS、防火墙与 `API_KEY` 使用。
