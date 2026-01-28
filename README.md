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
│   ├── character_manager.py  # 角色管理
│   └── message_handlers.py   # 消息处理器
├── services/                  # 服务层
│   ├── base.py               # 基础抽象类和异常
│   ├── llm_service.py        # LLM 服务 (Doubao)
│   ├── tts_service.py        # TTS 服务 (MiniMax)
│   └── asr_service.py        # ASR 服务 (ByteDance)
├── prompts/                   # 角色定义
│   └── anon/
│       └── character_manifest.md
├── client/                    # 测试客户端
│   ├── test_client.py        # CLI 测试客户端
│   └── audio_manager.py      # 音频 I/O 管理
├── credentials.py             # 密钥配置 (不提交到 git)
├── credentials.example.py     # 密钥模板
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

#### 方法 A: 环境变量（推荐用于生产环境）

设置以下环境变量：

```bash
export BYTEDANCE_APP_KEY="your_app_key"
export BYTEDANCE_ACCESS_KEY="your_access_key"
export DOUBAO_API_KEY="your_llm_api_key"
export MINIMAX_API_KEY="your_minimax_key"
export VALID_API_TOKENS="token1,token2,token3"  # 逗号分隔
```

#### 方法 B: 配置文件（用于开发环境）

复制密钥模板并填入实际凭据：

```bash
cp credentials.example.py credentials.py
```

编辑 `credentials.py` 填入你的 API 密钥。环境变量优先于文件配置。

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

### 客户端发送消息类型

#### `message` - 文本消息
发送文本给角色进行对话。
```json
{
  "type": "message",
  "content": "你好，爱音！"
}
```

#### `audio_message` - 批量音频消息
发送完整音频进行识别和对话（非流式）。
```json
{
  "type": "audio_message",
  "audio_base64": "<base64_encoded_pcm_audio>"
}
```

#### `audio_stream_start` - 开始流式语音识别
```json
{
  "type": "audio_stream_start"
}
```

#### `audio_stream_chunk` - 发送音频块
在流式识别期间发送音频块。
```json
{
  "type": "audio_stream_chunk",
  "audio_base64": "<base64_encoded_chunk>"
}
```

#### `audio_stream_end` - 结束流式语音识别
```json
{
  "type": "audio_stream_end"
}
```

#### `agent_mode_start` - 开启连续对话模式
开启免唤醒连续语音对话模式。
```json
{
  "type": "agent_mode_start"
}
```

#### `agent_mode_stop` - 退出连续对话模式
```json
{
  "type": "agent_mode_stop"
}
```

#### `agent_audio_chunk` - 连续模式音频块
在 Agent 模式中发送音频块。
```json
{
  "type": "agent_audio_chunk",
  "audio_base64": "<base64_encoded_chunk>"
}
```

#### `switch_character` - 切换角色
```json
{
  "type": "switch_character",
  "character_name": "anon"
}
```

#### `clear_history` - 清除对话历史
```json
{
  "type": "clear_history"
}
```

#### `ping` - 心跳检测
```json
{
  "type": "ping"
}
```

### 服务器响应消息类型

#### `connected` - 连接成功
```json
{
  "type": "connected",
  "character": "anon",
  "character_display_name": "千早爱音 (Chihaya Anon)",
  "message": "Connection established successfully"
}
```

#### `thinking` - 处理中指示
```json
{
  "type": "thinking",
  "message": "Processing..."
}
```

#### `transcription` - 语音识别结果
```json
{
  "type": "transcription",
  "text": "识别的文本",
  "is_partial": true
}
```
`is_partial` 为 `true` 表示是中间结果，`false` 表示最终结果。

#### `audio_chunk` - 音频块（流式 TTS）
```json
{
  "type": "audio_chunk",
  "audio_base64": "<base64_encoded_pcm>",
  "audio_format": "pcm",
  "audio_sample_rate": 16000
}
```

#### `audio_end` - 音频流结束
```json
{
  "type": "audio_end"
}
```

#### `response` - 文本响应
```json
{
  "type": "response",
  "content_cn": "哈喽～",
  "content_jp": "ハロー～",
  "emotion": "happy",
  "audio_format": "pcm",
  "audio_sample_rate": 16000
}
```

#### `audio_stream_started` - 流式识别已开始
```json
{
  "type": "audio_stream_started",
  "message": "Streaming ASR session started"
}
```

#### `agent_listening` - Agent 模式等待输入
```json
{
  "type": "agent_listening",
  "message": "Ready to listen"
}
```

#### `character_switched` - 角色切换成功
```json
{
  "type": "character_switched",
  "character": "anon",
  "character_display_name": "千早爱音 (Chihaya Anon)",
  "message": "Switched to character: anon"
}
```

#### `history_cleared` - 历史已清除
```json
{
  "type": "history_cleared",
  "message": "Conversation history cleared"
}
```

#### `pong` - 心跳响应
```json
{
  "type": "pong"
}
```

#### `disconnected` - 连接断开通知
```json
{
  "type": "disconnected",
  "reason": "new_connection",
  "message": "Another client connected with the same token"
}
```

#### `error` - 错误响应
```json
{
  "type": "error",
  "message": "Error description"
}
```

### 交互模式

#### 1. 文本对话模式
```
客户端: message -> 服务器: thinking -> audio_chunk* -> audio_end -> response
```

#### 2. 批量语音模式
```
客户端: audio_message -> 服务器: thinking -> transcription* -> audio_chunk* -> audio_end -> response
```

#### 3. 流式语音模式
```
客户端: audio_stream_start -> audio_stream_chunk* -> audio_stream_end
服务器: audio_stream_started -> transcription* -> thinking -> audio_chunk* -> audio_end -> response
```

#### 4. Agent 连续对话模式
```
客户端: agent_mode_start -> agent_audio_chunk* -> agent_mode_stop
服务器: agent_listening -> transcription* -> thinking -> audio_chunk* -> audio_end -> response -> agent_listening (循环)
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

endpoints:
  asr: "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
  tts: "wss://api.minimaxi.com/ws/v1/t2a_v2"
  llm: "https://ark.cn-beijing.volces.com/api/v3"

ssl:
  verify_tts: false  # MiniMax may require this

timeouts:
  websocket_connect: 30.0  # Initial connection timeout
  websocket_receive: 0.01  # Non-blocking receive timeout

models:
  llm_model: "doubao-seed-1-6-lite-251015"
  tts_model: "speech-2.8-hd"

audio:
  asr_sample_rate: 16000
  tts_sample_rate: 16000
  channels: 1
  bits: 16

llm:
  stream: false
  reasoning_effort: "low"  # Options: low, medium, high
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
