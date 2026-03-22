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
| `API_KEY` | 若设置，则要求 `Authorization: Bearer <key>` | 未设置（不校验） |
| `DEFAULT_MODEL` | 未在请求中指定 `model` 时使用的模型名 | `proxy-agent` |

也可使用项目根目录下的 `.env`（`pydantic-settings` 会读取）。

## API

- `POST /v1/chat/completions`：请求体与 OpenAI 类似，需包含 `messages`。会从 **最后一条 `role` 为 `user` 且 `content` 为字符串** 的消息中取出提示词，交给 CLI。不支持 `stream: true`（会返回 400）。
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
```

## 错误与退出码

CLI 非零退出时，接口返回 **502**，JSON 中含 OpenAI 风格的 `error` 对象，并附带 `exit_code`、`stderr`（若有）便于排查。

## 安全说明

提示词通过 **`asyncio.create_subprocess_exec` 的参数列表** 传给子进程，**不使用 shell**，避免注入。请仍将本服务部署在受信网络或配合 TLS、防火墙与 `API_KEY` 使用。
