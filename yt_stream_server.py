import os
import sys
import json
import time
import shutil
import threading
import subprocess
import urllib.parse
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = ROOT_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

MAX_CACHED_SONGS = 40

def cleanup_old_downloads(max_count: int = MAX_CACHED_SONGS):
    """当下载的音乐超过 max_count 首时，删除最早下载的文件，最多保留 max_count 首"""
    try:
        mp3_files = [p for p in DOWNLOADS_DIR.glob("*.mp3") if p.is_file()]
        if len(mp3_files) > max_count:
            # 按照修改时间从小到大排序（最旧的排在最前）
            mp3_files.sort(key=lambda p: p.stat().st_mtime)
            remove_count = len(mp3_files) - max_count
            for p in mp3_files[:remove_count]:
                try:
                    p.unlink(missing_ok=True)
                    print(f"[Cache Cleanup] 缓存歌曲超过 {max_count} 首，已自动清理最早下载的文件: {p.name}")
                except Exception as err:
                    print(f"[Cache Cleanup Error] 无法删除文件 {p.name}: {err}")
    except Exception as e:
        print(f"[Cache Cleanup Error] 检查清理缓存失败: {e}")

def cleanup_stale_temp_files(max_age_seconds: int = 3600):
    """清理异常中断残留的临时 .tmp 文件"""
    try:
        now = time.time()
        for tmp_p in DOWNLOADS_DIR.glob("*.tmp"):
            if tmp_p.is_file() and (now - tmp_p.stat().st_mtime) > max_age_seconds:
                tmp_p.unlink(missing_ok=True)
    except Exception:
        pass

def find_ffmpeg() -> str:
    f = shutil.which("ffmpeg")
    if f:
        return f
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        matches = list(Path(local_app_data).glob("**/ffmpeg.exe"))
        if matches:
            return str(matches[0])
    return "ffmpeg"

FFMPEG_EXE = find_ffmpeg()

class StreamSession:
    """管理单首歌曲的 YouTube 下载与 MP3 实时流式转码"""
    def __init__(self, video_id: str):
        self.video_id = video_id
        self.chunks = []
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.done = False
        self.error = None
        self.total_bytes = 0
        self.cache_file = DOWNLOADS_DIR / f"{video_id}.mp3"
        self.tmp_file = DOWNLOADS_DIR / f"{video_id}.mp3.tmp"
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _worker(self):
        url = f"https://www.youtube.com/watch?v={self.video_id}"
        p1 = None
        p2 = None
        tmp_fp = None
        try:
            tmp_fp = open(self.tmp_file, "wb")
            print(f"[Stream] 启动 YouTube 实时音频流: {self.video_id}")
            # p1: yt-dlp 抓取最高音频流输出到 stdout
            p1 = subprocess.Popen([
                sys.executable, "-m", "yt_dlp",
                "--extractor-args", "youtube:player_client=android",
                "-f", "bestaudio/best",
                "-o", "-",
                url
            ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            # p2: ffmpeg 实时转码为标准 MP3 (128kbps) 输出到 stdout
            p2 = subprocess.Popen([
                FFMPEG_EXE,
                "-i", "pipe:0",
                "-vn",
                "-codec:a", "libmp3lame",
                "-b:a", "128k",
                "-f", "mp3",
                "pipe:1"
            ], stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            p1.stdout.close()

            while True:
                data = p2.stdout.read(16384)
                if not data:
                    break
                tmp_fp.write(data)
                tmp_fp.flush()
                with self.lock:
                    self.chunks.append(data)
                    self.total_bytes += len(data)
                    self.cv.notify_all()

            p2.wait()
            tmp_fp.close()
            tmp_fp = None

            # 完整下载后重命名为正式缓存文件
            if os.path.exists(self.tmp_file) and os.path.getsize(self.tmp_file) > 10000:
                if os.path.exists(self.cache_file):
                    os.remove(self.cache_file)
                os.rename(self.tmp_file, self.cache_file)
                print(f"[Stream] 成功缓存整首歌曲: {self.cache_file.name} ({self.total_bytes} 字节)")
                cleanup_old_downloads(MAX_CACHED_SONGS)

        except Exception as e:
            self.error = str(e)
            print(f"[Stream Error] {self.video_id}: {e}")
        finally:
            if tmp_fp:
                try: tmp_fp.close()
                except Exception: pass
            if p2:
                try: p2.terminate()
                except Exception: pass
            if p1:
                try: p1.terminate()
                except Exception: pass
            with self.lock:
                self.done = True
                self.cv.notify_all()

    def wait_for_buffer(self, min_bytes=65536, timeout=8.0) -> bool:
        """等待缓冲达到指定字节数 (默认 64KB 约 4-5 秒播放长度)"""
        start = time.time()
        with self.lock:
            while self.total_bytes < min_bytes and not self.done and not self.error:
                elapsed = time.time() - start
                remaining = timeout - elapsed
                if remaining <= 0:
                    break
                self.cv.wait(timeout=remaining)
            return self.total_bytes >= min_bytes or (self.done and self.total_bytes > 0)

class StreamManager:
    def __init__(self):
        self.sessions: dict[str, StreamSession] = {}
        self.lock = threading.Lock()

    def get_or_create(self, video_id: str) -> StreamSession:
        with self.lock:
            # 如果本地已有完整文件，不需要启动流管道
            if (DOWNLOADS_DIR / f"{video_id}.mp3").exists():
                return None
            if video_id not in self.sessions or (self.sessions[video_id].done and not (DOWNLOADS_DIR / f"{video_id}.mp3").exists()):
                self.sessions[video_id] = StreamSession(video_id)
            return self.sessions[video_id]

stream_manager = StreamManager()

class MusicStreamHandler(SimpleHTTPRequestHandler):
    """同时支持静态文件、预缓冲接口与 HTTP 1.1 Chunked 实时推流"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/stream") or parsed.path == "/api/prepare":
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg" if parsed.path.startswith("/stream") else "application/json")
            self.end_headers()
            return
        super().do_HEAD()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 1. 预热/预缓冲接口 (供 MCP 调用)
        if path == "/api/prepare":
            qs = urllib.parse.parse_qs(parsed.query)
            video_id = qs.get("v", [""])[0].strip()
            if not video_id:
                self.send_error(400, "Missing videoId")
                return
            
            cache_file = DOWNLOADS_DIR / f"{video_id}.mp3"
            if cache_file.exists():
                self._send_json({"ok": True, "cached": True, "videoId": video_id})
                return

            session = stream_manager.get_or_create(video_id)
            # 等待几秒预缓冲
            ready = session.wait_for_buffer(min_bytes=65536, timeout=6.0) if session else True
            self._send_json({
                "ok": True,
                "cached": False,
                "videoId": video_id,
                "buffered_bytes": session.total_bytes if session else cache_file.stat().st_size,
                "ready": ready
            })
            return

        # 2. 音频推流接口: /stream/<video_id>.mp3
        if path.startswith("/stream/") or path == "/stream":
            self.handle_youtube_stream(parsed)
            return

        # 3. 静态文件处理 (如本地 /qingtian.ogg, /daoxiang.mp3 等)
        super().do_GET()

    def _send_json(self, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_youtube_stream(self, parsed):
        video_id = None
        if parsed.path.startswith("/stream/"):
            raw = parsed.path[len("/stream/"):]
            if raw.endswith(".mp3") or raw.endswith(".ogg"):
                raw = raw.rsplit(".", 1)[0]
            video_id = raw.strip()
        else:
            qs = urllib.parse.parse_qs(parsed.query)
            video_id = qs.get("v", [""])[0].strip()

        if not video_id:
            self.send_error(400, "Missing videoId")
            return

        cache_file = DOWNLOADS_DIR / f"{video_id}.mp3"
        # A. 命中本地缓存，直接以静态文件高效发送
        if cache_file.exists():
            print(f"[HTTP] 命中本地完整缓存，秒级发送: {cache_file.name}")
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(cache_file.stat().st_size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(cache_file, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        # B. 动态分块推流 (Transfer-Encoding: chunked)
        print(f"[HTTP] 建立分块流式连接 (Chunked): {video_id}")
        session = stream_manager.get_or_create(video_id)
        if session:
            # 确保至少有 32KB 初始缓冲
            session.wait_for_buffer(min_bytes=32768, timeout=8.0)

        self.protocol_version = "HTTP/1.1"
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        idx = 0
        try:
            while session:
                chunk = None
                with session.lock:
                    if idx < len(session.chunks):
                        chunk = session.chunks[idx]
                        idx += 1
                    elif session.done:
                        break
                    else:
                        session.cv.wait(timeout=1.0)
                        if idx < len(session.chunks):
                            chunk = session.chunks[idx]
                            idx += 1
                        elif session.done:
                            break

                if chunk:
                    chunk_hdr = f"{len(chunk):X}\r\n".encode("ascii")
                    self.wfile.write(chunk_hdr + chunk + b"\r\n")
                    self.wfile.flush()

            # 发送 chunked 终止块
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            print(f"[HTTP] 分块推流完成: {video_id}")
        except (BrokenPipeError, ConnectionResetError):
            print(f"[HTTP] 客户端断开连接: {video_id}")
        except Exception as e:
            print(f"[HTTP Stream Error] {e}")

def run_server(host="0.0.0.0", port=8111):
    cleanup_stale_temp_files()
    cleanup_old_downloads(MAX_CACHED_SONGS)
    server = ThreadingHTTPServer((host, port), MusicStreamHandler)
    print(f"=====================================================")
    print(f"[MusicStreamServer] 运行中: http://{host}:{port}")
    print(f"[MusicStreamServer] 根目录: {ROOT_DIR}")
    print(f"[MusicStreamServer] 缓存目录: {DOWNLOADS_DIR}")
    print(f"=====================================================")
    server.serve_forever()

if __name__ == "__main__":
    port = 8111
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run_server(port=port)
