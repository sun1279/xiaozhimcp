import warnings
warnings.filterwarnings("ignore", message=".*IncompleteFieldDefinitionWarning.*")
warnings.filterwarnings("ignore", message=".*lifespan.*")
import os
import sys
import json
import time
import random
import threading
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
        "url": f"{BASE_URL}/stream/qingtian.mp3",
        "artist": "周杰伦",
        "genre": "流行"
    },
    "稻香": {
        "url": f"{BASE_URL}/stream/daoxiang.mp3",
        "artist": "周杰伦",
        "genre": "流行"
    },
    "小燕子": {
        "url": f"{BASE_URL}/stream/xiaoyanzi.mp3",
        "artist": "未知",
        "genre": "未知"
    },
    "夜曲": {
        "url": f"{BASE_URL}/stream/yequ.mp3",
        "artist": "周杰伦",
        "genre": "流行"
    },
    "海阔天空": {
        "url": f"{BASE_URL}/stream/haikuotiankong.mp3",
        "artist": "Beyond",
        "genre": "摇滚"
    }
}

ROOT_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = ROOT_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
METADATA_FILE = DOWNLOADS_DIR / "metadata.json"
CURRENT_PLAYING_FILE = DOWNLOADS_DIR / "current_playing.json"
CURRENT_PLAYING_INFO = None

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

def get_current_playing_record() -> dict:
    global CURRENT_PLAYING_INFO
    if CURRENT_PLAYING_INFO:
        return CURRENT_PLAYING_INFO
    if CURRENT_PLAYING_FILE.exists():
        try:
            with open(CURRENT_PLAYING_FILE, "r", encoding="utf-8") as f:
                CURRENT_PLAYING_INFO = json.load(f)
                if CURRENT_PLAYING_INFO:
                    return CURRENT_PLAYING_INFO
        except Exception:
            pass
    meta = load_metadata()
    if meta:
        try:
            latest_id = max(meta.keys(), key=lambda k: meta[k].get("timestamp", 0))
            item = meta[latest_id]
            return {
                "song_name": item.get("title", "未知歌曲"),
                "artist": item.get("channel", "未知歌手"),
                "audio_url": f"{BASE_URL}/stream/{latest_id}.mp3",
                "time": item.get("time", ""),
                "timestamp": item.get("timestamp", 0)
            }
        except Exception:
            pass
    return None

def set_current_playing_record(song_name: str, artist: str, audio_url: str):
    global CURRENT_PLAYING_INFO
    CURRENT_PLAYING_INFO = {
        "song_name": song_name,
        "artist": artist,
        "audio_url": audio_url,
        "time": time.strftime("%H:%M:%S", time.localtime()),
        "timestamp": time.time()
    }
    try:
        with open(CURRENT_PLAYING_FILE, "w", encoding="utf-8") as f:
            json.dump(CURRENT_PLAYING_INFO, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Current Playing Error] {e}", file=sys.stderr, flush=True)

def make_music_result(song_name: str, artist: str, audio_url: str) -> dict:
    """Return a URL for the model to pass to the device playback tool."""
    set_current_playing_record(song_name, artist, audio_url)
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

SEARCH_CACHE_ALL_ITEMS = []
SEARCH_CACHE_PAGE = 0
SEARCH_PAGE_SIZE = 5
CURRENT_SEARCH_QUERY = ""

def format_page_options(query: str, page_idx: int) -> dict:
    global LAST_SEARCH_OPTIONS, SEARCH_CACHE_ALL_ITEMS, SEARCH_CACHE_PAGE
    start = page_idx * SEARCH_PAGE_SIZE
    end = start + SEARCH_PAGE_SIZE
    page_items = SEARCH_CACHE_ALL_ITEMS[start:end]

    if not page_items:
        return {
            "status": "end_of_results",
            "message": f"关于《{query}》的所有精选版本都已经浏览完毕了，您可以尝试更具体的歌名搜索！",
            "instruction": "请告知用户已经看完了所有相关版本，询问用户想听哪一首，或者想换什么新歌搜索。"
        }

    SEARCH_CACHE_PAGE = page_idx
    options = []
    for idx, item in enumerate(page_items):
        options.append({
            "index": idx + 1,
            "title": item["title"],
            "channel": item["channel"],
            "video_id": item["video_id"]
        })
    LAST_SEARCH_OPTIONS = options

    page_num = page_idx + 1
    total_pages = (len(SEARCH_CACHE_ALL_ITEMS) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE
    notify_log("HIT", f"展示第 {page_num}/{total_pages} 批 (本批 {len(options)} 首, 候选池 {len(SEARCH_CACHE_ALL_ITEMS)} 首)")

    readable_list = [f"{idx+1}. {opt['title']}" for idx, opt in enumerate(options)]
    
    return {
        "status": "multiple_results",
        "query": query,
        "batch_page": page_num,
        "total_batches": total_pages,
        "options_count": len(options),
        "total_pool": len(SEARCH_CACHE_ALL_ITEMS),
        "options": options,
        "instruction": (
            f"【播报指引】全网共搜到了海量版本，已为用户精选出第 {page_num} 批候选（共 5 首）：\n"
            f"{'; '.join(readable_list)}。\n"
            "【语言表达要求】请用亲切自然的口语告诉用户：'在全网为您找到了许多相关版本，精选推荐前 5 个：1. xxx 2. xxx ... 5. xxx。您想听第几个？如果都不喜欢，也可以对我说【换一批】。'\n"
            "【严禁】切勿在此时调用设备播放工具 self.audio.play_url，等待用户回答序号（如“第1个”、“放第二个”）、说出歌名或说【换一批】。"
        )
    }

# --- 功能 1：搜索歌曲候选列表（默认提供 5 个精选结果供用户语音挑选） ---
@mcp.tool()
def search_music_options(query: str, count: int = 5) -> str:
    """
    【搜索歌曲/候选版本挑选】当用户要求“搜索歌曲”、“找找某某的歌”、“搜一下xxx”、“有哪些版本”、“查一下某歌”或泛指歌手名时调用。
    全网检索海量版本，默认精选推荐前 5 个版本念给用户听并等待用户挑选；用户也可说“换一批”。
    :param query: 想要搜索的歌曲关键词、歌名或歌手名
    :param count: 候选数量，默认 5 首
    """
    global SEARCH_CACHE_ALL_ITEMS, SEARCH_CACHE_PAGE, CURRENT_SEARCH_QUERY
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
    try:
        # 一次性预取 15 个候选项入池，以便用户说“换一批”时秒级切换（每批 5 首，共 3 批）
        items = search_videos(cleaned_q, api_key, max_results=15)
        if not items and cleaned_q != query:
            items = search_videos(query, api_key, max_results=15)

        if not items:
            notify_log("NOT_FOUND", f"未找到候选: '{query}'")
            return json.dumps({"status": "not_found", "message": f"在 YouTube 上未找到与《{query}》相关的歌曲"}, ensure_ascii=False)

        pool = []
        for idx, item in enumerate(items):
            vid = item.get("id", {}).get("videoId", "")
            snip = item.get("snippet", {})
            title = snip.get("title", f"选项 {idx+1}")
            channel = snip.get("channelTitle", "")
            if vid:
                pool.append({
                    "title": title,
                    "channel": channel,
                    "video_id": vid
                })

        SEARCH_CACHE_ALL_ITEMS = pool
        CURRENT_SEARCH_QUERY = query
        SEARCH_CACHE_PAGE = 0

        res = format_page_options(query, 0)
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:
        print(f"[YouTube 候选搜索异常] {e}", file=sys.stderr, flush=True)
        notify_log("ERROR", f"候选搜索异常: {e}")
        return json.dumps({"status": "error", "message": f"搜索异常: {e}"}, ensure_ascii=False)

# --- 功能 2：换一批候选歌曲 ---
@mcp.tool()
def next_music_options() -> str:
    """
    【换一批歌曲候选】当用户对上一批候选不满意，说“换一批”、“下一批”、“换一组”、“还有其他的吗”、“再找找”、“看后面的”时调用。
    自动切换展示后续的 5 首精选版本念给用户听。
    """
    global SEARCH_CACHE_ALL_ITEMS, SEARCH_CACHE_PAGE, CURRENT_SEARCH_QUERY
    print(f"\n[DEBUG 换一批候选请求] 当前页: {SEARCH_CACHE_PAGE}, 备选池: {len(SEARCH_CACHE_ALL_ITEMS)} 首", file=sys.stderr, flush=True)
    notify_log("MCP_CALL", "换一批歌曲候选")

    if not SEARCH_CACHE_ALL_ITEMS:
        return json.dumps({
            "status": "no_previous_search",
            "message": "当前还没有搜索过歌曲哦，请先告诉我你想搜索什么歌，例如‘搜一下周杰伦的歌’！",
            "instruction": "告知用户还没有进行过搜索，询问用户想搜哪位歌手或哪首歌。"
        }, ensure_ascii=False)

    next_page = SEARCH_CACHE_PAGE + 1
    total_pages = (len(SEARCH_CACHE_ALL_ITEMS) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE

    if next_page >= total_pages:
        # 已到末尾，循环回第 1 批
        SEARCH_CACHE_PAGE = 0
        res = format_page_options(CURRENT_SEARCH_QUERY, 0)
        res["instruction"] = (
            "所有候选版本已经全部浏览完毕，已为您重新循环回到第 1 批精选推荐。"
            "请告诉用户：'已经为您浏览完全部候选，已为您转回第 1 批推荐，您想听哪一个，或者想换个新歌？'"
        )
        return json.dumps(res, ensure_ascii=False)

    res = format_page_options(CURRENT_SEARCH_QUERY, next_page)
    return json.dumps(res, ensure_ascii=False)

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
    # 兼容容错 0.01：如果用户询问“你在播什么”/“这是什么歌”/“刚才放的什么”
    if any(q in song_name for q in ["在播什么", "播什么歌", "放的什么歌", "现在放的是什么", "这是什么歌", "这是哪首歌", "这是啥歌", "现在播什么", "刚才放的什么", "刚刚放的是什么", "歌名是什么", "这是什么音乐", "刚才那首歌", "唱的什么"]):
        print(f"[DEBUG 自动重定向] 识别到查询歌曲指令: '{song_name}' -> 触发 get_now_playing()\n", file=sys.stderr, flush=True)
        return get_now_playing()

    # 兼容容错 0.02：如果用户说“继续播放”/“接着放”/“恢复播放”
    cleaned_strip = song_name.strip()
    if cleaned_strip in ["继续", "接着", "继续播放", "接着放", "继续放", "恢复播放", "接着听", "继续听", "接着播"] or any(q in song_name for q in ["继续播放", "接着放", "继续放", "恢复播放", "接着听", "继续听", "接着播"]):
        print(f"[DEBUG 自动重定向] 识别到续播指令: '{song_name}' -> 触发 resume_music()\n", file=sys.stderr, flush=True)
        return resume_music()

    # 兼容容错 0：如果包含定时关闭指令（如“30分钟后停止”、“半小时后关音乐”）
    if any(k in song_name for k in ["定时", "分钟后", "小时后", "半小时", "分钟停止"]):
        import re
        m = re.search(r'(\d+)\s*(?:分钟|分)', song_name)
        if m:
            return set_sleep_timer(int(m.group(1)))
        if "半小时" in song_name:
            return set_sleep_timer(30)
        if "一小时" in song_name or "1小时" in song_name:
            return set_sleep_timer(60)

    # 兼容容错 0.1：如果用户说“取消定时”
    if any(phrase in song_name for phrase in ["取消定时", "取消睡眠", "不要定时"]):
        return cancel_sleep_timer()

    # 兼容容错 0.2：如果用户说“停止播放”/“别放了”/“关掉音乐”/“暂停播放”
    if any(phrase in song_name for phrase in ["停止", "别放了", "关掉音乐", "不放了", "别唱了", "不要放歌", "暂停"]):
        print(f"[DEBUG 自动重定向] 识别到停止指令: '{song_name}' -> 触发 stop_music()\n", file=sys.stderr, flush=True)
        return stop_music()

    # 兼容容错 1：如果用户说“换一批”，而模型误调了 play_my_music
    if any(phrase in song_name for phrase in ["换一批", "下一批", "换一组", "下一页", "更多版本", "其他版本", "还有吗", "还有别的吗"]):
        print(f"[DEBUG 自动重定向] 识别到换一批指令: '{song_name}' -> 触发 next_music_options()\n", file=sys.stderr, flush=True)
        return next_music_options()

    # 兼容容错 2：如果用户在上一轮多选后回答“第1个”，而模型误调了 play_my_music
    choice_idx = parse_choice_index(song_name)
    if choice_idx is not None and LAST_SEARCH_OPTIONS and 0 <= choice_idx < len(LAST_SEARCH_OPTIONS):
        sel = LAST_SEARCH_OPTIONS[choice_idx]
        print(f"[DEBUG 自动重定向] 识别到序号选择: {song_name} -> 播放候选《{sel['title']}》\n", file=sys.stderr, flush=True)
        return play_selected_song(sel.get("video_id") or "1", sel["title"])

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

# --- 睡眠定时与播放控制状态 ---
SLEEP_TIMER = None
SLEEP_TIMER_END = 0.0
SLEEP_TIMER_LOCK = threading.Lock()

def _sleep_timer_trigger():
    global SLEEP_TIMER, SLEEP_TIMER_END
    print("[Sleep Timer] 睡眠定时倒计时到期，正在切断推流...", file=sys.stderr, flush=True)
    notify_log("TIMER", "⏰ 睡眠定时到期，已自动停止音乐播放")
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{SERVER_PORT}/api/stop_stream")
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception as e:
        print(f"[Sleep Timer Stop Error] {e}", file=sys.stderr, flush=True)
    with SLEEP_TIMER_LOCK:
        SLEEP_TIMER = None
        SLEEP_TIMER_END = 0.0

# --- 功能 7：设置定时停止播放（睡眠定时） ---
@mcp.tool()
def set_sleep_timer(minutes: int) -> str:
    """
    【设置定时停止播放/睡眠模式】当用户说“xx分钟后停止播放”、“半小时后关掉音乐”、“20分钟后睡觉”、“定时关闭音乐”时调用。
    :param minutes: 倒计时分钟数，例如 15、20、30、60（“半小时”请传 30，“一小时”请传 60）
    """
    global SLEEP_TIMER, SLEEP_TIMER_END
    mins = max(1, int(minutes))
    seconds = mins * 60

    with SLEEP_TIMER_LOCK:
        if SLEEP_TIMER and SLEEP_TIMER.is_alive():
            SLEEP_TIMER.cancel()
        SLEEP_TIMER_END = time.time() + seconds
        SLEEP_TIMER = threading.Timer(seconds, _sleep_timer_trigger)
        SLEEP_TIMER.daemon = True
        SLEEP_TIMER.start()

    print(f"[DEBUG 设置睡眠定时] {mins} 分钟后自动停止播放", file=sys.stderr, flush=True)
    notify_log("TIMER", f"设置定时关闭: {mins} 分钟后")
    return json.dumps({
        "status": "success",
        "minutes": mins,
        "message": f"好的，已为你设置在 {mins} 分钟后自动停止播放音乐，祝你好梦！"
    }, ensure_ascii=False)

# --- 功能 8：取消定时停止播放 ---
@mcp.tool()
def cancel_sleep_timer() -> str:
    """
    【取消定时关闭】当用户说“取消定时”、“取消睡眠模式”、“别关音乐了”、“取消定时关机”时调用。
    """
    global SLEEP_TIMER, SLEEP_TIMER_END
    with SLEEP_TIMER_LOCK:
        if SLEEP_TIMER and SLEEP_TIMER.is_alive():
            SLEEP_TIMER.cancel()
            SLEEP_TIMER = None
            SLEEP_TIMER_END = 0.0
            print("[DEBUG 取消睡眠定时] 已成功取消", file=sys.stderr, flush=True)
            notify_log("TIMER", "已取消定时关闭")
            return json.dumps({
                "status": "success",
                "message": "已为你取消睡眠定时，音乐将继续播放。"
            }, ensure_ascii=False)

    return json.dumps({
        "status": "not_active",
        "message": "当前没有正在运行的睡眠定时任务。"
    }, ensure_ascii=False)

# --- 功能 9：查询定时剩余时间 ---
@mcp.tool()
def get_sleep_timer_status() -> str:
    """
    【查询定时剩余时间】当用户问“还有多久关音乐”、“定时还剩几分钟”、“什么时候停止播放”时调用。
    """
    global SLEEP_TIMER, SLEEP_TIMER_END
    with SLEEP_TIMER_LOCK:
        if SLEEP_TIMER and SLEEP_TIMER.is_alive() and SLEEP_TIMER_END > time.time():
            remaining_secs = int(SLEEP_TIMER_END - time.time())
            rem_mins = max(1, round(remaining_secs / 60))
            return json.dumps({
                "status": "active",
                "remaining_minutes": rem_mins,
                "remaining_seconds": remaining_secs,
                "message": f"音乐将在大约 {rem_mins} 分钟后自动停止播放。"
            }, ensure_ascii=False)

    return json.dumps({
        "status": "inactive",
        "message": "当前没有设置睡眠定时哦。"
    }, ensure_ascii=False)

# --- 功能 10：立即停止/暂停播放音乐 ---
@mcp.tool()
def stop_music() -> str:
    """
    【立即停止/暂停播放音乐】当用户明确说“停止播放”、“别放了”、“关掉音乐”、“暂停播放”、“不要放歌了”时调用。
    """
    print("[DEBUG 立即停止播放]", file=sys.stderr, flush=True)
    notify_log("STOP", "立即停止音乐播放")
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{SERVER_PORT}/api/stop_stream")
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception as e:
        print(f"[Stop Music Request Error] {e}", file=sys.stderr, flush=True)

    return json.dumps({
        "status": "success",
        "message": "已为你停止播放音乐。如需继续收听，可随时对我说“继续播放”。"
    }, ensure_ascii=False)

# --- 功能 11：查询当前/刚刚播放的歌曲 ---
@mcp.tool()
def get_now_playing() -> str:
    """
    【查询当前正在播放/刚刚播放的歌曲】当用户询问“你在播什么”、“现在播的是什么歌”、“这是什么歌”、“刚才放的是什么”、“正在播放什么”、“刚才那首歌叫什么”等问题时调用。
    """
    rec = get_current_playing_record()
    if not rec or not rec.get("song_name"):
        notify_log("NOW_PLAYING", "查询当前播放：暂无记录")
        return json.dumps({
            "status": "idle",
            "message": "当前没有正在播放的歌曲，也没有刚才播放的记录。你可以随时告诉我你想听什么歌。"
        }, ensure_ascii=False)

    song = rec["song_name"]
    artist = rec.get("artist", "")
    t = rec.get("time", "")
    info_msg = f"当前（或刚才播放）的歌曲是《{song}》"
    if artist:
        info_msg += f" - {artist}"
    if t:
        info_msg += f"（于 {t} 开始播放）"
    info_msg += "。如果你刚才打断了我，可以说“继续播放”来继续收听。"

    notify_log("NOW_PLAYING", f"查询当前播放 -> 《{song}》- {artist}")
    return json.dumps({
        "status": "playing_or_paused",
        "song_name": song,
        "artist": artist,
        "play_time": t,
        "message": info_msg
    }, ensure_ascii=False)

# --- 功能 12：继续播放/恢复播放 ---
@mcp.tool()
def resume_music() -> str:
    """
    【继续播放/恢复播放】当用户在被打断或暂停后想要继续听歌，说“继续播放”、“接着放”、“恢复播放”、“继续听”、“继续”时调用。
    """
    rec = get_current_playing_record()
    if not rec or not rec.get("audio_url"):
        notify_log("RESUME", "恢复播放失败：无上一首记录")
        return json.dumps({
            "status": "error",
            "message": "没有找到刚才播放的歌曲记录，请直接告诉我你想听什么歌。"
        }, ensure_ascii=False)

    song = rec["song_name"]
    artist = rec.get("artist", "")
    url = rec["audio_url"]
    notify_log("RESUME", f"恢复播放 -> 《{song}》- {artist}")

    res = make_music_result(song, artist, url)
    res["message"] = f"继续为你播放《{song}》" + (f" - {artist}" if artist else "")
    return json.dumps(res, ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()
