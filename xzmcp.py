import warnings
warnings.filterwarnings("ignore", message=".*IncompleteFieldDefinitionWarning.*")
warnings.filterwarnings("ignore", message=".*lifespan.*")
import os
import sys
import json
import random
import urllib.request
import urllib.parse
from pathlib import Path
from fastmcp import FastMCP

# 导入 YouTube API 模块
YOUTUBE_API_DIR = r"E:\proj\youtubeApi"
if os.path.exists(YOUTUBE_API_DIR) and YOUTUBE_API_DIR not in sys.path:
    sys.path.append(YOUTUBE_API_DIR)

try:
    from youtube_search_demo import search_videos, load_api_key
    YOUTUBE_ENABLED = True
except Exception as e:
    print(f"[WARN] 导入 youtubeApi 失败: {e}", file=sys.stderr, flush=True)
    YOUTUBE_ENABLED = False

# 1. 初始化 MCP 服务
mcp = FastMCP("XiaozhiPrivateMusicServer")

LOCAL_IP = os.environ.get("LOCAL_IP", "192.168.50.220")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8111"))
# 支持统一前缀（如 http://music.sunjw.cn），默认使用本地局域网 IP 与端口
BASE_URL = os.environ.get("BASE_URL", f"http://{LOCAL_IP}:{SERVER_PORT}").rstrip("/")

# 2. 私有曲库数据。URL 必须能被 ESP32 直接访问并返回 MP3 数据。
MUSIC_LIBRARY = {
    "晴天": {
        "url": f"{BASE_URL}/qingtian.mp3",
        "artist": "周杰伦",
        "genre": "流行"
    },
    "稻香": {
        "url": f"{BASE_URL}/daoxiang.mp3",
        "artist": "周杰伦",
        "genre": "流行"
    },
    "小燕子": {
        "url": f"{BASE_URL}/xiaoyanzi.mp3",
        "artist": "未知",
        "genre": "未知"
    },
    "夜曲": {
        "url": f"{BASE_URL}/yequ.mp3",
        "artist": "周杰伦",
        "genre": "流行"
    },
    "海阔天空": {
        "url": f"{BASE_URL}/haikuotiankong.mp3",
        "artist": "Beyond",
        "genre": "摇滚"
    }
}

def make_music_result(song_name: str, artist: str, audio_url: str) -> dict:
    """Return a URL for the model to pass to the device playback tool."""
    print(
        f"[DEBUG 返回歌曲结果] song='{song_name}' url='{audio_url}'; "
        "等待云端调用 self.audio.play_url",
        file=sys.stderr,
        flush=True,
    )
    return {
        "status": "success",
        "song_name": song_name,
        "artist": artist,
        "audio_url": audio_url,
        "instruction": (
            "请立即调用设备工具 self.audio.play_url，"
            "arguments.url 使用 audio_url 的值；不要发送 type=notify。"
        ),
        "message": f"已找到歌曲《{song_name}》- {artist}"
    }

def search_youtube_and_stream(query: str) -> dict:
    """搜索 YouTube 并请求本地流服务器预缓冲几秒，然后返回小智播放链接"""
    if not YOUTUBE_ENABLED:
        return {"status": "error", "message": "YouTube 模块未启用"}

    api_key = load_api_key()
    if not api_key:
        print("[YouTube Error] 未找到 YOUTUBE_API_KEY", file=sys.stderr, flush=True)
        return {"status": "error", "message": "未配置 YouTube API Key"}

    print(f"[YouTube 搜索] 正在检索: '{query}'", file=sys.stderr, flush=True)
    try:
        items = search_videos(query, api_key, max_results=1)
        if not items:
            return {"status": "not_found", "message": f"在 YouTube 上未搜索到《{query}》"}
        
        item = items[0]
        video_id = item.get("id", {}).get("videoId", "")
        snippet = item.get("snippet", {})
        title = snippet.get("title", query)
        channel = snippet.get("channelTitle", "YouTube")

        print(f"[YouTube 命中] 《{title}》 ID: {video_id}，正在通知流服务器预缓冲...", file=sys.stderr, flush=True)

        # 触发本地流服务器预缓冲 (等待几秒首包缓冲完成)
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{SERVER_PORT}/api/prepare?v={video_id}",
                headers={"User-Agent": "XiaozhiMCP/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                print(f"[YouTube 预缓冲响应] {data}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[YouTube 预缓冲请求异常] {e} (客户端直接请求时会自动触发流式缓冲)", file=sys.stderr, flush=True)

        stream_url = f"{BASE_URL}/stream/{video_id}.mp3"
        return make_music_result(title, channel, stream_url)
    except Exception as e:
        print(f"[YouTube 搜索异常] {e}", file=sys.stderr, flush=True)
        return {"status": "error", "message": f"YouTube 搜索异常: {e}"}

# --- 功能 1：根据歌名点歌播放（优先私有曲库，若无则无缝搜索 YouTube 边下边播） ---
@mcp.tool()
def play_my_music(song_name: str) -> str:
    """
    【指定点歌】当用户明确指定歌名想要听某首歌时调用。支持私有曲库及全网 YouTube 歌曲点歌。
    :param song_name: 歌曲名称或关键词，例如：晴天、稻香、七里香、Taylor Swift
    """
    print(f"\n[DEBUG 收到点歌请求] 查找歌名: '{song_name}'", file=sys.stderr, flush=True)
    # 1. 优先在本地私有曲库中查找
    for name, info in MUSIC_LIBRARY.items():
        if song_name.lower() in name.lower() or name.lower() in song_name.lower():
            print(f"[DEBUG 命中私有曲库] 《{name}》- {info['artist']} -> {info['url']}\n", file=sys.stderr, flush=True)
            return json.dumps(make_music_result(name, info["artist"], info["url"]), ensure_ascii=False)

    # 2. 本地曲库未命中，自动无缝检索 YouTube 进行实时流式点播 (方案 A)
    print(f"[DEBUG 私有曲库未命中] 自动启动全网 YouTube 检索与实时流式缓冲: '{song_name}'\n", file=sys.stderr, flush=True)
    res = search_youtube_and_stream(song_name)
    return json.dumps(res, ensure_ascii=False)

# --- 功能 2：专用的 YouTube 点歌工具 ---
@mcp.tool()
def search_and_play_youtube(song_name: str) -> str:
    """
    【YouTube 点歌】专门用于在 YouTube 上搜索音乐并实时流式推送给小智播放。
    :param song_name: 想要搜索并播放的 YouTube 歌曲名或艺术家
    """
    print(f"\n[DEBUG 收到 YouTube 点歌] '{song_name}'", file=sys.stderr, flush=True)
    res = search_youtube_and_stream(song_name)
    return json.dumps(res, ensure_ascii=False)

# --- 功能 3：列出曲库中的所有歌曲 ---
@mcp.tool()
def list_music_library() -> str:
    """
    【列出歌单】当用户询问“有什么歌”、“有什么歌曲”、“列出歌单”、“曲库列表”、“你能放什么歌”时调用。
    """
    print(f"\n[DEBUG 收到查询歌单请求]", file=sys.stderr, flush=True)
    songs_list = []
    for name, info in MUSIC_LIBRARY.items():
        songs_list.append({
            "song": name,
            "artist": info["artist"],
            "genre": info["genre"]
        })
    print(f"[DEBUG 导出歌单] 共 {len(songs_list)} 首歌曲\n", file=sys.stderr, flush=True)
    return json.dumps({
        "status": "success",
        "total": len(songs_list),
        "songs": songs_list,
        "message": f"本地私有曲库中共有 {len(songs_list)} 首歌曲：{', '.join([s['song'] for s in songs_list])}。同时也支持点播任何 YouTube 音乐！"
    }, ensure_ascii=False)

# --- 功能 4：随机播放一首歌曲 ---
@mcp.tool()
def play_random_music() -> str:
    """
    【随机点歌】当用户说“随便放首歌”、“推荐一首歌”、“随机播放音乐”、“来点音乐”时调用。
    """
    print(f"\n[DEBUG 收到随机播放请求]", file=sys.stderr, flush=True)
    name = random.choice(list(MUSIC_LIBRARY.keys()))
    info = MUSIC_LIBRARY[name]
    print(f"[DEBUG 随机抽取] 抽中《{name}》- {info['artist']} -> {info['url']}\n", file=sys.stderr, flush=True)
    return json.dumps(make_music_result(name, info["artist"], info["url"]), ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()
