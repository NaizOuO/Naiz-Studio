"""影音工具的進階設定視窗:編碼器、解析度、畫面速率、位元速率、編碼速度、時間範圍與音訊設定。"""

import pygame

from core import paths, theme, widgets
from core.scroll import ScrollView
from core.widgets import Button, Dropdown, SegmentedControl, Slider, TextInput, Toggle, draw_text, rounded_panel

from . import formats as F
from . import ops

LABEL_W = 110
KEEP_CODECS = ("h264", "hevc", "av1", "vp9", "mpeg4")
KEEP_AUDIO = ("aac", "mp3", "opus")
GENERIC_BITRATES = (64, 96, 128, 160, 192, 256, 320)

_icons = {}


def _icon(name, size, color):
    """images/ui_*.png 的白色圖示,染成指定顏色;讀不到時回傳 None。"""
    key = (name, size)
    if key not in _icons:
        try:
            image = pygame.image.load(str(paths.IMAGES_DIR / f"ui_{name}.png")).convert_alpha()
            _icons[key] = pygame.transform.smoothscale(image, (size, size))
        except Exception:
            _icons[key] = None
    if _icons[key] is None:
        return None
    tinted = _icons[key].copy()
    tinted.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
    return tinted


def summary(adv, kind, fmt_key):
    """主畫面上顯示的進階設定摘要;全部是預設值時回傳空字串。"""
    parts = []
    video = kind == "video" and fmt_key != "gif" and not fmt_key.startswith("dvd")
    if video:
        if adv.codec:
            parts.append(F.CODECS[adv.codec].label)
        if adv.resolution == "custom":
            parts.append(f"{adv.width}×{adv.height}")
        elif adv.resolution != "source":
            parts.append(f"{adv.resolution}p")
        if adv.fps != "source":
            parts.append(f"{adv.fps} 格/秒")
        if adv.rate_mode != "quality":
            parts.append(f"{'平均' if adv.rate_mode == 'vbr' else '固定'} {adv.bitrate} kbps")
        elif adv.quality is not None:
            parts.append(f"自訂品質 {round(adv.quality)}")
        if adv.speed != "normal":
            parts.append(f"編碼速度{dict(F.SPEEDS)[adv.speed]}")
    if adv.start is not None or adv.end is not None:
        end = ops.human_time(adv.end) if adv.end is not None else "結尾"
        parts.append(f"{ops.human_time(adv.start or 0)} ~ {end}")
    if kind == "video" and fmt_key != "gif" and adv.audio_remove:
        parts.append("移除聲音")
    elif kind == "audio" or fmt_key != "gif":
        if kind == "video" and adv.audio_codec:
            parts.append(F.AUDIO_CODECS[adv.audio_codec].label)
        if adv.audio_bitrate:
            parts.append(f"聲音 {adv.audio_bitrate} kbps")
        if adv.sample_rate != "source":
            parts.append(f"{adv.sample_rate} Hz")
        if adv.channels != "source":
            parts.append(dict(F.CHANNELS)[adv.channels])
    return " · ".join(parts)


class AdvancedDialog:
    def __init__(self, get_screen, accent):
        self.get_screen = get_screen
        self.accent = accent
        self.is_open = False
        self.kind = "video"
        self.fmt_key = "keep"
        self.level = "balance"
        self.encoder_for = None
        self.aspect = 16 / 9
        self.on_done = None
        self.error = ""
        self.custom_quality = False
        self.locked = True
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.area = pygame.Rect(0, 0, 0, 0)
        self.lock_rect = pygame.Rect(0, 0, 0, 0)
        self.view = ScrollView(accent=accent, indicator=True)

        self.codec = Dropdown([("", "")], accent=accent)
        self.resolution = Dropdown(F.RESOLUTIONS, accent=accent)
        self.width = TextInput(accent=accent, size=14)
        self.height = TextInput(accent=accent, size=14)
        self.fps = Dropdown(F.FRAME_RATES, accent=accent)
        self.rate = Dropdown(F.RATE_MODES, accent=accent)
        self.quality = Slider(0, 100, 50, accent=accent)
        self.bitrate = TextInput(accent=accent, size=14)
        self.speed = SegmentedControl(F.SPEEDS, index=1, accent=accent)
        self.start = TextInput(placeholder="從頭開始", accent=accent, size=14)
        self.end = TextInput(placeholder="到結尾", accent=accent, size=14)
        self.audio_remove = Toggle(False, accent=accent)
        self.audio_codec = Dropdown([("", "")], accent=accent)
        self.audio_bitrate = Dropdown([("", "")], accent=accent)
        self.sample_rate = Dropdown(F.SAMPLE_RATES, accent=accent)
        self.channels = SegmentedControl(F.CHANNELS, accent=accent)
        self.btn_reset = Button("還原成預設值", filled=False, size=13)
        self.btn_cancel = Button("取消", filled=False, size=14)
        self.btn_done = Button("完成", accent=accent, size=14)
        self.controls, self.inputs, self.sliders, self.dropdowns = [], [], [], []

    @property
    def screen(self):
        return self.get_screen()

    # ------------------------------------------------------------ 開關

    def open(self, kind, adv, fmt_key, level, encoder_for, aspect, on_done):
        """kind:video / audio;encoder_for(編碼) 回傳實際會用的 Encoder;aspect 是第一個檔案的寬高比。"""
        self.kind, self.fmt_key, self.level = kind, fmt_key, level
        self.encoder_for, self.aspect, self.on_done = encoder_for, aspect or 16 / 9, on_done
        self._load(adv)
        self.view.scroll = 0
        self.error = ""
        self.is_open = True

    def close(self):
        for field in (self.width, self.height, self.bitrate, self.start, self.end):
            field.blur()
        for dropdown in (self.codec, self.resolution, self.fps, self.rate, self.audio_codec, self.audio_bitrate,
                         self.sample_rate):
            dropdown.close()
        self.quality.dragging = False
        self.view.reset()
        self.is_open = False

    # ------------------------------------------------------------ 資料

    @property
    def video_fmt(self):
        return F.VIDEO_FORMAT.get(self.fmt_key) if self.kind == "video" else None

    def _codec_keys(self):
        if self.kind != "video" or self.fmt_key == "gif":
            return ()
        return KEEP_CODECS if self.fmt_key == "keep" else self.video_fmt.codecs

    def _audio_keys(self):
        if self.kind != "video":
            fmt = F.AUDIO_FORMAT[self.fmt_key]
            return (fmt.codec,) if fmt.codec else ()
        if self.fmt_key == "keep":
            return KEEP_AUDIO
        return self.video_fmt.audio

    def current_codec(self):
        keys = self._codec_keys()
        if not keys:
            return ""
        return self.codec.value or ("h264" if self.fmt_key == "keep" else keys[0])

    def _encoder(self):
        return self.encoder_for(self.current_codec() or "h264")

    def _level_position(self):
        return self._encoder().level_position(self.level)

    def _refresh_audio_bitrates(self, value=""):
        keys = self._audio_keys()
        key = (self.audio_codec.value if self.kind == "video" else "") or (keys[0] if keys else "")
        bitrates = F.AUDIO_CODECS[key].bitrates if key else GENERIC_BITRATES
        options = [("", "跟著畫質等級")] + [(str(b), f"{b} kbps") for b in bitrates]
        self.audio_bitrate.set_options(options, value)

    def _load(self, adv):
        keys = self._codec_keys()
        options = ([("", "沿用原檔的編碼")] if self.fmt_key == "keep" else []) + \
            [(key, F.CODECS[key].label) for key in keys]
        self.codec.set_options(options or [("", "")])
        self.codec.set_value(adv.codec if adv.codec in keys else ("" if self.fmt_key == "keep" or not keys else keys[0]))
        self.resolution.set_value(adv.resolution)
        self.width.set_text(str(adv.width))
        self.height.set_text(str(adv.height))
        self.fps.set_value(adv.fps)
        self.rate.set_value(adv.rate_mode)
        self.custom_quality = adv.quality is not None
        self.quality.value = adv.quality if adv.quality is not None else (self._level_position() if keys else 50)
        self.bitrate.set_text(str(adv.bitrate))
        self.speed.index = [key for key, _ in F.SPEEDS].index(adv.speed)
        self.start.set_text(ops.human_time(adv.start) if adv.start is not None else "")
        self.end.set_text(ops.human_time(adv.end) if adv.end is not None else "")
        self.audio_remove.value = adv.audio_remove
        audio_keys = self._audio_keys()
        audio_options = ([("", "沿用原檔的編碼")] if self.fmt_key == "keep" else []) + \
            [(key, F.AUDIO_CODECS[key].label) for key in audio_keys]
        self.audio_codec.set_options(audio_options or [("", "")])
        default_audio = "" if self.fmt_key == "keep" or not audio_keys else audio_keys[0]
        self.audio_codec.set_value(adv.audio_codec if adv.audio_codec in audio_keys else default_audio)
        self._refresh_audio_bitrates(str(adv.audio_bitrate) if adv.audio_bitrate else "")
        self.audio_bitrate.set_value(str(adv.audio_bitrate) if adv.audio_bitrate else "")
        self.sample_rate.set_value(adv.sample_rate)
        self.channels.index = [key for key, _ in F.CHANNELS].index(adv.channels)

    def collect(self):
        """把畫面上的值整理成 Advanced;有欄位填錯時回傳 None,錯誤寫在 self.error。"""
        adv = ops.Advanced()
        try:
            if self._codec_keys():
                default = "" if self.fmt_key == "keep" else self._codec_keys()[0]
                adv.codec = "" if self.codec.value == default else self.codec.value
                adv.resolution = self.resolution.value
                if adv.resolution == "custom":
                    adv.width, adv.height = int(self.width.text), int(self.height.text)
                    if not (16 <= adv.width <= 8192 and 16 <= adv.height <= 8192):
                        raise ValueError("寬和高要在 16 到 8192 之間")
                adv.fps = self.fps.value
                adv.rate_mode = self.rate.value if F.CODECS[self.current_codec()].bitrate else "quality"
                if adv.rate_mode == "quality":
                    if self.custom_quality and abs(self.quality.value - self._level_position()) >= 0.5:
                        adv.quality = float(self.quality.value)
                else:
                    adv.bitrate = int(self.bitrate.text)
                    if not 50 <= adv.bitrate <= 200000:
                        raise ValueError("位元速率要在 50 到 200000 kbps 之間")
                adv.speed = self.speed.value
            adv.start, adv.end = ops.parse_time(self.start.text), ops.parse_time(self.end.text)
            if adv.start is not None and adv.end is not None and adv.end <= adv.start:
                raise ValueError("結束時間要晚於開始時間")
            if self.kind == "video":
                adv.audio_remove = self.audio_remove.value
                default_audio = "" if self.fmt_key == "keep" or not self._audio_keys() else self._audio_keys()[0]
                adv.audio_codec = "" if self.audio_codec.value == default_audio else self.audio_codec.value
            adv.audio_bitrate = int(self.audio_bitrate.value) if self.audio_bitrate.value else None
            adv.sample_rate = self.sample_rate.value
            adv.channels = self.channels.value
        except ValueError as exc:
            message = str(exc)
            self.error = message if message and not message.startswith("invalid literal") else "有欄位不是數字"
            return None
        self.error = ""
        return adv

    # ------------------------------------------------------------ 事件

    def _typed(self, field):
        if not self.locked or field not in (self.width, self.height) or not field.text.isdigit():
            return
        value = int(field.text)
        if field is self.width:
            self.height.set_text(str(max(2, round(value / self.aspect / 2) * 2)))
        else:
            self.width.set_text(str(max(2, round(value * self.aspect / 2) * 2)))

    def _changed(self, control, before):
        if control is self.codec and not self.custom_quality and self._codec_keys():
            self.quality.value = self._level_position()
        elif control is self.audio_codec:
            self._refresh_audio_bitrates()
        elif control is self.resolution and self.resolution.value == "custom" and before != "custom":
            self._typed(self.width)

    def handle_event(self, event, pos):
        for dropdown in self.dropdowns:
            if dropdown.is_open:
                before = dropdown.value
                dropdown.handle(event, pos)
                if dropdown.value != before:
                    self._changed(dropdown, before)
                return
        focused = any(field.focused for field in self.inputs)
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE and not focused:
            self.close()
            return
        if self.view.handle_event(event, pos):
            return
        for field in self.inputs:
            if field.handle(event, pos):
                self._typed(field)
        inside = self.area.collidepoint(pos)
        for slider in self.sliders:
            if event.type == pygame.MOUSEBUTTONDOWN and not inside:
                continue
            if slider.handle(event, pos):
                self.custom_quality = True
                return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.btn_cancel.clicked(pos, True):
            self.close()
        elif self.btn_reset.clicked(pos, True):
            self._load(ops.Advanced())
            self.error = ""
        elif self.btn_done.clicked(pos, True):
            adv = self.collect()
            if adv is not None:
                action = self.on_done
                self.close()
                action(adv)
        elif inside:
            for dropdown in self.dropdowns:
                if dropdown.handle(event, pos):
                    return
            if self.lock_rect.collidepoint(pos):
                self.locked = not self.locked
                self._typed(self.width)
                return
            for control in self.controls:
                before = getattr(control, "index", None)
                if control.clicked(pos, True):
                    self._changed(control, before)
                    return

    # ------------------------------------------------------------ 繪製

    def draw(self, mouse_pos):
        screen = self.screen
        width, height = screen.get_size()
        veil = pygame.Surface((width, height), pygame.SRCALPHA)
        veil.fill((8, 10, 14, 170))
        screen.blit(veil, (0, 0))
        panel = pygame.Rect(0, 0, min(620, width - 60), min(760, height - 60))
        panel.center = (width // 2, height // 2)
        self.rect = panel
        rounded_panel(screen, panel, theme.PANEL, radius=14, alpha=250, border=theme.PANEL_EDGE)

        title = draw_text(screen, "進階設定", (panel.x + 24, panel.y + 18), 17, theme.TEXT, bold=True)
        label = (dict((f.key, f.label) for f in F.VIDEO_FORMATS) if self.kind == "video"
                 else dict((f.key, f.label) for f in F.AUDIO_FORMATS))[self.fmt_key]
        draw_text(screen, f"{'影片' if self.kind == 'video' else '音訊'} · {label} · 套用到這個分頁的所有檔案",
                  (title.right + 14, panel.y + 23), 12, theme.TEXT_FAINT)
        pygame.draw.line(screen, theme.PANEL_EDGE, (panel.x + 16, panel.y + 54), (panel.right - 16, panel.y + 54))

        self.area = pygame.Rect(panel.x, panel.y + 55, panel.width, panel.height - 55 - 66)
        self.controls, self.inputs, self.sliders, self.dropdowns = [], [], [], []
        self.lock_rect = pygame.Rect(0, 0, 0, 0)
        screen.set_clip(self.area)
        bottom = self._draw_rows(panel.x + 24, self.area.y + 16 - self.view.scroll, panel.width - 48, mouse_pos)
        screen.set_clip(None)
        self.view.layout(self.area, bottom + self.view.scroll - self.area.y + 12)
        self.view.draw(screen, mouse_pos)

        foot_y = panel.bottom - 54
        pygame.draw.line(screen, theme.PANEL_EDGE, (panel.x + 16, foot_y - 12), (panel.right - 16, foot_y - 12))
        self.btn_reset.draw(screen, pygame.Rect(panel.x + 24, foot_y, 118, 36), mouse_pos)
        if self.error:
            draw_text(screen, widgets.clip_text(self.error, 12, panel.width - 400), (panel.x + 156, foot_y + 10), 12,
                      theme.DANGER)
        self.btn_cancel.draw(screen, pygame.Rect(panel.right - 24 - 90 - 10 - 80, foot_y, 80, 36), mouse_pos)
        self.btn_done.draw(screen, pygame.Rect(panel.right - 24 - 90, foot_y, 90, 36), mouse_pos)
        for dropdown in self.dropdowns:
            dropdown.draw_menu(screen, mouse_pos)

    def _section(self, title, x, y, right):
        draw_text(self.screen, title, (x, y), 15, theme.TEXT, bold=True)
        pygame.draw.line(self.screen, theme.PANEL_EDGE, (x, y + 28), (right, y + 28))
        return y + 40

    def _label(self, text, x, y, color=theme.TEXT):
        draw_text(self.screen, text, (x, y + 8), 13, color)

    def _note(self, text, x, y, inner, color=theme.TEXT_FAINT):
        draw_text(self.screen, widgets.clip_text(text, 12, inner - LABEL_W), (x + LABEL_W, y), 12, color)
        return y + 20

    def _dropdown(self, label, dropdown, x, y, inner, mouse_pos, enabled=True):
        self._label(label, x, y, theme.TEXT if enabled else theme.TEXT_FAINT)
        dropdown.enabled = enabled
        dropdown.draw(self.screen, pygame.Rect(x + LABEL_W, y, inner - LABEL_W, 34), mouse_pos)
        if enabled:
            self.dropdowns.append(dropdown)
        return y + 40

    def _segment(self, label, control, x, y, inner, mouse_pos):
        self._label(label, x, y)
        control.draw(self.screen, pygame.Rect(x + LABEL_W, y, inner - LABEL_W, 34), mouse_pos)
        self.controls.append(control)
        return y + 40

    def _input(self, field, rect, mouse_pos):
        field.draw(self.screen, rect, mouse_pos)
        self.inputs.append(field)

    def _draw_rows(self, x, y, inner, mouse_pos):
        right = x + inner
        if self.kind == "video":
            y = self._draw_video(x, y, inner, right, mouse_pos)
        y = self._section("時間範圍", x, y, right)
        self._label("開始", x, y)
        half = (inner - LABEL_W - 60) // 2
        self._input(self.start, pygame.Rect(x + LABEL_W, y, half, 34), mouse_pos)
        draw_text(self.screen, "結束", (x + LABEL_W + half + 14, y + 8), 13, theme.TEXT)
        self._input(self.end, pygame.Rect(right - half, y, half, 34), mouse_pos)
        y = self._note("格式：分:秒 或 時:分:秒，例如 1:30；多個檔案時每個都套用", x, y + 42, inner) + 16
        return self._draw_audio(x, y, inner, right, mouse_pos)

    def _draw_video(self, x, y, inner, right, mouse_pos):
        y = self._section("影片", x, y, right)
        if self.fmt_key == "gif":
            return self._note("GIF 的每秒格數、寬度和抖色方式在主畫面設定", x - LABEL_W, y, inner + LABEL_W) + 16
        if self.video_fmt.target:
            return self._note("DVD 的解析度、畫面速率和編碼是規格固定的，只能調整時間範圍和聲音", x - LABEL_W, y,
                              inner + LABEL_W) + 16

        key = self.current_codec()
        codec = F.CODECS[key]
        encoder = self._encoder()
        y = self._dropdown("編碼器", self.codec, x, y, inner, mouse_pos)
        y = self._note(codec.note, x, y, inner)
        if self.fmt_key == "keep" and not self.codec.value:
            y = self._note("每個檔案沿用自己原本的編碼", x, y, inner)
        elif self.fmt_key == "keep":
            y = self._note("原檔的格式不能用這個編碼時，改用那個格式的預設編碼", x, y, inner)
        if codec.slow and not encoder.hardware:
            y = self._note(codec.slow, x, y, inner, theme.WARN)
        y += 10

        y = self._dropdown("解析度", self.resolution, x, y, inner, mouse_pos)
        if self.resolution.value == "custom":
            field_w = (inner - LABEL_W - 60) // 2
            self._input(self.width, pygame.Rect(x + LABEL_W, y, field_w, 34), mouse_pos)
            self.lock_rect = pygame.Rect(x + LABEL_W + field_w + 12, y + 2, 36, 30)
            hovered = self.lock_rect.collidepoint(mouse_pos)
            rounded_panel(self.screen, self.lock_rect, theme.PANEL_LIGHT if hovered else theme.PANEL, radius=7,
                          border=self.accent if self.locked else theme.PANEL_EDGE)
            icon = _icon("link_on" if self.locked else "link_off", 20, self.accent if self.locked else theme.TEXT_DIM)
            if icon is not None:
                self.screen.blit(icon, icon.get_rect(center=self.lock_rect.center))
            self._input(self.height, pygame.Rect(right - field_w, y, field_w, 34), mouse_pos)
            y = self._note("寬 × 高；按鎖鏈可以固定比例（依第一個檔案的比例）", x, y + 40, inner)
        elif self.resolution.value != "source":
            y = self._note("只縮小不放大；直的影片會限制寬度", x, y, inner)
        y += 10

        y = self._dropdown("畫面速率", self.fps, x, y, inner, mouse_pos)
        if self.fps.value != "source":
            y = self._note("每秒幾格；只降低不提高", x, y, inner)
        y += 10

        y = self._dropdown("位元速率", self.rate, x, y, inner, mouse_pos, enabled=codec.bitrate)
        y = self._note(RATE_NOTE_DISABLED if not codec.bitrate else F.RATE_NOTES[self.rate.value], x, y, inner)
        y += 10
        if self.rate.value == "quality" or not codec.bitrate:
            self._label("品質", x, y)
            position = self.quality.value
            slider = pygame.Rect(x + LABEL_W, y + 9, inner - LABEL_W - 92, 16)
            for level, _ in F.LEVELS:       # 三個畫質等級的位置
                mark = slider.x + round(slider.width * encoder.level_position(level) / 100)
                pygame.draw.line(self.screen, theme.TEXT_FAINT, (mark, slider.y - 4), (mark, slider.bottom + 4), 1)
            self.quality.draw(self.screen, slider, mouse_pos)
            self.sliders.append(self.quality)
            name = next((label for level, label in F.LEVELS
                         if abs(position - encoder.level_position(level)) < 0.5), "自訂")
            draw_text(self.screen, name, (right, y + 17), 13, self.accent, right=True)
            y += 38
            y = self._note(f"刻度是三個畫質等級的位置；目前編碼器的數值是 {encoder.value(position)}", x, y, inner)
            if position < encoder.level_position("small") - 0.5:
                y = self._note("比「檔案最小」還低，畫面可能明顯變差", x, y, inner, theme.WARN)
        else:
            self._label("位元速率", x, y)
            self._input(self.bitrate, pygame.Rect(x + LABEL_W, y, 160, 34), mouse_pos)
            draw_text(self.screen, "kbps", (x + LABEL_W + 172, y + 8), 13, theme.TEXT_DIM)
            y = self._note("1080p 一般約 4000~8000，720p 約 2000~4000", x, y + 42, inner)
        y += 10

        if encoder.speeds:
            y = self._segment("編碼速度", self.speed, x, y, inner, mouse_pos)
            y = self._note(F.SPEED_NOTES[self.speed.value], x, y, inner)
        return y + 16

    def _draw_audio(self, x, y, inner, right, mouse_pos):
        y = self._section("音訊", x, y, right)
        if self.kind == "video":
            if self.fmt_key == "gif":
                return self._note("GIF 沒有聲音", x - LABEL_W, y, inner + LABEL_W)
            self._label("移除聲音", x, y)
            self.audio_remove.draw(self.screen, (right - 42, y + 6), mouse_pos)
            self.controls.append(self.audio_remove)
            y += 40
            if self.audio_remove.value:
                return y
            if len(self.audio_codec.options) > 1:
                y = self._dropdown("編碼器", self.audio_codec, x, y, inner, mouse_pos)
        keys = self._audio_keys()
        key = (self.audio_codec.value if self.kind == "video" else "") or (keys[0] if keys else "")
        if key and F.AUDIO_CODECS[key].lossless:
            y = self._note("無損格式不需要設定位元速率", x - LABEL_W, y, inner + LABEL_W) + 6
        else:
            y = self._dropdown("位元速率", self.audio_bitrate, x, y, inner, mouse_pos)
            y = self._note("選「跟著畫質等級」時依主畫面的畫質等級決定", x, y, inner) + 6
        y = self._dropdown("取樣率", self.sample_rate, x, y, inner, mouse_pos)
        y = self._note("格式不支援選的取樣率時，自動改用最接近的", x, y, inner) + 6
        return self._segment("聲道", self.channels, x, y, inner, mouse_pos)


RATE_NOTE_DISABLED = "這個編碼只能用品質設定"
