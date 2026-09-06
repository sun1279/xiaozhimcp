# 小智 ESP32 私有与 YouTube 在线音乐服务 (Xiaozhi Music & YouTube Stream Server)

本项目为 **小智 AI 语音助手（ESP32）** 提供了本地私有音乐曲库与 **全网 YouTube 音乐在线点歌、边下边播（HTTP 分块流式传输）** 的完整一体化解决方案。

通过接入模型上下文协议（**MCP, Model Context Protocol**），让小智大模型能够理解用户的点歌、搜歌、查歌单等自然语言指令；当遇到曲库未收录歌曲时，自动无缝检索 YouTube 音乐，通过本地流媒体服务实时抓取并转码推流给小智硬件播放。

---

## 目录
- [一、 实现原理与核心架构](#一-实现原理与核心架构)
  - [1. 整体系统交互流程图](#1-整体系统交互流程图)
  - [2. 方案 A 核心技术：HTTP 分块流式传输 (Chunked Streaming) 与预缓冲](#2-方案-a-核心技术http-分块流式传输-chunked-streaming-与预缓冲)
  - [3. 本地与在线双模调度机制](#3-本地与在线双模调度机制)
  - [4. 小智固件音频调用机制 (`self.audio.play_url`)](#4-小智固件音频调用机制-selfaudioplay_url)
- [二、 项目目录结构](#二-项目目录结构)
- [三、 环境准备与依赖](#三-环境准备与依赖)
- [四、 服务配置与一键启动](#四-服务配置与一键启动)
  - [1. 配置 MCP WSS 端点](#1-配置-mcp-wss-端点)
  - [2. 一键启动服务](#2-一键启动服务)
  - [3. 小智云端管理后台 (xiaozhi.me) 人设配置](#3-小智云端管理后台-xiaozhime-人设配置)
- [五、 本地静态曲库维护与转码说明](#五-本地静态曲库维护与转码说明)
- [六、 常见问题与排障指南 (FAQ)](#六-常见问题与排障指南-faq)

---

## 一、 实现原理与核心架构

### 1. 整体系统交互流程图

```text
[用户口述点歌] -> 例如："我想听周杰伦的七里香" / "放一首 Taylor Swift 的歌曲"
       │
       ▼
[小智 ESP32 硬件] ────(Opus 语音上传)────> [小智云端大模型 (LLM)]
                                                │
                                      (命中意图，发起 MCP Tool 调用)
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
                 ┌──────────────────────────────┴──────────────────────────────┐
                 ▼                                                             ▼
         【本地曲库命中】                                              【本地未收录：全网检索】
                 │                                                             │
                 │                                                调用 youtubeApi 检索 VideoID
                 │                                                             │
                 │                                                通知 yt_stream_server 预缓冲 64KB
                 │                                                             │
                 └──────────────────────────────┬──────────────────────────────┘
                                                │
                                       返回下发播放指令：
                  指示小智调用设备工具 self.audio.play_url 播放指定 URL
                                                │
                                                ▼
[小智 ESP32 硬件] <──(下发设备指令报文)─────── [小智云端控制通道]
       │
       │ (读取到 audio_url: http://192.168.50.220:8111/stream/<videoId>.mp3)
       │
       ▼
[ESP32 启动后台 HTTP Task] ────> [本地流媒体服务 (yt_stream_server.py: 8111端口)]
       │                                              │
       │                                     ┌────────┴────────┐
       │                                     ▼                 ▼
       │                              【已下载过该歌曲】   【首次点播：实时推流】
       │                                     │                 │
       │                               直接读取本地缓存    yt-dlp 管道传输到 ffmpeg 实时转码
       │                               (秒级秒开)        HTTP 1.1 Chunked 边下边推
       │                                     │                 │
       │<─────────(分块下载 MP3 音频流)────────┴─────────────────┘
       ▼
[ESP32 底层 MP3/I2S 模块] ──> [实时解码并输出到 DAC/I2S 扬声器放音]
```

---

### 2. 方案 A 核心技术：HTTP 分块流式传输 (Chunked Streaming) 与预缓冲

#### ① 为什么传统 HTTP 下载无法实现“边下边播”？
常规 HTTP 服务器（如 Python 原生 `http.server` 或 Nginx）在传输静态文件时：
* 必须在响应头中指定 `Content-Length: <文件总字节数>`。
* 如果歌曲还在下载中，服务器读取到的文件大小仅为当前已写入的几百 KB，发送该 `Content-Length` 后连接会过早断开，导致小智播放器报错或放几秒就戛然而止。
* 如果等待歌曲彻底下载完成再返回小智，一次完整音频下载通常需要 10~30 秒，小智会直接发生网络超时。

#### ② 本方案技术突破：HTTP 1.1 Chunked Transfer Encoding
本项目通过独立自主实现的 `yt_stream_server.py` 完美解决了该瓶颈：
1. **进程级管道级联（Pipelining）**：
   * `yt-dlp`（通过 Android 原生客户端协议）抓取 YouTube 音频流并直接输出至标准输出 (`stdout`)。
   * `ffmpeg` 标准输入 (`stdin`) 直连 `yt-dlp` 的输出管道，实时将音频重编码为标准单/双声道 MP3（128kbps），即时输出至 `stdout`。
2. **HTTP 1.1 分块传输机制（Transfer-Encoding: chunked）**：
   * 服务端响应时不发送 `Content-Length` 报头，而是以 `Transfer-Encoding: chunked` 协议头响应。
   * 内存中维持分块队列与条件变量（Condition Variable），只要 `ffmpeg` 产生几十 KB 数据，立即打包为十六进制分块推向 ESP32。
   * 整个流程在 3~5 秒内即可完成首包输出，小智完全感受不到长时间等待。
3. **预热缓冲与防抖（Pre-buffering API）**：
   * `xzmcp.py` 在搜索到 YouTube 视频后，先向本地流服务发起 `/api/prepare?v=<videoId>` 请求。
   * 流服务在后台启动管道并预先缓冲约 64KB 音频帧到内存。
   * 当小智收到 URL 发起 HTTP GET 请求时，首段音频毫秒级直出，彻底杜绝网络抖动导致的断流卡顿。
4. **透明本地磁盘持久化（双路复用写盘）**：
   * 实时推流的同时，数据同步写入 `downloads/<videoId>.mp3`。
   * 当歌曲播放完毕，磁盘即存有完整无损 MP3。下一次点播同一首歌时，服务端直接走本地静态直链（秒开），极大节省外网带宽并免去重复转码消耗。

---

### 3. 本地与在线双模调度机制

`xzmcp.py` 内部实现了智能双层调度：
1. **第一层：本地私有精选曲库**：
   * 匹配本地预设的高清无损/单声道 Opus 音频（如周杰伦《晴天》、《稻香》等）。
   * 命中时直接返回本地直链，0 延迟、0 外部 API 消耗。
2. **第二层：YouTube 全网在线点播（无缝降级）**：
   * 若本地曲库未命中，自动调用 YouTube Data API 检索视频 ID。
   * 自动调度本地流服务进行分块推流，实现“海量曲库，随意点歌”。

---

### 4. 小智固件音频调用机制 (`self.audio.play_url`)

针对已支持 MP3 硬件/软解码的定制小智固件：
* 协议不使用受限较多的单声道 Opus 原生 `notify` 报文，而是调用小智定制设备工具：
  ```json
  {
      "status": "success",
      "action": "play_audio",
      "instruction": "请立即调用设备工具 self.audio.play_url，arguments.url 使用 audio_url 的值；不要发送 type=notify。",
      "audio_url": "http://192.168.50.220:8111/stream/DYptgVvkVLQ.mp3",
      "song": "周杰伦 Jay Chou【晴天 Sunny Day】",
      "artist": "杰威尔音乐 JVR Music"
  }
  ```
* 小智主控在收到该指令后，直接拉起内部底层音频任务通过 I2S/DAC 解码并播放该 MP3 流。

---

## 二、 项目目录结构

```text
music/
├── yt_stream_server.py     # 核心：流媒体服务器（支持静态文件、HTTP 1.1 分块推流、预热 API、自动缓存）
├── xzmcp.py                # 核心：FastMCP 服务（注册点歌工具，支持本地曲库 + YouTube 联动检索）
├── mcp_pipe.py             # 核心：WebSocket 管道桥接（穿透连接小智云端与本地 stdio MCP）
├── start_services.bat      # 一键启动脚本（同时拉起流媒体服务与 MCP 桥接）
├── downloads/              # 动态下载与缓存目录（自动缓存已播放的 YouTube MP3 歌曲）
├── *.ogg / *.mp3           # 本地常驻精品音乐资源
├── .gitignore              # Git 提交忽略配置（保护 Token 凭证与已下载的音乐媒体）
└── README.md               # 项目技术架构与使用手册
```

---

## 三、 环境准备与依赖

1. **Python 环境**：Python 3.10 或更高版本。
2. **安装核心 Python 库**：
   ```bash
   pip install fastmcp websockets yt-dlp google-api-python-client
   ```
3. **系统依赖 `ffmpeg`**：
   * 确保电脑命令行可执行 `ffmpeg`（若未在 PATH 中，程序也会自动扫描本地 LocalAppData 目录）。
   * 可通过终端运行 `ffmpeg -version` 确认安装。
4. **YouTube API 密钥**：
   * 确保已在 `E:\proj\youtubeApi\api_key.txt` 或环境变量中配置有效的 `YOUTUBE_API_KEY`。

---

## 四、 服务配置与一键启动

### 1. 配置 MCP WSS 端点
1. 登录 [小智控制台 (xiaozhi.me)](https://xiaozhi.me/)。
2. 进入您的设备角色配置 -> **MCP 工具** 配置界面。
3. 复制生成的 **接入点端点 URL**（形式如 `wss://api.xiaozhi.me/mcp/?token=...`）。
4. 将该链接粘贴并保存至 `新建文本文档.txt`（该文件已加入 `.gitignore`，不会被误提交泄露）。

### 2. 一键启动服务
直接双击运行目录下的：
```cmd
start_services.bat
```
脚本将按顺序启动：
1. **音乐与流媒体服务**：监听 `0.0.0.0:8111`，负责静态音频与分块流式音频的分发。
2. **小智 MCP 桥接服务**：连接小智云端网关，自动向大模型注册点歌工具集合。

控制台打印如下信息表示运行正常：
```text
[MusicStreamServer] 运行中: http://0.0.0.0:8111
[SUCCESS] [mcp-pipe] WSS 连接成功！MCP 工具已注册到小智云端。
```

### 3. 小智云端管理后台 (xiaozhi.me) 人设配置
为了确保大模型精准触发点歌工具，在后台的角色 **Prompt（人设设定 / 人物介绍）** 中推荐添加如下引导词：
> **“你具备音乐播放能力。当用户提出听歌、点歌、播放音乐（包括本地歌曲或任意网络歌曲、歌手名）时，请调用专属的 MCP 音乐工具（play_my_music 或 search_and_play_youtube）。收到返回的 audio_url 后，请严格根据指令立即调用设备工具 self.audio.play_url 播放，并在界面或口播中自然地告诉用户正在播放的歌名与歌手。”**

---

## 五、 本地静态曲库维护与转码说明

若需要向本地静态曲库追加免网络请求的精品歌曲：
1. **准备音频**：
   若设备仅支持单声道 Opus，使用 ffmpeg 转换：
   ```bash
   ffmpeg -y -i input.mp3 -c:a libopus -b:a 16k -ac 1 -ar 16000 -frame_duration 60 output.ogg
   ```
   若设备已支持标准 MP3，直接放置 `.mp3` 即可。
2. **在 `xzmcp.py` 的 `MUSIC_LIBRARY` 中注册**：
   ```python
   MUSIC_LIBRARY = {
       "晴天": {
           "url": f"http://{LOCAL_IP}:{SERVER_PORT}/qingtian.mp3",
           "artist": "周杰伦",
           "genre": "流行"
       },
       # 追加新歌...
   }
   ```

---

## 六、 常见问题与排障指南 (FAQ)

### Q1: 点播 YouTube 歌曲时，小智提示找不到歌曲？
1. 检查 YouTube API 密钥配额是否充足（Google Cloud Console）。
2. 查看 `mcp_pipe` 控制台输出是否有 `[YouTube 搜索]` 日志。

### Q2: 小智提示已开始播放，但听不到声音？
1. **检查局域网通信**：确保小智 ESP32 与运行本程序的电脑在同一个路由器局域网下。
2. **Windows 防火墙**：手机在同一 WiFi 下访问 `http://192.168.50.220:8111/qingtian.mp3`，若打不开需在 Windows 防火墙添加入站规则开放 `8111` 端口。
3. **固件工具支持**：确认固件是否已注册并支持 `self.audio.play_url` 设备工具。

### Q3: 为什么第一次播放有些歌需要等待 3~5 秒，第二次秒开？
这是本方案设计的精髓所在：首次点播需要启动 `yt-dlp` 与 `ffmpeg` 进行分块推流与预热缓冲；推流的同时文件会自动无缝写入 `downloads/` 目录；第二次点播直接命中本地硬盘缓存，享受毫秒级秒开体验！
