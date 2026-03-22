# proxy-agent

将本地 **Agent CLI**（如 Cursor `agent -p`）以 **OpenAI Chat Completions** 兼容 HTTP 暴露：`GET /v1/models`、`POST /v1/chat/completions`（支持 `stream: true` SSE）。

## 实现结构

全部逻辑在 **单文件** [`src/proxy_agent/app.py`](src/proxy_agent/app.py)。[`__init__.py`](src/proxy_agent/__init__.py) 仅导出 `app`、`create_app`、`run`。

说明：包内把 FastAPI 实例命名为 `app` 并再导出后，表达式 `proxy_agent.app` 在已 `import proxy_agent` 时指向的是 **应用对象** 而非 `app.py` 模块。若要在代码里引用实现模块本身，请使用 `importlib.import_module("proxy_agent.app")`。

## 安装与运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn proxy_agent.app:app --host 0.0.0.0 --port 8000
# 或
proxy-agent
```

## 环境变量

与此前一致：`AGENT_COMMAND`、`AGENT_ARGS_*_TEMPLATE`、`AGENT_STREAM_PROTOCOL`、`AGENT_TIMEOUT_SEC`、`AGENT_SUBPROCESS_STREAM_LIMIT`、`AGENT_SSE_COMMENT_INTERVAL_SEC`、`AGENT_STREAM_CHECK_CLIENT_DISCONNECT`（默认 `false`）、`API_KEY`、`DEFAULT_MODEL` 等。详见 `app.py` 内 `Settings` 字段注释。

## 测试

```bash
pytest
```
