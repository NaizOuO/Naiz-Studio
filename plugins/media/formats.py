"""影音工具的格式表:每種輸出格式能用的編碼器、畫質數值與說明文字。只有資料和簡單計算,不執行 FFmpeg。"""

from dataclasses import dataclass, field
from pathlib import Path

LEVELS = [("small", "檔案最小"), ("balance", "平衡"), ("high", "接近原畫質")]
LEVEL_NOTES = {
    "small": "適合傳訊息或備份，細節會變模糊",
    "balance": "大小和畫質兼顧，大多數情況選這個",
    "high": "肉眼幾乎看不出差別，檔案較大",
}
SPEEDS = [("fast", "較快"), ("normal", "標準"), ("slow", "較慢")]
SPEED_NOTES = {"fast": "轉得快，檔案稍大", "normal": "速度和檔案大小兼顧", "slow": "花比較久的時間，換取小一點的檔案"}


@dataclass
class Encoder:
    """一個實際的 FFmpeg 編碼器。品質用 0~100 的位置表示(100 畫質最好),再換成編碼器自己的數值。"""

    name: str
    quality: tuple = ()          # 品質參數,"{q}" 會換成數值
    worst: int = 0
    best: int = 0
    levels: dict = field(default_factory=dict)    # small / balance / high 對應的編碼器數值
    speeds: dict = field(default_factory=dict)    # fast / normal / slow 對應的參數
    extra: tuple = ()            # 一定要加的參數
    two_pass: str = ""           # "pass":用 -pass 分析兩次;"x265":x265 自己的參數;空字串:不分析兩次
    hardware: bool = False
    bpp: dict = field(default_factory=dict)       # 各等級每像素每格的位元數,預估大小用;空的表示沒有實測
    bpp_exponent: float = 0.0    # 解析度是 1080p 的 N 倍時,bpp 乘上 N 的這個次方

    def value(self, position):
        return round(self.worst + (self.best - self.worst) * max(0, min(100, position)) / 100)

    def position(self, value):
        return (value - self.worst) / (self.best - self.worst) * 100

    def level_position(self, level):
        return self.position(self.levels[level])

    def quality_args(self, position):
        value = str(self.value(position))
        return [part.replace("{q}", value) for part in self.quality]


@dataclass
class Codec:
    label: str
    note: str
    cpu: Encoder
    hardware: dict = field(default_factory=dict)   # 顯示卡廠牌 → Encoder
    slow: str = ""               # 用處理器轉很慢時的橘字提醒
    bitrate: bool = True         # 可以改用平均或固定位元速率

    @property
    def hardware_capable(self):
        return bool(self.hardware)


def _x26x(name, worst, best, levels, two_pass):
    return Encoder(name, ("-crf", "{q}"), worst, best, levels,
                   {"fast": ("-preset", "faster"), "normal": ("-preset", "medium"), "slow": ("-preset", "slow")},
                   two_pass=two_pass)


def _nvenc(name, levels):
    return Encoder(name, ("-rc", "vbr", "-b:v", "0", "-cq", "{q}"), 51, 1, levels,
                   {"fast": ("-preset", "p3"), "normal": ("-preset", "p5"), "slow": ("-preset", "p7")}, hardware=True)


def _amf(name, levels):
    return Encoder(name, ("-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}"), 51, 1, levels,
                   {"fast": ("-quality", "speed"), "normal": ("-quality", "balanced"), "slow": ("-quality", "quality")},
                   hardware=True)


def _qsv(name, levels):
    return Encoder(name, ("-global_quality", "{q}"), 51, 1, levels,
                   {"fast": ("-preset", "faster"), "normal": ("-preset", "medium"), "slow": ("-preset", "slower")},
                   hardware=True)


def _qscale(name, levels, worst=31, best=2, extra=()):
    """舊格式用的 -q:v(數字越小畫質越好)。"""
    return Encoder(name, ("-q:v", "{q}"), worst, best, levels, extra=extra)


# 三個畫質等級的數值:用真實手機影片(1080p、每秒 30 格)量 VMAF 畫質分數決定,
# 接近原畫質約 95~96 分、平衡約 92 分、檔案最小約 87 分。同一個數字在顯示卡和處理器上的畫質不一樣,所以分開設定。
# AMD、Intel 顯示卡沒有實測,先用 NVIDIA 的數值;處理器的 AV1 太慢沒有實測,參考顯示卡的結果。
# *_BPP 只用來預估檔案大小:(1080p 時各等級每個像素每一格大約用幾位元, 解析度指數)。
# 解析度越高每個像素用的位元越少,用 720p、1080p、4K 三段手機影片的實測大小推算
X264, X264_BPP = {"small": 28, "balance": 24, "high": 21}, ({"small": 0.036, "balance": 0.060, "high": 0.102}, -0.24)
H264_HW, H264_HW_BPP = {"small": 33, "balance": 30, "high": 26}, ({"small": 0.034, "balance": 0.051, "high": 0.090}, -0.26)
X265, X265_BPP = {"small": 27, "balance": 23, "high": 19}, ({"small": 0.022, "balance": 0.039, "high": 0.079}, -0.56)
HEVC_HW, HEVC_HW_BPP = {"small": 34, "balance": 31, "high": 26}, ({"small": 0.021, "balance": 0.032, "high": 0.070}, -0.42)
AOM, AOM_BPP = {"small": 42, "balance": 35, "high": 28}, ({"small": 0.018, "balance": 0.030, "high": 0.057}, -0.40)
AV1_HW, AV1_HW_BPP = {"small": 44, "balance": 38, "high": 32}, ({"small": 0.018, "balance": 0.030, "high": 0.057}, -0.40)
# VP9 只量了 720p 和 1080p,兩點算出的指數太陡,改用和 H.265 相近的 -0.6
VP9, VP9_BPP = {"small": 37, "balance": 31, "high": 24}, ({"small": 0.026, "balance": 0.044, "high": 0.087}, -0.60)


def _bpp(encoder, table):
    encoder.bpp, encoder.bpp_exponent = table
    return encoder


def _hardware(prefix, levels, bpp):
    return {"nvidia": _bpp(_nvenc(f"{prefix}_nvenc", levels), bpp), "amd": _bpp(_amf(f"{prefix}_amf", levels), bpp),
            "intel": _bpp(_qsv(f"{prefix}_qsv", levels), bpp)}


CODECS = {
    "h264": Codec("H.264", "相容性最好，幾乎所有手機、電腦、電視都能播",
                  _bpp(_x26x("libx264", 51, 0, X264, "pass"), X264_BPP), _hardware("h264", H264_HW, H264_HW_BPP)),
    "hevc": Codec("H.265", "同樣畫質下檔案比 H.264 小約三到四成，較舊的裝置可能播不了",
                  _bpp(_x26x("libx265", 51, 0, X265, "x265"), X265_BPP), _hardware("hevc", HEVC_HW, HEVC_HW_BPP),
                  slow="用處理器轉 H.265 比 H.264 慢約三倍"),
    "av1": Codec("AV1", "檔案最小的新格式，較舊的裝置和軟體可能播不了",
                 _bpp(Encoder("libaom-av1", ("-b:v", "0", "-crf", "{q}"), 63, 0, AOM,
                              {"fast": ("-cpu-used", "8"), "normal": ("-cpu-used", "6"), "slow": ("-cpu-used", "4")},
                              extra=("-row-mt", "1"), two_pass="pass"), AOM_BPP),
                 _hardware("av1", AV1_HW, AV1_HW_BPP),
                 slow="用處理器轉 AV1 非常慢，30 秒的影片可能要轉 40 秒以上"),
    "vp9": Codec("VP9", "網頁影片常用，畫質和 H.265 接近",
                 _bpp(Encoder("libvpx-vp9", ("-b:v", "0", "-crf", "{q}"), 63, 0, VP9,
                              {"fast": ("-cpu-used", "5"), "normal": ("-cpu-used", "3"), "slow": ("-cpu-used", "1")},
                              extra=("-deadline", "good", "-row-mt", "1"), two_pass="pass"), VP9_BPP),
                 slow="VP9 轉得比較慢，30 秒的影片約要 20 秒以上"),
    "vp8": Codec("VP8", "舊版網頁影片格式，壓縮率不如 VP9",
                 Encoder("libvpx", ("-b:v", "0", "-crf", "{q}"), 63, 4, {"small": 40, "balance": 20, "high": 10},
                         {"fast": ("-cpu-used", "5"), "normal": ("-cpu-used", "2"), "slow": ("-cpu-used", "0")},
                         extra=("-deadline", "good", "-b:v", "0"), two_pass="pass")),
    "mpeg4": Codec("MPEG-4", "舊的影片格式，相容老舊的播放器，同樣畫質檔案比 H.264 大",
                   _qscale("mpeg4", {"small": 12, "balance": 6, "high": 3}, extra=("-vtag", "xvid"))),
    "mjpeg": Codec("Motion JPEG", "每一格都是一張 JPG，剪輯方便但檔案非常大",
                   _qscale("mjpeg", {"small": 10, "balance": 5, "high": 2}, extra=("-pix_fmt", "yuvj420p"))),
    "prores": Codec("ProRes", "剪輯軟體常用，幾乎不失真，但檔案非常大",
                    Encoder("prores_ks", ("-profile:v", "{q}"), 0, 3, {"small": 0, "balance": 2, "high": 3},
                            extra=("-pix_fmt", "yuv422p10le")), bitrate=False),
    "mpeg1": Codec("MPEG-1", "非常舊的格式，只給需要的舊設備使用",
                   _qscale("mpeg1video", {"small": 12, "balance": 6, "high": 3})),
    "mpeg2": Codec("MPEG-2", "DVD 和電視廣播用的舊格式，檔案較大",
                   _qscale("mpeg2video", {"small": 12, "balance": 6, "high": 3})),
    "wmv2": Codec("WMV", "Windows 舊格式，畫質和壓縮率都不如 H.264",
                  _qscale("wmv2", {"small": 12, "balance": 6, "high": 3})),
    "flv1": Codec("Sorenson Spark", "舊的 Flash 影片編碼，畫質和壓縮率都很差",
                  _qscale("flv", {"small": 12, "balance": 6, "high": 3})),
    "theora": Codec("Theora", "開放格式，壓縮率不如 VP9",
                    Encoder("libtheora", ("-q:v", "{q}"), 0, 10, {"small": 4, "balance": 7, "high": 9})),
}
# ffprobe 回報的編碼名稱 → CODECS 的鍵
PROBE_CODECS = {"h264": "h264", "hevc": "hevc", "av1": "av1", "vp9": "vp9", "vp8": "vp8", "mpeg4": "mpeg4",
                "mjpeg": "mjpeg", "prores": "prores", "mpeg1video": "mpeg1", "mpeg2video": "mpeg2",
                "wmv1": "wmv2", "wmv2": "wmv2", "wmv3": "wmv2", "vc1": "wmv2", "flv1": "flv1", "theora": "theora"}


@dataclass
class AudioCodec:
    label: str
    encoder: str                 # PCM 的 {bits} 會換成位元深度
    bitrates: tuple = ()         # 空的表示無損,沒有位元速率可選
    levels: dict = field(default_factory=dict)
    sample_rates: tuple = ()     # 空的表示任何取樣率都可以
    max_channels: int = 8

    @property
    def lossless(self):
        return not self.bitrates

    def encoder_name(self, bits=16):
        return self.encoder.replace("{bits}", str(bits))


AUDIO_CODECS = {
    "aac": AudioCodec("AAC", "aac", (64, 96, 128, 160, 192, 256, 320), {"small": 96, "balance": 160, "high": 256}),
    "mp3": AudioCodec("MP3", "libmp3lame", (64, 96, 128, 160, 192, 256, 320), {"small": 96, "balance": 192, "high": 320},
                      (48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000), 2),
    "opus": AudioCodec("Opus", "libopus", (32, 48, 64, 96, 128, 160, 192, 256), {"small": 64, "balance": 128, "high": 192},
                       (48000, 24000, 16000, 12000, 8000)),
    "vorbis": AudioCodec("Vorbis", "libvorbis", (64, 96, 128, 160, 192, 256, 320),
                         {"small": 96, "balance": 160, "high": 256}),
    "wma": AudioCodec("WMA", "wmav2", (64, 96, 128, 160, 192), {"small": 64, "balance": 128, "high": 192}, (), 2),
    "ac3": AudioCodec("AC-3", "ac3", (96, 128, 192, 256, 384, 448, 640), {"small": 192, "balance": 384, "high": 448},
                      (48000, 44100, 32000), 6),
    "mp2": AudioCodec("MP2", "mp2", (96, 128, 192, 256, 384), {"small": 128, "balance": 192, "high": 384},
                      (48000, 44100, 32000), 2),
    "flac": AudioCodec("FLAC", "flac"),
    "alac": AudioCodec("ALAC", "alac"),
    "pcm": AudioCodec("PCM", "pcm_s{bits}le"),
    "pcm_be": AudioCodec("PCM", "pcm_s{bits}be"),
}
PROBE_AUDIO = {"aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "vorbis", "wmav2": "wma", "wmav1": "wma",
               "ac3": "ac3", "mp2": "mp2", "flac": "flac", "alac": "alac"}


@dataclass
class VideoFormat:
    key: str
    label: str
    ext: str
    muxer: tuple                 # 指定封裝格式的參數
    codecs: tuple                # 可用的影片編碼,第一個是預設
    audio: tuple                 # 可用的音訊編碼,第一個是預設;空的表示不能有聲音
    note: str
    warn: str = ""               # 橘字提醒
    target: str = ""             # DVD:ntsc-dvd / pal-dvd,解析度和編碼固定
    sample_rates: tuple = ()


VIDEO_FORMATS = [
    VideoFormat("keep", "原格式", "", (), (), (), "格式不變，只重新壓縮；GIF 以外的動畫會存成 MP4"),
    VideoFormat("mp4", "MP4", ".mp4", ("-f", "mp4", "-movflags", "+faststart"), ("h264", "hevc", "av1", "mpeg4"),
                ("aac", "mp3", "opus"), "最通用，手機、電腦、網站都能播"),
    VideoFormat("mov", "MOV", ".mov", ("-f", "mov"), ("h264", "hevc", "prores", "mjpeg"), ("aac", "pcm"),
                "Apple 裝置和剪輯軟體常用"),
    VideoFormat("mkv", "MKV", ".mkv", ("-f", "matroska"), ("h264", "hevc", "av1", "vp9"), ("aac", "opus", "flac", "mp3"),
                "可以放多條音軌和字幕，部分手機和電視不支援"),
    VideoFormat("webm", "WebM", ".webm", ("-f", "webm"), ("vp9", "av1", "vp8"), ("opus", "vorbis"),
                "網頁用的格式，瀏覽器可以直接播放"),
    VideoFormat("avi", "AVI", ".avi", ("-f", "avi"), ("mpeg4", "h264", "mjpeg"), ("mp3", "pcm"),
                "老格式，相容舊軟體；同樣畫質檔案較大"),
    VideoFormat("wmv", "WMV", ".wmv", ("-f", "asf"), ("wmv2",), ("wma",), "Windows 舊格式，給只能播 WMV 的舊軟體使用"),
    VideoFormat("flv", "FLV", ".flv", ("-f", "flv"), ("h264", "flv1"), ("aac", "mp3"), "舊的 Flash 網頁影片格式",
                sample_rates=(44100, 22050, 11025)),
    VideoFormat("mpeg1", "MPEG-1", ".mpg", ("-f", "mpeg"), ("mpeg1",), ("mp2",), "非常舊的格式，檔案大、畫質差",
                warn="只建議給需要這個格式的舊設備使用"),
    VideoFormat("mpeg2", "MPEG-2", ".mpg", ("-f", "vob"), ("mpeg2",), ("mp2", "ac3"), "DVD 和電視廣播用的舊格式，檔案較大"),
    VideoFormat("m2ts", "M2TS", ".m2ts", ("-f", "mpegts", "-mpegts_m2ts_mode", "1"), ("h264", "hevc", "mpeg2"),
                ("aac", "ac3"), "藍光光碟和攝影機常用的格式"),
    VideoFormat("ogv", "OGV", ".ogv", ("-f", "ogg"), ("theora",), ("vorbis", "opus"), "開放格式，壓縮率不如 WebM"),
    VideoFormat("3gp", "3GP", ".3gp", ("-f", "3gp"), ("h264",), ("aac",), "舊手機的格式，建議搭配小解析度"),
    VideoFormat("swf", "SWF", ".swf", ("-f", "swf"), ("flv1",), ("mp3",), "Flash 動畫格式",
                warn="現在的瀏覽器已經不能播放 SWF", sample_rates=(44100, 22050, 11025)),
    VideoFormat("dvd_ntsc", "DVD (NTSC)", ".mpg", ("-f", "dvd"), ("mpeg2",), ("ac3",),
                "可以燒成 DVD 在家用播放器播放；720×480，台灣、美國、日本的規格", target="ntsc-dvd"),
    VideoFormat("dvd_pal", "DVD (PAL)", ".mpg", ("-f", "dvd"), ("mpeg2",), ("ac3",),
                "可以燒成 DVD 在家用播放器播放；720×576，歐洲、中國大陸的規格", target="pal-dvd"),
    VideoFormat("gif", "GIF", ".gif", ("-f", "gif"), (), (), "動圖，沒有聲音；最多 256 色",
                warn="影片轉 GIF 檔案通常會變大很多，建議縮短時間、降低寬度"),
]
VIDEO_FORMAT = {fmt.key: fmt for fmt in VIDEO_FORMATS}
VIDEO_EXT = {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".mkv": "mkv", ".webm": "webm", ".avi": "avi",
             ".wmv": "wmv", ".asf": "wmv", ".flv": "flv", ".mpg": "mpeg2", ".mpeg": "mpeg2", ".vob": "mpeg2",
             ".m2ts": "m2ts", ".mts": "m2ts", ".ts": "m2ts", ".ogv": "ogv", ".3gp": "3gp", ".swf": "swf",
             ".gif": "gif", ".webp": "mp4"}
DVD_BITRATES = {"small": 3000, "balance": 5000, "high": 8000}   # kbps;DVD 規格上限約 9800

DITHERS = [("sierra2_4a", "平滑"), ("bayer:bayer_scale=3", "網點"), ("none", "不抖色")]
DITHER_NOTES = {"sierra2_4a": "顏色過渡比較自然，檔案稍大", "bayer:bayer_scale=3": "規則的網點花紋，檔案較小",
                "none": "色塊明顯，適合卡通、簡單圖案，檔案最小"}


@dataclass
class AudioFormat:
    key: str
    label: str
    ext: str
    muxer: tuple
    codec: str
    note: str
    depths: tuple = ()           # 可選的位元深度


AUDIO_FORMATS = [
    AudioFormat("keep", "原格式", "", (), "", "格式不變，只重新壓縮；影片會依聲音的編碼存成對應的格式"),
    AudioFormat("mp3", "MP3", ".mp3", ("-f", "mp3"), "mp3", "相容性最好，幾乎所有裝置都能播"),
    AudioFormat("m4a", "M4A", ".m4a", ("-f", "ipod"), "aac", "AAC 編碼，同樣音質比 MP3 小，Apple 裝置常用"),
    AudioFormat("wav", "WAV", ".wav", ("-f", "wav"), "pcm", "完全不壓縮，檔案很大，適合剪輯", (16, 24)),
    AudioFormat("flac", "FLAC", ".flac", ("-f", "flac"), "flac", "無損壓縮，音質和原檔一樣，大小約 WAV 的一半", (16, 24)),
    AudioFormat("ogg", "OGG", ".ogg", ("-f", "ogg"), "vorbis", "開放格式，部分 Apple 裝置不支援"),
    AudioFormat("opus", "Opus", ".opus", ("-f", "opus"), "opus", "低位元速率時音質最好，適合語音；舊裝置可能不支援"),
    AudioFormat("wma", "WMA", ".wma", ("-f", "asf"), "wma", "Windows 舊格式，給只能播 WMA 的舊軟體使用"),
    AudioFormat("aiff", "AIFF", ".aiff", ("-f", "aiff"), "pcm_be", "Apple 的不壓縮格式，檔案很大", (16, 24)),
    AudioFormat("alac", "ALAC", ".m4a", ("-f", "ipod"), "alac", "Apple 的無損壓縮，音質和原檔一樣", (16, 24)),
    AudioFormat("ac3", "AC3", ".ac3", ("-f", "ac3"), "ac3", "DVD 和家庭劇院常用，支援環繞聲道"),
]
AUDIO_FORMAT = {fmt.key: fmt for fmt in AUDIO_FORMATS}
AUDIO_EXT = {".mp3": "mp3", ".m4a": "m4a", ".aac": "m4a", ".wav": "wav", ".flac": "flac", ".ogg": "ogg", ".oga": "ogg",
             ".opus": "opus", ".wma": "wma", ".aif": "aiff", ".aiff": "aiff", ".ac3": "ac3"}
# 原格式時,影片檔依聲音的編碼決定存成哪種音訊格式
AUDIO_FROM_CODEC = {"aac": "m4a", "mp3": "mp3", "opus": "opus", "vorbis": "ogg", "flac": "flac", "alac": "alac",
                    "ac3": "ac3", "wma": "wma"}

VIDEO_INPUT = set(VIDEO_EXT)
AUDIO_INPUT = set(AUDIO_EXT) | VIDEO_INPUT - {".gif", ".webp"}

RESOLUTIONS = [("source", "原始大小"), ("2160", "2160p (4K)"), ("1440", "1440p (2K)"), ("1080", "1080p"),
               ("720", "720p"), ("480", "480p"), ("360", "360p"), ("custom", "自訂寬高")]
FRAME_RATES = [("source", "原始"), ("60", "60"), ("50", "50"), ("30", "30"), ("29.97", "29.97"), ("25", "25"),
               ("24", "24"), ("15", "15"), ("10", "10")]
RATE_MODES = [("quality", "品質優先"), ("vbr", "平均位元速率 (VBR)"), ("cbr", "固定位元速率 (CBR)")]
RATE_NOTES = {"quality": "依畫面複雜度自動分配，同樣大小下畫質最好",
              "vbr": "指定平均每秒用多少資料，檔案大小比較好預估",
              "cbr": "每秒固定用一樣多的資料，適合直播或特定設備；同樣大小下畫質較差"}
SAMPLE_RATES = [("source", "原始"), ("48000", "48000 Hz"), ("44100", "44100 Hz"), ("32000", "32000 Hz"),
                ("22050", "22050 Hz"), ("16000", "16000 Hz")]
CHANNELS = [("source", "原始"), ("2", "立體聲"), ("1", "單聲道")]


def source_video_format(path, info):
    key = VIDEO_EXT.get(Path(path).suffix.lower(), "mp4")
    if key == "mpeg2" and info.get("video_codec") == "mpeg1video":
        return "mpeg1"
    return key


def source_audio_format(path, info):
    key = AUDIO_EXT.get(Path(path).suffix.lower())
    if key == "m4a" and info.get("audio_codec") == "alac":
        return "alac"
    return key or AUDIO_FROM_CODEC.get(PROBE_AUDIO.get(info.get("audio_codec", ""), ""), "m4a")


def nearest(values, wanted):
    return min(values, key=lambda v: abs(v - wanted))
