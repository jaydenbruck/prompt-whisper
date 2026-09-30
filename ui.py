"""
Liquid-glass waveform overlay for Prompt Whisper.

A floating frosted-glass pill (glossy sheen, specular highlight, bright rim)
filled with a fine, mirrored voice waveform. The bars react to live mic
amplitude and scroll like a real voice-memo waveform.
"""
import sys

import customtkinter as ctk
import tkinter as tk
from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageTk
import threading
import time
import math
from typing import Callable, Optional


# ---- layout ----
PILL_W = 280
PILL_H = 72
FOOTER_H = 28

# ---- colors ----
IS_MAC = sys.platform == "darwin"
# Windows keys this colour out to transparent; Tk on macOS has a real transparent colour.
BG_TRANSPARENT = "systemTransparent" if IS_MAC else "#010203"
FONT = ("Helvetica Neue", 13, "bold") if IS_MAC else ("Segoe UI", 11, "bold")
TEXT_COLOR = "#8891a8"              # muted, only shown while processing
WINDOW_ALPHA = 0.94                 # subtle real translucency (glass)

GLASS_TOP = (255, 255, 255, 236)    # frosted body gradient (top -> bottom)
GLASS_BOT = (231, 237, 250, 219)
RIM_LIGHT = (255, 255, 255, 170)    # bright inner rim
RIM_EDGE = (148, 158, 184, 95)      # subtle outer edge for definition
GLOSS_FILL = (255, 255, 255, 85)    # top sheen
SPEC_FILL = (255, 255, 255, 95)     # specular highlight blob
BAR_TOP = (70, 78, 104, 255)        # indigo-graphite waveform (top -> bottom)
BAR_BOT = (28, 34, 54, 255)

# ---- waveform bars ----
NUM_BARS = 19
BAR_W = 3
BAR_GAP = 9
BAR_MIN_H = 2                       # idle half-height
BAR_MAX_H = 26                      # max half-height (margin inside the pill)


def _vgrad(w: int, h: int, top, bot) -> Image.Image:
    """Vertical RGBA gradient (interpolates colour AND alpha)."""
    g = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(round(top[i] + (bot[i] - top[i]) * t)) for i in range(4))
        gd.line((0, y, w, y), fill=c)
    return g


class RecordingWindow:
    def __init__(self,
                 on_start_recording: Callable,
                 on_stop_recording: Callable,
                 on_cancel: Callable):
        self.on_start_recording = on_start_recording
        self.on_stop_recording = on_stop_recording
        self.on_cancel = on_cancel

        self.window: Optional[ctk.CTkToplevel] = None
        self.is_recording = False
        self.start_time = 0

        self._running = False
        self._amp = 0.0
        self._target_amp = 0.0
        self._phase = 0.0
        self._photo = None
        self._img_id = None
        self._render_thread: Optional[threading.Thread] = None
        self._amp_history: list[float] = [0.0] * 48   # ring buffer → scrolling waveform

        # static, pre-rendered layers (built in show())
        self._glass_base: Optional[Image.Image] = None
        self._rim: Optional[Image.Image] = None
        self._bars_grad: Optional[Image.Image] = None
        self._bar_shape: list[float] = []
        self._bar_x0 = 0.0

    # ---------- window lifecycle ----------

    def show(self):
        self.window = ctk.CTkToplevel()
        self.window.title("")
        self.window.overrideredirect(True)

        w = PILL_W
        h = PILL_H + FOOTER_H
        sw = self.window.winfo_screenwidth()
        sh = self.window.winfo_screenheight()
        x = (sw - w) // 2
        y = int(sh * 0.70)
        self.window.geometry(f"{w}x{h}+{x}+{y}")
        self.window.configure(fg_color=BG_TRANSPARENT)
        self.window.attributes('-topmost', True)
        # Windows: key this colour to fully transparent; macOS: a transparent window. Either way the pill floats freely.
        try:
            if IS_MAC:
                self.window.attributes('-transparent', True)
            else:
                self.window.attributes('-transparentcolor', BG_TRANSPARENT)
        except Exception:
            pass
        # subtle real see-through for a glassy feel
        try:
            self.window.attributes('-alpha', WINDOW_ALPHA)
        except Exception:
            pass

        self.canvas = tk.Canvas(
            self.window, width=w, height=h,
            bg=BG_TRANSPARENT, highlightthickness=0, bd=0
        )
        self.canvas.pack()

        # blank by default (clean waveform-only look); filled while processing
        self.status_text = self.canvas.create_text(
            w // 2, PILL_H + FOOTER_H // 2,
            text="",
            fill=TEXT_COLOR,
            font=FONT
        )

        self.window.bind("<Escape>", lambda e: self._on_close())

        self._build_static()
        self._running = True
        self._render_thread = threading.Thread(target=self._render_loop, daemon=True)
        self._render_thread.start()

    # ---------- external API ----------

    def push_amplitude(self, amp: float):
        """Feed raw mic amplitude (0..1-ish) from audio recorder."""
        # Speech RMS typically 0.01–0.08; boost and clamp.
        self._target_amp = max(0.0, min(1.0, amp * 18.0))

    def set_processing_status(self, message: str):
        try:
            self.canvas.itemconfig(self.status_text, text=message)
        except Exception:
            pass

    def start_recording(self):
        self.is_recording = True
        self.start_time = time.time()
        self.on_start_recording()

    def stop_recording(self):
        self.is_recording = False
        self.on_stop_recording()

    def close(self):
        self._running = False
        self.is_recording = False
        if self.window:
            try:
                if self.window.winfo_exists():
                    self.window.destroy()
            except Exception:
                pass
            self.window = None

    def _on_close(self):
        self.is_recording = False
        self.on_cancel()
        self.close()

    # ---------- rendering ----------

    def _build_static(self):
        """Pre-render the glass body, sheen, specular highlight and rim once."""
        W, H = PILL_W, PILL_H
        radius = H // 2

        pill = Image.new("L", (W, H), 0)
        ImageDraw.Draw(pill).rounded_rectangle((0, 0, W - 1, H - 1),
                                               radius=radius, fill=255)

        def build_base(top, bot):
            """Frosted glass body + gloss + specular, clipped to the pill."""
            base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            body = _vgrad(W, H, top, bot)
            body.putalpha(ImageChops.multiply(body.split()[3], pill))
            base = Image.alpha_composite(base, body)

            gloss = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(gloss).rounded_rectangle(
                (4, 2, W - 4, int(H * 0.52)), radius=radius, fill=GLOSS_FILL)
            gloss = gloss.filter(ImageFilter.GaussianBlur(7))
            gloss.putalpha(ImageChops.multiply(gloss.split()[3], pill))
            base = Image.alpha_composite(base, gloss)

            spec = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(spec).ellipse((18, 5, 104, 20), fill=SPEC_FILL)
            spec = spec.filter(ImageFilter.GaussianBlur(5))
            spec.putalpha(ImageChops.multiply(spec.split()[3], pill))
            return Image.alpha_composite(base, spec)

        self._glass_base = build_base(GLASS_TOP, GLASS_BOT)

        # rim overlay (drawn on top of the bars each frame)
        rim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        rd = ImageDraw.Draw(rim)
        rd.rounded_rectangle((1, 1, W - 2, H - 2), radius=radius - 1,
                             outline=RIM_LIGHT, width=1)
        rd.rounded_rectangle((0, 0, W - 1, H - 1), radius=radius,
                             outline=RIM_EDGE, width=1)
        self._rim = rim

        # waveform gradient + fixed per-bar silhouette
        self._bars_grad = _vgrad(W, H, BAR_TOP, BAR_BOT)
        shape = []
        for i in range(NUM_BARS):
            v = (math.sin(i * 0.7) * 0.5 +
                 math.sin(i * 1.7 + 0.6) * 0.3 +
                 math.sin(i * 3.1 + 1.2) * 0.2)
            shape.append(0.35 + 0.65 * abs(v))
        self._bar_shape = shape
        total = NUM_BARS * BAR_W + (NUM_BARS - 1) * BAR_GAP
        self._bar_x0 = (W - total) / 2

    def _render_loop(self):
        while self._running and self.window is not None:
            try:
                # smooth amplitude: fast attack, slower release
                if self._target_amp > self._amp:
                    self._amp += (self._target_amp - self._amp) * 0.55
                else:
                    self._amp += (self._target_amp - self._amp) * 0.18
                self._target_amp *= 0.88
                self._phase += 0.12
                self._amp_history.append(self._amp)
                if len(self._amp_history) > 64:
                    self._amp_history.pop(0)

                img = self._draw_frame(self._amp, self._phase)
                photo = ImageTk.PhotoImage(img)
                self.window.after(0, self._blit, photo)
                time.sleep(1 / 30)
            except Exception:
                break

    def _blit(self, photo):
        try:
            if not self.window or not self.canvas.winfo_exists():
                return
            if self._img_id is None:
                self._img_id = self.canvas.create_image(
                    PILL_W // 2, PILL_H // 2, image=photo
                )
                self.canvas.tag_raise(self.status_text)
            else:
                self.canvas.itemconfig(self._img_id, image=photo)
            self._photo = photo  # prevent GC
        except Exception:
            pass

    def _draw_frame(self, amp: float, phase: float) -> Image.Image:
        """Composite the reactive waveform onto the pre-rendered glass pill."""
        W, H = PILL_W, PILL_H
        img = self._glass_base.copy()

        # build the bar mask for this frame
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        cy = H / 2
        history = self._amp_history
        hist_len = len(history)
        for i in range(NUM_BARS):
            idle = 0.05 + 0.03 * math.sin(phase * 1.3 + i * 0.35)
            # sample amplitude at a per-bar delay → wave scrolls across the pill
            sampled = history[-1 - min(hist_len - 1, i)] if hist_len else amp
            t = i / (NUM_BARS - 1)
            win = 0.55 + 0.45 * math.sin(math.pi * t)     # gentle edge taper
            level = max(idle, sampled * self._bar_shape[i] * 1.2) * win
            level = max(0.0, min(1.0, level))
            half = BAR_MIN_H + (BAR_MAX_H - BAR_MIN_H) * level
            cx = self._bar_x0 + i * (BAR_W + BAR_GAP) + BAR_W / 2
            md.rounded_rectangle(
                (cx - BAR_W / 2, cy - half, cx + BAR_W / 2, cy + half),
                radius=BAR_W / 2, fill=255
            )

        # soft contact shadow → "ink on glass" depth
        shadow = self._bars_grad.copy()
        shadow.putalpha(mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(1.4))
        img = Image.alpha_composite(img, shadow)

        # crisp waveform bars
        bars = self._bars_grad.copy()
        bars.putalpha(mask)
        img = Image.alpha_composite(img, bars)

        # rim on top so the border stays clean over the bars
        img = Image.alpha_composite(img, self._rim)
        return img
