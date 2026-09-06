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

ROOT_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = ROOT_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
METADATA_FILE = DOWNLOADS_DIR / "metadata.json"

def load_metadata() -> dict:
    if METADATA_FILE.exists():
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_metadata(video_id: str, title: str, channel: str = ""):
    try:
        data = load_metadata()
        data[video_id] = {
            "title": title,
            "channel": channel,
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "timestamp": time.time()
        }
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Metadata Error] {e}", file=sys.stderr, flush=True)

def notify_log(event_type: str, msg: str):
    """尝试将事件发送到流媒体日志缓冲区"""
    try:
        url = f"http://127.0.0.1:{SERVER_PORT}/api/log_event?type={urllib.parse.quote(event_type)}&msg={urllib.parse.quote(msg)}"
        req = urllib.request.Request(url, headers={"User-Agent": "XiaozhiMCP/1.0"})
        with urllib.request.urlopen(req, timeout=1):
            pass
    except Exception:
        pass

def clean_song_query(query: str) -> str:
    cleaned = query.strip()
    for prefix in ["我想听", "我要听", "帮我放", "请播放", "播放一首", "播放", "放一首", "来一首", "唱一首", "搜索", "点歌", "点一首", "给我放", "给我唱"]:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    for suffix in ["的歌", "的歌曲", "这首歌", "这首歌曲", "这歌", "一首"]:
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)].strip()
            break
    return cleaned if cleaned else query.strip()

def search_youtube_and_stream(query: str) -> dict:
    """搜索 YouTube 并请求本地流服务器预缓冲几秒，然后返回小智播放链接"""
    if not YOUTUBE_ENABLED:
        notify_log("ERROR", f"YouTube 模块未启用 (搜索: {query})")
        return {"status": "error", "message": "YouTube 模块未启用"}

    api_key = load_api_key()
    if not api_key:
        print("[YouTube Error] 未找到 YOUTUBE_API_KEY", file=sys.stderr, flush=True)
        notify_log("ERROR", "未配置 YOUTUBE_API_KEY")
        return {"status": "error", "message": "未配置 YouTube API Key"}

    cleaned_q = clean_song_query(query)
    print(f"[YouTube 搜索] 原始: '{query}' -> 清洗: '{cleaned_q}'", file=sys.stderr, flush=True)
    notify_log("SEARCH", f"检索: '{query}'" + (f" (清洗词: '{cleaned_q}')" if cleaned_q != query else ""))

    try:
        items = search_videos(cleaned_q, api_key, max_results=1)
        if not items and cleaned_q != query:
            print(f"[YouTube 搜索] 清洗词未搜到，回退原始词重试: '{query}'", file=sys.stderr, flush=True)
            items = search_videos(query, api_key, max_results=1)

        if not items:
            notify_log("NOT_FOUND", f"未找到: '{query}'")
            return {"status": "not_found", "message": f"在 YouTube 上未搜索到《{query}》"}
        
        item = items[0]
        video_id = item.get("id", {}).get("videoId", "")
        snippet = item.get("snippet", {})
        title = snippet.get("title", query)
        channel = snippet.get("channelTitle", "YouTube")

        print(f"[YouTube 命中] 《{title}》 ID: {video_id}，正在通知流服务器预缓冲...", file=sys.stderr, flush=True)
        notify_log("HIT", f"命中《{title}》({channel}) ID: {video_id}")
        save_metadata(video_id, title, channel)

        # 触发本地流服务器预缓冲 (等待几秒首包缓冲完成)
        try:
            enc_t = urllib.parse.quote(title)
            enc_c = urllib.parse.quote(channel)
            req = urllib.request.Request(
                f"http://127.0.0.1:{SERVER_PORT}/api/prepare?v={video_id}&title={enc_t}&channel={enc_c}",
                headers={"User-Agent": "XiaozhiMCP/1.0"}
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                print(f"[YouTube 预缓冲响应] {data}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[YouTube 预缓冲请求异常] {e} (客户端直接请求时会自动触发流式缓冲)", file=sys.stderr, flush=True)

        stream_url = f"{BASE_URL}/stream/{video_id}.mp3"
        return make_music_result(title, channel, stream_url)
    except Exception as e:
        print(f"[YouTube 搜索异常] {e}", file=sys.stderr, flush=True)
        notify_log("ERROR", f"搜索异常: {e}")
        return {"status": "error", "message": f"YouTube 搜索异常: {e}"}

LAST_SEARCH_OPTIONS = []

def parse_choice_index(text: str) -> int | None:
    """尝试从自然语言文本解析用户选择的序号（0-indexed）"""
    t = text.strip()
    patterns = [
        (0, ["1", "一", "第一", "首个", "1号"]),
        (1, ["2", "二", "两", "第二", "2号"]),
        (2, ["3", "三", "第三", "3号"]),
        (3, ["4", "四", "第四", "4号"]),
        (4, ["5", "五", "第五", "5号"]),
    ]
    # 优先匹配“第X”、“选X”、“听X”、“放X”、“来X”
    for idx, keywords in patterns:
        for kw in keywords:
            for verb in ["第", "选", "听", "放", "来", "要"]:
                if f"{verb}{kw}" in t:
                    return idx
    # 精确或短文本包含
    for idx, keywords in patterns:
        for kw in keywords:
            if t == kw or (kw in t and len(t) <= 6):
                return idx
    return None

# --- 功能 1：搜索歌曲候选列表（提供多个结果让用户语音选择） ---
@mcp.tool()
def search_music_options(query: str, count: int = 3) -> str:
    """
    【搜索歌曲/候选版本挑选】当用户要求“搜索歌曲”、“找找某某的歌”、“搜一下xxx”、“有哪些版本”、“查一下某歌”或泛指歌手名时调用。
    返回 3 个候选歌曲版本，由大模型用语音念给用户听并等待用户语音回答挑选第几个。
    :param query: 想要搜索的歌曲关键词、歌名或歌手名
    :param count: 候选数量，默认 3 首 (1~5)
    """
    global LAST_SEARCH_OPTIONS
    print(f"\n[DEBUG 搜索歌曲候选] query: '{query}', count={count}", file=sys.stderr, flush=True)
    notify_log("SEARCH", f"候选检索: '{query}'")

    if not YOUTUBE_ENABLED:
        notify_log("ERROR", "YouTube 模块未启用")
        return json.dumps({"status": "error", "message": "YouTube 搜索模块未启用"}, ensure_ascii=False)

    api_key = load_api_key()
    if not api_key:
        notify_log("ERROR", "未配置 YOUTUBE_API_KEY")
        return json.dumps({"status": "error", "message": "未配置 YouTube API Key"}, ensure_ascii=False)

    cleaned_q = clean_song_query(query)
    num = max(1, min(int(count), 5))
    try:
        items = search_videos(cleaned_q, api_key, max_results=num)
        if not items and cleaned_q != query:
            items = search_videos(query, api_key, max_results=num)

        if not items:
            notify_log("NOT_FOUND", f"未找到候选: '{query}'")
            return json.dumps({"status": "not_found", "message": f"在 YouTube 上未找到与《{query}》相关的歌曲"}, ensure_ascii=False)

        options = []
        for idx, item in enumerate(items):
            vid = item.get("id", {}).get("videoId", "")
            snip = item.get("snippet", {})
            title = snip.get("title", f"选项 {idx+1}")
            channel = snip.get("channelTitle", "")
            options.append({
                "index": idx + 1,
                "title": title,
                "channel": channel,
                "video_id": vid
            })

        LAST_SEARCH_OPTIONS = options
        notify_log("HIT", f"找到 {len(options)} 首候选 (首选: {options[0]['title'][:18]})")

        return json.dumps({
            "status": "multiple_results",
            "query": query,
            "total": len(options),
            "options": options,
            "instruction": (
                "【重要指令】请用亲切简短的中文口语向用户朗读找到的这些选项（例如：'为您找到了几个版本：第一个是xxx，第二个是xxx，请问您想听第几个？'）。"
                "【禁止】此时切勿调用设备播放工具 self.audio.play_url，必须等待用户回答序号（如“第1个”、“放第二个”）或选定具体版本后，"
                "再调用 play_selected_song 工具（传入对应选项的 video_id）来进行播放。"
            )
        }, ensure_ascii=False)
    except Exception as e:
        print(f"[YouTube 候选搜索异常] {e}", file=sys.stderr, flush=True)
        notify_log("ERROR", f"候选搜索异常: {e}")
        return json.dumps({"status": "error", "message": f"搜索异常: {e}"}, ensure_ascii=False)

# --- 功能 2：播放用户选中的歌曲 ---
@mcp.tool()
def play_selected_song(video_id_or_index: str, title: str = "") -> str:
    """
    【播放用户选中的歌曲】当用户从之前 search_music_options 列出的候选中选定了某首歌（例如用户回答说“第1个”、“放第二个”、“听现场版”等）时调用。
    :param video_id_or_index: 选中的序号（如 '1', '2' 或 '第1个'）或者对应的 YouTube 视频 ID (如 'nDchQNPuA0k')
    :param title: 歌曲标题（可选）
    """
    global LAST_SEARCH_OPTIONS
    print(f"\n[DEBUG 播放选中歌曲] target: '{video_id_or_index}', title: '{title}'", file=sys.stderr, flush=True)

    target = str(video_id_or_index).strip()
    video_id = target
    choice_idx = parse_choice_index(target)

    # 1. 序号索引解析
    if choice_idx is not None and LAST_SEARCH_OPTIONS and 0 <= choice_idx < len(LAST_SEARCH_OPTIONS):
        sel = LAST_SEARCH_OPTIONS[choice_idx]
        if sel.get("url") and not sel.get("video_id"):
            # 命中了本地内置常驻曲目（如晴天、稻香）
            print(f"[DEBUG 播放内置常驻曲目] 《{sel['title']}》 -> {sel['url']}\n", file=sys.stderr, flush=True)
            notify_log("LOCAL_HIT", f"选中本地内置: {sel['title']}")
            return json.dumps(make_music_result(sel["title"], sel.get("channel", "精选曲库"), sel["url"]), ensure_ascii=False)
        video_id = sel["video_id"]
        title = title or sel["title"]
    elif len(video_id) != 11 and LAST_SEARCH_OPTIONS:
        # 2. 如果不是11位ID，尝试按标题关键字模糊匹配
        for opt in LAST_SEARCH_OPTIONS:
            if target.lower() in opt["title"].lower() or (title and title.lower() in opt["title"].lower()):
                if opt.get("url") and not opt.get("video_id"):
                    return json.dumps(make_music_result(opt["title"], opt.get("channel", "精选曲库"), opt["url"]), ensure_ascii=False)
                video_id = opt["video_id"]
                title = opt["title"]
                break

    notify_log("HIT", f"选中播放: {title or video_id} (ID: {video_id})")
    if title:
        save_metadata(video_id, title)

    # 预缓冲
    try:
        enc_t = urllib.parse.quote(title) if title else ""
        req = urllib.request.Request(
            f"http://127.0.0.1:{SERVER_PORT}/api/prepare?v={video_id}&title={enc_t}",
            headers={"User-Agent": "XiaozhiMCP/1.0"}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[YouTube 预缓冲响应] {data}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[YouTube 预缓冲请求异常] {e}", file=sys.stderr, flush=True)

    stream_url = f"{BASE_URL}/stream/{video_id}.mp3"
    return json.dumps(make_music_result(title or f"YouTube 音乐", "YouTube", stream_url), ensure_ascii=False)

# --- 功能 3：直接点歌播放（无需选择，优先本地曲库，其次秒播 YouTube 第一条） ---
@mcp.tool()
def play_my_music(song_name: str) -> str:
    """
    【直接点歌播放】当用户明确指定歌名想要直接立刻听歌时调用（例如：“播放晴天”、“放一首稻香”、“来一首七里香”）。直接播放最匹配的一首。
    :param song_name: 歌曲名称或关键词
    """
    global LAST_SEARCH_OPTIONS
    print(f"\n[DEBUG 收到点歌请求] 查找歌名: '{song_name}'", file=sys.stderr, flush=True)
    notify_log("MCP_CALL", f"收到点歌: '{song_name}'")

    # 兼容容错：如果用户在上一轮多选后回答“第1个”，而模型误调了 play_my_music
    choice_idx = parse_choice_index(song_name)
    if choice_idx is not None and LAST_SEARCH_OPTIONS and 0 <= choice_idx < len(LAST_SEARCH_OPTIONS):
        sel = LAST_SEARCH_OPTIONS[choice_idx]
        print(f"[DEBUG 自动重定向] 识别到序号选择: {song_name} -> 播放候选《{sel['title']}》\n", file=sys.stderr, flush=True)
        return play_selected_song(sel["video_id"], sel["title"])

    # 1. 优先在本地私有曲库中查找（必须同时确认本地文件实际存在）
    cleaned_name = clean_song_query(song_name)
    for name, info in MUSIC_LIBRARY.items():
        if name.lower() in song_name.lower() or name.lower() in cleaned_name.lower():
            fname = Path(info["url"]).name
            fpath = ROOT_DIR / fname
            if fpath.exists():
                print(f"[DEBUG 命中私有曲库] 《{name}》- {info['artist']} -> {info['url']}\n", file=sys.stderr, flush=True)
                notify_log("LOCAL_HIT", f"命中本地曲库: 《{name}》({info['artist']})")
                return json.dumps(make_music_result(name, info["artist"], info["url"]), ensure_ascii=False)
            else:
                print(f"[DEBUG 忽略私有曲库条目] 《{name}》本地文件 {fname} 缺失，自动转入全网检索！", file=sys.stderr, flush=True)

    # 2. 本地曲库未命中，自动无缝检索 YouTube 进行实时流式点播 (方案 A)
    print(f"[DEBUG 私有曲库未命中] 自动启动全网 YouTube 检索与实时流式缓冲: '{song_name}'\n", file=sys.stderr, flush=True)
    res = search_youtube_and_stream(song_name)
    return json.dumps(res, ensure_ascii=False)

# --- 功能 4：专用的 YouTube 直接点播工具 ---
@mcp.tool()
def search_and_play_youtube(song_name: str) -> str:
    """
    【YouTube 直接点歌】专门用于在 YouTube 上搜索音乐并直接秒播第一首。
    :param song_name: 想要搜索并播放的 YouTube 歌曲名或艺术家
    """
    print(f"\n[DEBUG 收到 YouTube 点歌] '{song_name}'", file=sys.stderr, flush=True)
    res = search_youtube_and_stream(song_name)
    return json.dumps(res, ensure_ascii=False)

# --- 功能 5：查看最近播放/推荐歌单 ---
@mcp.tool()
def list_music_library() -> str:
    """
    【查看最近歌单/常听历史】当用户询问“有什么歌”、“你能放什么歌”、“列出歌单”、“播放历史”、“最近听了什么”、“推荐歌单”时调用。
    返回用户最近点播和缓存的历史歌曲列表，供用户重温或挑选播放。
    """
    global LAST_SEARCH_OPTIONS
    print(f"\n[DEBUG 收到查询歌单请求]", file=sys.stderr, flush=True)
    notify_log("MCP_CALL", "查询最近歌单与历史")

    meta = load_metadata()
    recent_songs = []

    # 1. 扫描本地已缓存的完整 MP3 文件
    if DOWNLOADS_DIR.exists():
        cached_files = sorted(
            [p for p in DOWNLOADS_DIR.glob("*.mp3") if p.is_file()],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        for p in cached_files:
            vid = p.stem
            info = meta.get(vid, {})
            title = info.get("title", "")
            channel = info.get("channel", "YouTube 点播")
            if not title:
                title = f"缓存歌曲 ({vid})"
            recent_songs.append({
                "title": title,
                "channel": channel,
                "video_id": vid,
                "url": f"{BASE_URL}/stream/{vid}.mp3"
            })

    # 2. 追加本地精选常驻曲目（如晴天、稻香）
    for name, info in MUSIC_LIBRARY.items():
        fname = Path(info["url"]).name
        if (ROOT_DIR / fname).exists():
            if not any(s["title"] == name or name in s["title"] for s in recent_songs):
                recent_songs.append({
                    "title": f"{name} - {info['artist']}",
                    "channel": info["artist"],
                    "video_id": "",
                    "url": info["url"]
                })

    display_songs = recent_songs[:6]
    if not display_songs:
        return json.dumps({
            "status": "success",
            "message": "当前暂无本地常听歌曲。但我支持点播全网任意 YouTube 音乐，你可以直接告诉我你想听什么！",
            "instruction": "请告诉用户当前曲库为空，但随时可点播全网任何音乐，询问用户想听谁的歌。"
        }, ensure_ascii=False)

    # 存入选择缓存，以便用户立即说“播放第一首”时能生效
    options = []
    for idx, s in enumerate(display_songs):
        options.append({
            "index": idx + 1,
            "title": s["title"],
            "channel": s["channel"],
            "video_id": s["video_id"],
            "url": s.get("url", "")
        })
    LAST_SEARCH_OPTIONS = options

    readable_list = [f"{idx+1}. {s['title']}" for idx, s in enumerate(display_songs)]
    print(f"[DEBUG 导出最近歌单] 共 {len(display_songs)} 首歌曲: {readable_list}", file=sys.stderr, flush=True)

    return json.dumps({
        "status": "success",
        "total": len(display_songs),
        "songs": options,
        "message": f"最近点播与常听的歌曲有：{'; '.join(readable_list)} 等。",
        "instruction": (
            f"请用自然的口吻向用户介绍最近常听的歌曲列表（例如：'你最近点播过的歌曲有：{readable_list[0]}、{readable_list[1]} 等。你想听哪一首，或者想点播其他新歌？'）。"
            "此时切勿调用播放工具，等待用户回复第几首或指定歌名后再调用 play_selected_song 或 play_my_music 播放。"
        )
    }, ensure_ascii=False)

# --- 功能 6：随机播放一首歌曲 ---
@mcp.tool()
def play_random_music() -> str:
    """
    【随机点歌】当用户说“随便放首歌”、“推荐一首歌”、“随机播放音乐”、“来点音乐”时调用。
    从用户最近常听/缓存的曲库中随机抽选一首播放。
    """
    global LAST_SEARCH_OPTIONS
    print(f"\n[DEBUG 收到随机播放请求]", file=sys.stderr, flush=True)
    notify_log("MCP_CALL", "随机播放音乐")

    meta = load_metadata()
    candidates = []

    # 1. 收集本地已缓存的 MP3
    if DOWNLOADS_DIR.exists():
        for p in DOWNLOADS_DIR.glob("*.mp3"):
            if p.is_file():
                info = meta.get(p.stem, {})
                title = info.get("title", f"音乐 {p.stem}")
                channel = info.get("channel", "YouTube")
                candidates.append({
                    "title": title,
                    "channel": channel,
                    "video_id": p.stem,
                    "url": f"{BASE_URL}/stream/{p.stem}.mp3"
                })

    # 2. 收集本地精选
    for name, info in MUSIC_LIBRARY.items():
        fname = Path(info["url"]).name
        if (ROOT_DIR / fname).exists():
            candidates.append({
                "title": f"{name} - {info['artist']}",
                "channel": info["artist"],
                "video_id": "",
                "url": info["url"]
            })

    if not candidates:
        # 如果什么缓存都没有，默认点播周杰伦
        return play_my_music("周杰伦 晴天")

    chosen = random.choice(candidates)
    print(f"[DEBUG 随机抽取] 抽中《{chosen['title']}》 -> {chosen['url']}\n", file=sys.stderr, flush=True)
    notify_log("HIT", f"随机抽中: {chosen['title']}")
    return json.dumps(make_music_result(chosen["title"], chosen["channel"], chosen["url"]), ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()
