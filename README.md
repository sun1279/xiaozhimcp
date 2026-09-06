# 小智 ESP32 智能音乐与 YouTube 全网点歌系统 (Xiaozhi Music & YouTube Stream Server)

本项目为 **小智 AI 语音助手（ESP32 系列芯片）** 量身打造了本地私有音乐曲库与 **全网 YouTube 音乐在线点歌、边下边播（HTTP 1.1 Chunked 分块推流）** 的软硬件一体化解决方案。

通过接入模型上下文协议（**MCP, Model Context Protocol**），大语言模型（LLM）能够准确理解用户的点歌、搜歌、查歌单、随机推荐等自然语言意图；当歌曲未在本地收录时，系统自动无缝调用 YouTube Data API 进行全球检索，并通过底层流媒体服务实时抓取、转码推流至 ESP32 硬件播放，同时支持最大 40 首的磁盘自动淘汰保护。

---

## 📑 目录
- [一、 核心特性与亮点](#一-核心特性与亮点)
- [二、 系统架构与实现原理](#二-系统架构与实现原理)
  - [1. 整体架构流程图](#1-整体架构流程图)
  - [2. 方案 A 核心技术：HTTP 1.1 Chunked 边下边播与预缓冲机制](#2-方案-a-核心技术http-11-chunked-边下边播与预缓冲机制)
  - [3. 硬盘容量保护：先进先出 (FIFO) 自动淘汰淘汰机制 (最多 40 首)](#3-硬盘容量保护先进先出-fifo-自动淘汰机制-最多-40-首)
  - [4. 双模分发与本地秒开机制](#4-双模分发与本地秒开机制)
  - [5. 小智固件端交互机制 (`self.audio.play_url`)](#5-小智固件端交互机制-selfaudioplay_url)
- [三、 项目目录与代码结构解析](#三-项目目录与代码结构解析)
- [四、 环境准备与依赖安装](#四-环境准备与依赖安装)
- [五、 服务配置与一键启动](#五-服务配置与一键启动)
  - [第 1 步：配置局域网 IP 与端口](#第-1-步配置局域网-ip-与端口)
  - [第 2 步：配置 YouTube API 密钥](#第-2-步配置-youtube-api-密钥)
  - [第 3 步：配置小智 MCP 接入凭证](#第-3-步配置小智-mcp-接入凭证)
  - [第 4 步：配置小智云端角色 Prompt 人设](#第-4-步配置小智云端角色-prompt-人设)
  - [第 5 步：一键运行服务](#第-5-步一键运行服务)
- [六、 本地私有曲库扩充与音频转码](#六-本地私有曲库扩充与音频转码)
- [七、 接口调试与命令行测试](#七-接口调试与命令行测试)
- [八、 常见问题与排障指南 (FAQ)](#八-常见问题与排障指南-faq)

---

## 一、 核心特性与亮点

- ⚡ **毫秒级起播（首包预热缓冲）**：利用 `/api/prepare` 接口在 MCP 响应期间预先填充 64KB 内存首包，ESP32 发起连接即可秒级出声，无需等待全曲下载。
- 🌐 **全网海量曲库无缝检索**：对接 YouTube Data API，本地没有的冷门歌曲、欧美流行、日韩音乐皆可张口即来。
- 🔄 **HTTP 1.1 分块流式传输 (`Transfer-Encoding: chunked`)**：通过 `yt-dlp` 与 `ffmpeg` 进程管道直通级联，彻底解决传统静态下载由于 `Content-Length` 错误导致的提前截断或超时断流。
- 💾 **智能磁盘保护（FIFO 淘汰策略，上限 40 首）**：自动监控缓存目录，存满 40 首后自动清理最早下载的文件，并定期回收异常中断遗留的 `.tmp` 临时碎片。
- 🚀 **双路复用写盘与二次点播秒开**：推流过程静默落盘至 `downloads/<videoId>.mp3`，再次点播同一歌曲时直接走本地静态直链，零网络流量损耗。
- 🛡️ **安全规范**：Token 凭证、本地端点文件已严格列入 `.gitignore`，避免任何敏感鉴权泄漏。

---

## 二、 系统架构与实现原理

### 1. 整体架构流程图

```text
[用户语音交互] 例如："我想听周杰伦的告白气球" / "放一首 Taylor Swift 的歌曲"
       │
       ▼
[小智 ESP32 硬件] ────(Opus 语音流上传)────> [小智云端大模型 (LLM)]
                                                  │
                                        (命中点歌意图，发起 MCP Tool 调用)
                                                  ▼
                                      [小智云端 MCP 网关 (WSS)]
                                                  │
                                      (经 WebSocket 双向桥接传输)
                                                  ▼
                                           [mcp_pipe.py]
                                                  │ (stdio 管道)
                                                  ▼
                                           [xzmcp.py (FastMCP)]
                                                  │
                 ┌────────────────────────────────┴────────────────────────────────┐
                 ▼                                                                 ▼
         【本地曲库命中】                                                  【本地未收录：全网检索】
                 │                                                                 │
                 │                                                    调用 youtubeApi 检索 VideoID
                 │                                                                 │
                 │                                                    调用 /api/prepare 预缓冲 64KB
                 │                                                                 │
                 └────────────────────────────────┬────────────────────────────────┘
                                                  │
                                         返回下发播放指令：
                    指示小智调用设备工具 self.audio.play_url 播放指定 URL
                                                  │
                                                  ▼
[小智 ESP32 硬件] <───(下发设备控制报文)───────── [小智云端控制通道]
       │
       │ (读取到 audio_url: http://192.168.50.220:8111/stream/<videoId>.mp3)
       │
       ▼
[ESP32 发起 HTTP GET] ────> [本地流媒体服务 (yt_stream_server.py: 端口 8111)]
       │                                                 │
       │                                        ┌────────┴────────┐
       │                                        ▼                 ▼
       │                                 【已缓存过该歌曲】    【首次点播：实时推流】
       │                                        │                 │
       │                                  直接读取本地文件    yt-dlp 管道传输至 ffmpeg 实时转码
       │                                  (秒级直出)         HTTP 1.1 Chunked 边下边推
       │                                        │                 │
       │                                        │            同时写入 downloads/<videoId>.mp3
       │                                        │            (存满 40 首触发 FIFO 自动清理)
       │                                        │                 │
       │<──────────(分块接收 MP3 音频流)──────────┴─────────────────┘
       ▼
[ESP32 底层 MP3/I2S 模块] ──> [实时解码音频流并输出到扬声器放音]
```

---

### 2. 方案 A 核心技术：HTTP 1.1 Chunked 边下边播与预缓冲机制

#### ① 为什么传统 HTTP 静态服务无法做到“边下边播”？
在传统 HTTP 1.0/1.1 静态文件下载中：
- 服务端响应必须在响应头声明 `Content-Length: <文件总字节数>`。
- 当歌曲还在后台下载时，文件大小在不断增长。如果发送当前已下载的几十 KB 作为 `Content-Length`，ESP32 播放器读取完毕后就会**立即断开连接**，导致音乐播几秒就停。
- 若等待全曲彻底下载完毕后再返回，用户需要原地等待 15~30 秒，极易触发小智对话超时。

#### ② 本项目解决方案：HTTP 1.1 Chunked 流式传输
[`yt_stream_server.py`](file:///C:/Users/26621/Documents/ChatGPT/music/yt_stream_server.py) 使用了以下技术闭环：
1. **管道直连（Pipelining）**：
   - `yt-dlp`（通过 Android 移动端客户端协议）抓取音频流，输出到 `stdout`。
   - `ffmpeg` 从标准输入读取数据流，实时转码为兼容性极佳的标准 MP3（128kbps），输出到 `stdout`。
2. **分块传输编码（Transfer-Encoding: chunked）**：
   - 响应头去除 `Content-Length`，设置 `Transfer-Encoding: chunked` 与 `Connection: keep-alive`。
   - 内存维护分块队列，只要 `ffmpeg` 产生 16KB 数据，即刻封装为十六进制分块推向客户端，直至传输完毕发送 `0\r\n\r\n` 终止符。
3. **RAM 预热缓冲（Pre-buffering）**：
   - 在大模型组织语言回复的 3~4 秒间隙，MCP 会调用 `/api/prepare?v=<id>`。
   - 流媒体服务预先缓冲 64KB（约 4~5 秒音频），使得 ESP32 建立连接后第一包数据能够**零延迟**抵达，彻底杜绝起播卡顿。

---

### 3. 硬盘容量保护：先进先出 (FIFO) 自动淘汰机制 (最多 40 首)

为防止长期点歌导致主机磁盘耗尽，服务内置了严密的容量防护逻辑：
1. **容量限制**：设定最大缓存首数 `MAX_CACHED_SONGS = 40`。
2. **淘汰策略**：
   - 依据文件的最后修改/创建时间戳（`st_mtime`）进行排序；
   - 当 `downloads/` 目录中的完整 `.mp3` 数量超过 40 首时，按**从旧到新（FIFO）**顺序批量删除最早下载的文件；
3. **双重检测触发**：
   - **完成即检**：每首新歌曲推流落盘重命名完成后立即触发；
   - **启动自检**：服务每次启动运行时自动扫描清理历史冗余文件；
4. **临时垃圾回收**：自动清理下载中断遗留超过 1 小时的 `.tmp` 临时碎片。

---

### 4. 双模分发与本地秒开机制

- **本地私有曲库优先**：在 [`xzmcp.py`](file:///C:/Users/26621/Documents/ChatGPT/music/xzmcp.py) 中维护精选曲目（如周杰伦经典歌曲）。若命中本地曲目，直接返回本地静态直链，无任何外部 API 与网络消耗。
- **全网在线自动降级**：本地未命中时，自动进入 YouTube 检索与动态推流。
- **热歌自动固化**：首次点播的 YouTube 歌曲在落盘后，下次点播同首歌时将直接作为静态文件秒发（支持 `Content-Length` 和断点续传），无需再次启动转码进程。

---

### 5. 小智固件端交互机制 (`self.audio.play_url`)

针对支持 MP3 硬件/软解码的小智固件，MCP 服务在识别到歌曲后返回定制动作报文：
```json
{
  "status": "success",
  "song_name": "告白气球",
  "artist": "周杰伦",
  "audio_url": "http://192.168.50.220:8111/stream/bu7nU9Mhpyo.mp3",
  "action": "play_audio",
  "instruction": "请立即调用设备工具 self.audio.play_url，arguments.url 使用 audio_url 的值；不要发送 type=notify。",
  "message": "已找到歌曲《告白气球》- 周杰伦"
}
```
大模型解析该指令后，会通过下行通道驱动 ESP32 调用自身的底层音频播放器播放目标 URL。

---

## 三、 项目目录与代码结构解析

```text
music/
├── yt_stream_server.py      # 流媒体服务器：HTTP 1.1 Chunked 推流、预热 API、静态文件、40首自动清理
├── xzmcp.py                 # MCP 核心服务：注册点歌工具、曲库匹配、调用 YouTube 检索
├── mcp_pipe.py              # WebSocket 桥接守护进程：双向管道连接小智官方云端网关与本地 stdio MCP
├── start_services.bat       # 一键启动批处理脚本（自动拉起流媒体服务与 MCP 桥接）
├── downloads/               # 自动缓存目录：动态存储 YouTube 转码后的 MP3 歌曲（受 FIFO 保护）
├── qingtian.mp3/.ogg        # 本地精选常驻曲目示例
├── daoxiang.mp3/.ogg        # 本地精选常驻曲目示例
├── xiaoyanzi.mp3/.ogg       # 本地精选常驻曲目示例
├── 新建文本文档.txt          # 本地端点配置备份（仅在本地使用，已被 Git 忽略）
├── .gitignore               # 严格过滤 Token、缓存音频、临时文件等敏感信息
└── README.md                # 完整技术架构与操作手册
```

### 核心模块职责说明：

| 模块 | 核心类 / 函数 | 功能说明 |
| :--- | :--- | :--- |
| **`yt_stream_server.py`** | `StreamSession` | 管理单个视频的 `yt-dlp` | `ffmpeg` 管道、内存分块队列与本地 `.tmp` 写入 |
| | `StreamManager` | 线程安全的流会话单例管理，防止同一首歌并发重复转码 |
| | `cleanup_old_downloads` | 扫描 `downloads/` 目录，超出 40 首时依据时间戳删除最旧文件 |
| | `MusicStreamHandler` | 处理 `/api/prepare`、`/stream/<id>.mp3` 及常规静态文件路由 |
| **`xzmcp.py`** | `play_my_music` | 主入口工具：优先私有曲库，未命中自动转入 YouTube 搜索推流 |
| | `search_and_play_youtube` | 专用全网点歌工具：专用于检索并播放 YouTube 音频 |
| | `list_music_library` | 查询歌单工具：列出当前本地收录的歌曲 |
| | `play_random_music` | 随机点歌工具：在本地精选曲库中随机抽选 |
| **`mcp_pipe.py`** | `run_pipe` | 维持与 `wss://api.xiaozhi.me/mcp/?token=...` 的长连接，双向映射 stdio |

---

## 四、 环境准备与依赖安装

### 1. Python 环境
- 推荐使用 **Python 3.10** 及以上版本（已在 Python 3.10 ~ 3.14 环境充分测试）。

### 2. 安装 Python 核心依赖
打开终端执行：
```bash
pip install fastmcp websockets yt-dlp google-api-python-client
```

### 3. 安装并检查 `ffmpeg`
`ffmpeg` 是音频实时重编码的核心依赖：
- **Windows 用户**：
  - 若已安装通过 PATH 全局可用，可运行 `ffmpeg -version` 检查；
  - 本项目的 `yt_stream_server.py` 还内置了自动扫描 `%LOCALAPPDATA%` 目录功能，若 `winget` 或其他工具安装过 `ffmpeg.exe`，系统可自动发现并加载。

---

## 五、 服务配置与一键启动

### 第 1 步：配置局域网 IP 与端口
1. 打开命令行运行 `ipconfig`，查看电脑当前的无线局域网 IPv4 地址（如 `192.168.50.220`）。
2. 打开 [`xzmcp.py`](file:///C:/Users/26621/Documents/ChatGPT/music/xzmcp.py)，确认或修改顶部的配置：
   ```python
   LOCAL_IP = "192.168.50.220"  # 替换为您的本机局域网 IP
   SERVER_PORT = 8111            # 默认推流端口 8111
   ```

### 第 2 步：配置 YouTube API 密钥
确保在 `E:\proj\youtubeApi\api_key.txt` 中填入了有效的 Google Cloud **YouTube Data API v3** 密钥（或通过系统环境变量 `YOUTUBE_API_KEY` 提供）。

### 第 3 步：配置小智 MCP 接入凭证
1. 访问并登录 [小智控制台 (xiaozhi.me)](https://xiaozhi.me/)。
2. 进入您的智能体角色配置 -> **MCP 工具**。
3. 复制生成的接入点 URL，格式为：
   ```text
   wss://api.xiaozhi.me/mcp/?token=eyJhbGciOi...
   ```
4. 将该完整链接粘贴并保存在本目录下的 `新建文本文档.txt` 中。

### 第 4 步：配置小智云端角色 Prompt 人设
为确保小智大模型能准确理解用户的点歌需求，请登录小智后台，在角色的 **Prompt（人设设定 / 人物描述）** 中追加以下引导词：

> **“你具备专业的音乐播放与点播能力。规则如下：**
> 1. **直接点播**：当用户明确说‘播放xxx’、‘放一首xxx’、‘来一首xxx’时，调用 `play_my_music` 直接秒播最匹配的一首；
> 2. **搜索挑选**：当用户说‘搜一下xxx’、‘找找某某的歌’、‘查一下有什么版本’或泛指歌手名时，调用 `search_music_options` 检索 3 首候选。收到候选列表后，用简短自然的口语朗读给用户（如：‘为您找到了3个版本：1. 官方MV 2. 现场版 3. 伴奏，您想听哪一个？’），此时**切勿**调用播放工具，等待用户语音回答；
> 3. **确认选歌**：当用户回答‘第1个’、‘放第二个’、‘听现场版’等选择时，调用 `play_selected_song` 传入序号或 video_id；
> 4. **执行播放**：收到任何返回 audio_url 的成功结果后，严格根据 instruction 指令立即调用设备工具 `self.audio.play_url` 播放音乐，不要回复 type=notify。”**

### 第 5 步：一键运行服务
双击运行目录下的批处理脚本：
```bat
start_services.bat
```
脚本将按顺序拉起两个独立窗口：
1. **音乐与流媒体服务**：监听 `0.0.0.0:8111`；
2. **小智 MCP 桥接服务**：连接小智云端网关，自动注册工具集。

控制台看到如下日志即表示一切就绪：
```text
[MusicStreamServer] 运行中: http://0.0.0.0:8111
[MusicStreamServer] 缓存目录: ...\downloads
[INFO] Starting MCP server 'XiaozhiPrivateMusicServer' with transport 'stdio'
[SUCCESS] [mcp-pipe] WSS 连接成功！MCP 工具已注册到小智云端。
```

---

## 六、 本地私有曲库扩充与音频转码

若有本地高品质歌曲希望免网络检索直接秒播，可放入项目根目录：
1. **若设备固件已支持 MP3（推荐）**：
   直接将 `.mp3` 拷贝至本目录（如 `mysong.mp3`）。
2. **若设备固件为原生纯 Opus（仅识别单声道 Ogg）**：
   使用 `ffmpeg` 转换为标准 16kHz 单声道 Opus 文件：
   ```bash
   ffmpeg -y -i mysong.mp3 -c:a libopus -b:a 16k -ac 1 -ar 16000 -frame_duration 60 mysong.ogg
   ```
3. **在 [`xzmcp.py`](file:///C:/Users/26621/Documents/ChatGPT/music/xzmcp.py) 的 `MUSIC_LIBRARY` 中追加条目**：
   ```python
   MUSIC_LIBRARY = {
       "晴天": {
           "url": f"http://{LOCAL_IP}:{SERVER_PORT}/qingtian.mp3",
           "artist": "周杰伦",
           "genre": "流行"
       },
       "我的新歌": {
           "url": f"http://{LOCAL_IP}:{SERVER_PORT}/mysong.mp3",
           "artist": "歌手名",
           "genre": "流行"
       },
   }
   ```

---

## 七、 接口调试与命令行测试

您可以使用系统自带的 `curl` 或 Python 脚本在本地验证各功能环节：

### 1. 测试流媒体服务是否在线
```bash
curl -I http://127.0.0.1:8111/
```
*预期返回：`HTTP/1.0 200 OK`*

### 2. 测试本地静态文件读取
```bash
curl -i http://127.0.0.1:8111/qingtian.mp3 --range 0-100
```
*预期返回：`HTTP/1.0 200 OK`，并输出音频头部二进制。*

### 3. 测试 YouTube 预热缓冲接口
```bash
curl http://127.0.0.1:8111/api/prepare?v=DYptgVvkVLQ
```
*预期返回 JSON：`{"ok": true, "cached": false, "videoId": "DYptgVvkVLQ", "buffered_bytes": 65536, "ready": true}`*

### 4. 测试实时分块流式传输 (Chunked Stream)
```bash
curl -i http://127.0.0.1:8111/stream/DYptgVvkVLQ.mp3 --max-time 5 -o test.mp3
```
*预期输出响应头包含：`Transfer-Encoding: chunked`，并在 5 秒内稳定接收数兆字节数据。*

### 5. 测试 MCP 工具全流程执行
在命令行执行以下单行 Python 测试：
```bash
python -c "import xzmcp; print(xzmcp.play_my_music('告白气球'))"
```
*预期输出：自动命中 YouTube 检索并返回带流媒体地址的合法 JSON。*

---

## 八、 常见问题与排障指南 (FAQ)

### Q1: 小智说“正在播放”，但音响完全没有声音？
1. **Windows 防火墙拦截入站端口**：
   - 手机连入同一 Wi-Fi，在手机浏览器中打开 `http://192.168.50.220:8111/qingtian.mp3`。
   - 如果手机打不开或持续转圈，说明 Windows 防火墙拦截了外来设备访问电脑的 `8111` 端口。请在 Windows 高级防火墙中添加入站规则，允许 TCP 端口 `8111`。
2. **局域网跨网段/路由隔离**：
   - 确保小智连接的 Wi-Fi 与电脑属于同一个路由器的相同网段（如 `192.168.50.x`），且路由器未开启“AP 隔离”功能。
3. **固件是否支持 MP3**：
   - 确保固件支持通过 `self.audio.play_url` 播放 MP3 流。如果使用官方未经修改的纯 Opus 固件，请使用前述转码命令生成单声道 16kHz `.ogg`。

### Q2: 点播 YouTube 歌曲时提示未找到或搜索异常？
1. **API Key 验证**：检查 Google Cloud Console 中 YouTube Data API v3 服务的启用状态和每日配额使用情况。
2. **网络环境**：从命令行运行 `yt-dlp -F "https://www.youtube.com/watch?v=bu7nU9Mhpyo"` 测试本机网络抓取 YouTube 媒体流的能力。

### Q3: 端口 8111 提示“Address already in use”？
说明之前启动的 Python 服务未完全退出：
```powershell
# 查询占用 8111 端口的 PID
netstat -ano | findstr 8111
# 强制终止该进程
Stop-Process -Id <PID> -Force
```

### Q4: 缓存歌曲达到 40 首后会发生什么？
服务会自动按时间顺序删除最旧的一首下载歌曲，无需任何人工介入或手动清空硬盘，目录始终维持在 40 首以内，保障系统长久平稳运行。

---

## 📄 开源许可证
本项目遵循 MIT 开源许可证。欢迎提交 Issue 或 Pull Request！
