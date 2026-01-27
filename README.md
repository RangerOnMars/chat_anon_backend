# ChatAnon Backend Server

基于 WebSocket 的角色扮演聊天后端服务器，支持 API Token 认证和角色切换。

## 功能特性

- **WebSocket 实时通信**: 基于 FastAPI 的异步 WebSocket 服务器
- **API Token 认证**: 每个 Token 同一时间只能建立一条连接
- **角色切换**: 支持切换不同的扮演角色（目前支持 anon）
- **LLM 对话**: 集成豆包 API 进行角色扮演对话
- **TTS 语音合成**: 集成 MiniMax TTS 生成角色语音
- **密钥管理**: 所有敏感信息集中在 `credentials.py` 管理

## 项目结构

```
chat_anon_backend/
├── server/                    # 服务器核心代码
│   ├── main.py               # FastAPI 入口
│   ├── config.py             # 配置加载
│   ├── auth.py               # Token 认证
│   ├── connection_manager.py # 连接管理
│   └── character_manager.py  # 角色管理
├── services/                  # 服务层
│   ├── base.py               # 基础抽象类
│   ├── llm_service.py        # LLM 服务
│   ├── tts_service.py        # TTS 服务
│   └── asr_service.py        # ASR 服务
├── prompts/                   # 角色定义
│   └── anon/
│       └── character_manifest.md
├── client/                    # 测试客户端
│   └── test_client.py
├── secrets.py                 # 密钥配置 (不提交到 git)
├── secrets.example.py         # 密钥模板
├── config.yaml               # 服务配置
├── requirements.txt
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置密钥

复制密钥模板并填入实际凭据：

```bash
cp credentials.example.py credentials.py
```

编辑 `credentials.py` 填入你的 API 密钥：

```python
BYTEDANCE_APP_KEY = "your_app_key"
BYTEDANCE_ACCESS_KEY = "your_access_key"
DOUBAO_API_KEY = "your_llm_api_key"
MINIMAX_API_KEY = "your_minimax_key"

VALID_API_TOKENS = [
    "your_token_1",
    "your_token_2",
]
```

### 3. 启动服务器

```bash
python -m server.main
```

或者使用 uvicorn：

```bash
uvicorn server.main:app --host 0.0.0.0 --port 8765
```

### 4. 运行测试客户端

```bash
python client/test_client.py --token dev_token_001
```

## WebSocket API 协议

### 连接流程

1. **客户端连接** `ws://localhost:8765/ws`

2. **发送认证消息**:
```json
{
  "type": "connect",
  "api_token": "your_token",
  "character_name": "anon"
}
```

3. **服务器响应**:
```json
{
  "type": "connected",
  "character": "anon",
  "character_display_name": "千早爱音 (Chihaya Anon)",
  "message": "Connection established successfully"
}
```

### 消息类型

#### 发送消息
```json
{
  "type": "message",
  "content": "你好，爱音！"
}
```

#### 响应
```json
{
  "type": "response",
  "content_cn": "哈喽～",
  "content_jp": "ハロー～",
  "emotion": "happy",
  "audio_base64": "...",
  "audio_format": "pcm",
  "audio_sample_rate": 16000
}
```

#### 切换角色
```json
{
  "type": "switch_character",
  "character_name": "anon"
}
```

#### 清除历史
```json
{
  "type": "clear_history"
}
```

### 错误响应
```json
{
  "type": "error",
  "message": "Error description"
}
```

## HTTP API

- `GET /` - 服务器信息
- `GET /health` - 健康检查
- `GET /characters` - 可用角色列表

## 配置说明

### config.yaml

```yaml
server:
  host: "0.0.0.0"
  port: 8765

models:
  llm_model: "doubao-seed-1-8-251228"
  tts_model: "speech-2.8-hd"

llm:
  stream: false
  reasoning_effort: "low"
```

## 测试客户端命令

```
/quit, /exit  - 退出客户端
/switch <name> - 切换角色
/clear        - 清除对话历史
/save         - 开启/关闭音频保存
/help         - 显示帮助
```

## 注意事项

1. **密钥安全**: `credentials.py` 已添加到 `.gitignore`，请勿提交到版本控制
2. **单连接限制**: 同一 Token 只能有一个活跃连接，新连接会断开旧连接
3. **角色支持**: 目前仅支持 "anon" 角色，可在 `character_manager.py` 中添加更多

## License

MIT
