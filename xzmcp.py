import warnings
warnings.filterwarnings("ignore", message=".*IncompleteFieldDefinitionWarning.*")
warnings.filterwarnings("ignore", message=".*lifespan.*")
import sys
import json
import random
from fastmcp import FastMCP

# 1. 初始化 MCP 服务
mcp = FastMCP("XiaozhiPrivateMusicServer")

# 2. 私有曲库数据。URL 必须能被 ESP32 直接访问并返回 MP3 数据。
MUSIC_LIBRARY = {
    "晴天": {
        "url": "http://192.168.50.220:8111/qingtian.mp3",
        "artist": "周杰伦",
        "genre": "流行"
    },
    "稻香": {
        "url": "http://192.168.50.220:8111/daoxiang.mp3",
        "artist": "周杰伦",
        "genre": "流行"
    },
    "小燕子": {
        "url": "http://music.sunjw.cn/qingtian.mp3",
        "artist": "未知",
        "genre": "未知"
    },
    "夜曲": {
        "url": "http://192.168.50.220:8111/yequ.mp3",
        "artist": "周杰伦",
        "genre": "流行"
    },
    "海阔天空": {
        "url": "http://192.168.50.220:8111/haikuotiankong.mp3",
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

# --- 功能 1：根据歌名点歌播放 ---
@mcp.tool()
def play_my_music(song_name: str) -> str:
    """
    【指定点歌】当用户明确指定歌名想要听某首歌时调用。
    :param song_name: 歌曲名称，例如：晴天、稻香、夜曲
    """
    print(f"\n[DEBUG 收到点歌请求] 查找歌名: '{song_name}'", file=sys.stderr, flush=True)
    for name, info in MUSIC_LIBRARY.items():
        if song_name.lower() in name.lower() or name.lower() in song_name.lower():
            print(f"[DEBUG 匹配成功] 《{name}》- {info['artist']} -> {info['url']}\n", file=sys.stderr, flush=True)
            return json.dumps(make_music_result(name, info["artist"], info["url"]), ensure_ascii=False)
            
    print(f"[DEBUG 查找失败] 未找到《{song_name}》\n", file=sys.stderr, flush=True)
    return json.dumps({
        "status": "not_found",
        "message": f"私有曲库中未找到《{song_name}》"
    }, ensure_ascii=False)

# --- 功能 2：列出曲库中的所有歌曲 ---
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
        "message": f"曲库中共有 {len(songs_list)} 首歌曲：{', '.join([s['song'] for s in songs_list])}"
    }, ensure_ascii=False)

# --- 功能 3：随机播放一首歌曲 ---
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

