"""影片與聲音:讀長度與畫面大小、抽出聲音(播放用 + 波形)、找人聲邊界、播放聲音、背景解出小畫面。

時間以聲音為準:畫面跟不上時跳過幾格,聲音和字幕不會跑掉。
"""

import hashlib
import json
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import pygame

from core import deps

BIN_RATE = 100                  # 波形每秒幾格(一格 10 毫秒)
PCM_RATE = 8000                 # 算波形用的取樣率
PLAY_RATE = 32000               # 播放用的聲音(只是對字幕,說話聽得清楚就好,檔案小)
FRAME_W = 640                   # 解出來的畫面寬度(只是對嘴型,不用原本畫質)
FRAME_FPS = 24
AHEAD = 6                       # 播放時最多先解好幾張畫面
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v", ".wmv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}


def ffmpeg():
    return str(deps.FFMPEG.path())


def ffprobe():
    return str(deps.FFMPEG.path("ffprobe.exe"))


def probe(path):
    """{"duration": 秒, "video": (寬, 高) 或 None, "audio": 有沒有聲音}。"""
    result = deps.run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                      capture_output=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError("讀不了這個檔案，可能不是影片或聲音")
    data = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    video, audio = None, False
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        disposition = stream.get("disposition") or {}
        if kind == "video" and video is None and not disposition.get("attached_pic"):
            w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
            rotate = str((stream.get("tags") or {}).get("rotate", "0"))
            for side in stream.get("side_data_list") or []:
                if "rotation" in side:
                    rotate = str(side["rotation"])
            if rotate.lstrip("-") in ("90", "270"):
                w, h = h, w
            if w and h:
                video = (w, h)
        elif kind == "audio":
            audio = True
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not duration:
        duration = max((float(s.get("duration") or 0) for s in data.get("streams", [])), default=0)
    return {"duration": duration, "video": video, "audio": audio}


def cache_key(path):
    path = Path(path)
    stat = path.stat()
    raw = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def prepare(path, cache_dir, duration, progress=None, cancel=None):
    """抽出播放用的聲音和波形,存在 cache_dir;已經有就直接用。回傳 (聲音檔, 波形 [峰值, 音量])。"""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key(path)
    sound, wave_file = cache_dir / f"{key}.ogg", cache_dir / f"{key}.npy"
    if sound.is_file() and wave_file.is_file():
        try:
            return sound, np.load(wave_file)
        except (OSError, ValueError):
            pass
    # 只留這一部的暫存(換影片時把上一部的刪掉,資料夾不會越來越大)
    for old in cache_dir.iterdir():
        if old.suffix in (".ogg", ".npy", ".tmp") and not old.name.startswith(key):
            try:
                old.unlink()
            except OSError:
                pass
    temp = cache_dir / f"{key}.ogg.tmp"
    args = [ffmpeg(), "-nostdin", "-v", "error", "-y", "-i", str(path), "-map", "0:a:0", "-ac", "1",
            "-ar", str(PLAY_RATE), "-c:a", "libvorbis", "-q:a", "4", "-f", "ogg", str(temp),
            "-map", "0:a:0", "-ac", "1", "-ar", str(PCM_RATE), "-f", "s16le", "pipe:1"]
    proc = deps.popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    step = PCM_RATE // BIN_RATE
    peaks, levels = [], []
    carry = np.zeros(0, np.int16)
    done = 0
    total = max(1, int(duration * PCM_RATE))
    try:
        while True:
            if cancel is not None and cancel.is_set():
                deps.kill_tree(proc)
                raise deps.Cancelled()
            chunk = proc.stdout.read(PCM_RATE * 2 * 5)
            if not chunk:
                break
            samples = np.concatenate([carry, np.frombuffer(chunk[:len(chunk) // 2 * 2], np.int16)])
            whole = len(samples) // step * step
            block = samples[:whole].reshape(-1, step).astype(np.float32) / 32768.0
            carry = samples[whole:]
            peaks.append(np.abs(block).max(axis=1))
            levels.append(np.sqrt((block ** 2).mean(axis=1)))
            done += len(chunk) // 2
            if progress:
                progress(min(0.99, done / total))
        error = proc.stderr.read().decode("utf-8", "replace").strip()
        if proc.wait() != 0 or not peaks:
            raise RuntimeError("抽不出聲音" + (f"：{error.splitlines()[-1]}" if error else "，這個檔案可能沒有聲音"))
    finally:
        if proc.poll() is None:
            deps.kill_tree(proc)
    wave = np.vstack([np.concatenate(peaks), np.concatenate(levels)]).astype(np.float32)
    temp.replace(sound)
    np.save(wave_file, wave)
    return sound, wave


def voice_regions(wave):
    """用音量找出有人聲的區間(秒):[(開始, 結束)];拖字幕時吸附到這些邊界。"""
    level = wave[1]
    if not len(level):
        return []
    db = 20 * np.log10(level + 1e-5)
    floor, loud = np.percentile(db, 10), np.percentile(db, 95)
    if loud - floor < 6:
        return []
    on = db > floor + (loud - floor) * 0.35
    regions = []
    start = None
    for i, value in enumerate(np.append(on, False)):
        if value and start is None:
            start = i
        elif not value and start is not None:
            if regions and start - regions[-1][1] < 0.2 * BIN_RATE:        # 很短的停頓:同一段
                regions[-1] = (regions[-1][0], i)
            else:
                regions.append((start, i))
            start = None
    return [(a / BIN_RATE, b / BIN_RATE) for a, b in regions if b - a >= 0.1 * BIN_RATE]


# ------------------------------------------------------------ 播放聲音

class Player:
    """播放抽出來的聲音;position() 是現在播到第幾秒(畫面和字幕都跟著它)。沒有音效裝置時用時鐘代替。"""

    def __init__(self):
        self.sound = None
        self.duration = 0.0
        self.playing = False
        self.base = 0.0
        self._started = 0.0
        self.mixer = False

    def load(self, sound, duration):
        self.stop()
        self.duration = duration
        self.sound = sound
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, channels=2)
            pygame.mixer.music.load(str(sound))
            self.mixer = True
        except pygame.error:
            self.mixer = False

    def unload(self):
        self.stop()
        if self.mixer:
            try:
                pygame.mixer.music.unload()
            except pygame.error:
                pass
        self.sound, self.mixer = None, False

    def play(self, at):
        at = max(0.0, min(at, max(0.0, self.duration - 0.05)))
        self.base = at
        self._started = time.monotonic()
        if self.mixer:
            try:
                pygame.mixer.music.play(start=at)
            except pygame.error:
                self.mixer = False
        self.playing = True

    def pause(self):
        if self.playing:
            self.base = self.position()
            self.playing = False
            if self.mixer:
                pygame.mixer.music.stop()

    def stop(self):
        self.playing = False
        if self.mixer:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass

    def seek(self, at):
        at = max(0.0, min(at, self.duration))
        if self.playing:
            self.play(at)
        else:
            self.base = at

    def position(self):
        if not self.playing:
            return self.base
        if self.mixer:
            ms = pygame.mixer.music.get_pos()
            if ms < 0 or not pygame.mixer.music.get_busy():
                self.playing = False                # 播完了
                self.base = self.duration
                return self.base
            return self.base + ms / 1000
        now = self.base + time.monotonic() - self._started
        if now >= self.duration:
            self.playing, self.base = False, self.duration
        return min(now, self.duration)


# ------------------------------------------------------------ 畫面

class Frames:
    """背景用 ffmpeg 解出縮小的畫面:播放時連續解,暫停或跳轉時只解那一張。"""

    def __init__(self, path, size):
        self.path = str(path)
        w, h = size
        self.size = (FRAME_W, max(2, round(FRAME_W * h / w / 2) * 2)) if w > FRAME_W else (w // 2 * 2, h // 2 * 2)
        self.frame = None                   # (秒, bytes):目前要顯示的
        self.buffer = deque()
        self._want = None                   # ("still", 秒) 或 ("play", 秒)
        self._busy = None
        self._cond = threading.Condition()
        self._stop = False
        self._proc = None
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()

    def close(self):
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        self._kill()

    def _kill(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def still(self, at):
        with self._cond:
            if self._want == ("still", at) or (self._want is None and self._busy == ("still", at)):
                return
            self._want = ("still", at)
            self._cond.notify_all()
        if self._busy and self._busy[0] == "play":
            self._kill()

    def play(self, at):
        with self._cond:
            self._want = ("play", at)
            self.buffer.clear()
            self._cond.notify_all()
        self._kill()

    def show(self, clock):
        """播放中:拿出時間到了的那張(太舊的丟掉)。"""
        with self._cond:
            while self.buffer and self.buffer[0][0] <= clock + 0.5 / FRAME_FPS:
                self.frame = self.buffer.popleft()
            self._cond.notify_all()
        return self.frame

    def _args(self, at, count=None):
        w, h = self.size
        args = [ffmpeg(), "-nostdin", "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", self.path, "-an", "-sn",
                "-vf", f"fps={FRAME_FPS},scale={w}:{h}" if count is None else f"scale={w}:{h}"]
        if count is not None:
            args += ["-frames:v", str(count)]
        return args + ["-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]

    def _work(self):
        size = self.size[0] * self.size[1] * 3
        while True:
            with self._cond:
                while self._want is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                job, self._want = self._want, None
                self._busy = job
            kind, at = job
            try:
                self._proc = deps.popen(self._args(at, 1 if kind == "still" else None),
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                count = 0
                while True:
                    data = self._proc.stdout.read(size)
                    if len(data) < size:
                        break
                    stamp = at + count / FRAME_FPS
                    count += 1
                    with self._cond:
                        if kind == "still":
                            self.frame = (at, data)
                            break
                        if self._want is not None or self._stop:
                            break
                        self.buffer.append((stamp, data))
                        while len(self.buffer) >= AHEAD and self._want is None and not self._stop:
                            self._cond.wait(0.5)
                        if self._want is not None or self._stop:
                            break
            except OSError:
                pass
            finally:
                self._kill()
                self._proc = None
                with self._cond:
                    self._busy = None
