"""
QB → Anki Card Generator
========================
QBオンラインの問題ページをコピペ → AnkiConnectで直接Ankiに追加
画像対応: Ctrl+V でクリップボードから貼り付け / ファイル選択ボタン

依存: Python 3.8+ 標準ライブラリ + Pillow（画像機能に必要）
  pip install Pillow

exe化: pyinstaller --onefile --noconsole qb_to_anki.py

AnkiConnect設定:
  Ankiを起動 → アドオン 2055492159 をインストール
  Tools > Add-ons > AnkiConnect > Config:
    "webCorsOriginList": ["*"]  を追加
"""

import base64
import io
import json
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.error import URLError
from urllib.request import Request, urlopen

# Pillow は画像機能にのみ必要。なければ画像機能を無効化
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ─────────────────────────────────────────────
ANKICONNECT_URL = "http://localhost:8765"
APP_TITLE       = "QB → Anki Card Generator"
WINDOW_SIZE     = "860x720"
IMG_MAX_PX      = 600   # Ankiカード内の最大表示幅(px)

SUBJECTS = [
    "",
    "A 消化管", "B 肝・胆・膵", "C 循環器", "D 代謝・内分泌",
    "E 腎・泌尿器", "F 免疫・膠原病", "G 血液", "H 感染症",
    "I 呼吸器", "J 神経", "K 中毒", "L 救急", "M 麻酔科",
    "N 医学総論", "O 小児科", "P 婦人科", "Q 産科", "R 眼科",
    "S 耳鼻咽喉科", "T 整形外科", "U 精神科", "V 皮膚科",
    "W 泌尿器科", "X 放射線科", "Y 公衆衛生", "Z 必修問題",
]

# ── ノイズ行パターン ──────────────────────────
NOISE_PATTERNS = [
    re.compile(r"^\d{4}\s+\d+-\d+"),
    re.compile(r"^基準値$"),
    re.compile(r"^\d+-\d+$"),
    re.compile(r"^リトライ$"),
    re.compile(r"^\[掲載頁"),
    re.compile(r"^ID\s*:"),
    re.compile(r"^解答[:：]?\s*$"),
    re.compile(r"^結果[:：]?\s*$"),
    re.compile(r"^履歴"),
    re.compile(r"^自分が登録"),
    re.compile(r"^\d{4}/\d{1,2}/\d{1,2}"),
    re.compile(r"^\*\s*$"),
    re.compile(r".*[○◯]\s*正解"),
    re.compile(r"^[×x]\s*不正解"),
    re.compile(r"^ガイドライン$"),
    re.compile(r"^基本事項など"),
]
CHOICE_RE  = re.compile(r"^\*\s*[aAａ-ｅa-eA-E１-５1-5①-⑤]\s")
CORRECT_RE = re.compile(r"^正解[:：]?\s*([aAａ-ｅa-eA-E１-５1-5①-⑤])", re.IGNORECASE)


def is_noise(line: str) -> bool:
    return any(p.match(line.strip()) for p in NOISE_PATTERNS)


def to_half(c: str) -> str:
    return chr(ord(c) - 0xFEE0) if "ａ" <= c <= "ｚ" or "Ａ" <= c <= "Ｚ" else c


def parse_qb(text: str) -> dict:
    explain_match = re.search(r"^解説\s*$", text, re.MULTILINE)
    if explain_match:
        before      = text[:explain_match.start()]
        expl_lines  = text[explain_match.end():].splitlines()
        explanation = "\n".join(
            l for l in expl_lines if l.strip() and not is_noise(l)
        ).strip()
    else:
        before      = text
        explanation = ""

    lines    = before.splitlines()
    question = ""
    choices  = []
    correct  = ""

    for raw in lines:
        line = raw.strip()
        if not line or is_noise(line):
            continue
        cm = CORRECT_RE.match(line)
        if cm:
            correct = to_half(cm.group(1)).upper()
            continue
        if CHOICE_RE.match(line):
            choices.append(re.sub(r"^\*\s*", "", line))
            continue
        question += ("\n" if question else "") + line

    return {
        "question":    question.strip(),
        "choices":     choices,
        "correct":     correct,
        "explanation": explanation,
    }


# ─────────────────────────────────────────────
# 画像ユーティリティ
# ─────────────────────────────────────────────
def pil_image_to_png_bytes(img: "Image.Image") -> bytes:
    """PIL Image → PNG bytes"""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_bytes_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def get_clipboard_image(root: tk.Tk) -> "Image.Image | None":
    """クリップボードから画像を取得 (Windows/Mac/Linux)"""
    if not HAS_PIL:
        return None
    try:
        # Windows: ImageGrab
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        if isinstance(img, Image.Image):
            return img
    except Exception:
        pass
    return None


def load_image_from_file(path: str) -> "Image.Image | None":
    if not HAS_PIL:
        return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def resize_for_preview(img: "Image.Image", max_w: int = 300) -> "ImageTk.PhotoImage":
    """プレビュー用にリサイズして PhotoImage を返す"""
    w, h = img.size
    if w > max_w:
        img = img.resize((max_w, int(h * max_w / w)), Image.LANCZOS)
    return ImageTk.PhotoImage(img)


def image_to_html_tag(img: "Image.Image") -> str:
    """PIL Image → Ankiカード埋め込み用 <img> タグ (base64)"""
    # 最大幅に収める
    w, h = img.size
    if w > IMG_MAX_PX:
        img = img.resize((IMG_MAX_PX, int(h * IMG_MAX_PX / w)), Image.LANCZOS)
    data = png_bytes_to_base64(pil_image_to_png_bytes(img))
    return f'<img src="data:image/png;base64,{data}" style="max-width:100%;margin:8px 0">'


# ─────────────────────────────────────────────
# AnkiConnect
# ─────────────────────────────────────────────
def anki_request(action: str, **params):
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = Request(ANKICONNECT_URL, payload, {"Content-Type": "application/json"})
    with urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["result"]


def get_basic_model_name() -> str:
    try:
        models = anki_request("modelNames")
        for c in ["Basic", "基本", "Básico", "Basique", "Basis"]:
            if c in models:
                return c
        for name in models:
            if len(anki_request("modelFieldNames", modelName=name)) >= 2:
                return name
        return models[0] if models else "Basic"
    except Exception:
        return "Basic"


# ─────────────────────────────────────────────
# HTML builders
# ─────────────────────────────────────────────
def build_front(p: dict, subject: str, img: "Image.Image | None" = None) -> str:
    tag = (f'<div style="display:inline-block;background:#0d7377;color:#fff;'
           f'font-size:11px;padding:2px 10px;border-radius:12px;'
           f'margin-bottom:10px;font-weight:700">{subject}</div><br>'
           if subject else "")
    q = p["question"].replace("\n", "<br>")
    choices_html = "".join(
        f'<div style="padding:5px 0;'
        f'{"border-bottom:1px solid #dde8f0;" if i < len(p["choices"])-1 else ""}">'
        f'{c}</div>'
        for i, c in enumerate(p["choices"])
    )
    choices_block = (
        f'<div style="border-left:3px solid #0d7377;padding-left:12px;margin-top:10px">'
        f'{choices_html}</div>'
        if choices_html else ""
    )
    img_html = image_to_html_tag(img) if img else ""
    return (
        f'<div style="font-family:\'Noto Sans JP\',sans-serif;font-size:15px;'
        f'line-height:1.8;color:#1a1a2e;max-width:640px;margin:0 auto;text-align:center">'
        f'{tag}'
        f'<div style="font-weight:600;margin-bottom:10px">{q}</div>'
        f'{choices_block}'
        f'{img_html}'
        f'</div>'
    )


def build_back(p: dict, img: "Image.Image | None" = None) -> str:
    correct_html = (
        f'<div style="background:#e8f8f5;border:2px solid #0d7377;border-radius:8px;'
        f'padding:10px 16px;margin-bottom:14px;font-size:20px;font-weight:700;'
        f'color:#0d7377;text-align:center">正解：{p["correct"]}</div>'
        if p["correct"] else ""
    )
    expl = p["explanation"].replace("\n", "<br>")
    expl_html = (
        f'<div style="background:#f8f9fa;border-radius:8px;padding:14px 16px;'
        f'font-size:13px;line-height:1.85;text-align:left">{expl}</div>'
        if expl else ""
    )
    img_html = image_to_html_tag(img) if img else ""
    return (
        f'<div style="font-family:\'Noto Sans JP\',sans-serif;font-size:14px;'
        f'line-height:1.8;color:#1a1a2e;max-width:640px;margin:0 auto;text-align:center">'
        f'{correct_html}{expl_html}{img_html}</div>'
    )


# ─────────────────────────────────────────────
# 画像パネルウィジェット
# ─────────────────────────────────────────────
class ImagePanel(tk.Frame):
    """表面/裏面それぞれの画像選択・表示パネル"""

    def __init__(self, parent, label: str, root: tk.Tk, **kw):
        super().__init__(parent, bg="#0d1526", **kw)
        self._root    = root
        self._image   = None   # PIL Image
        self._photo   = None   # keep reference

        # ヘッダ行
        hdr = tk.Frame(self, bg="#0d1526")
        hdr.pack(fill="x", padx=6, pady=(6, 2))
        tk.Label(hdr, text=f"📷 {label}の画像（任意）",
                 bg="#0d1526", fg="#5a7fa8",
                 font=("Yu Gothic UI", 9, "bold")).pack(side="left")

        if HAS_PIL:
            tk.Button(hdr, text="ファイル選択",
                      bg="#1e2d45", fg="#8aacc8", relief="flat",
                      font=("Yu Gothic UI", 9),
                      command=self._pick_file).pack(side="right", padx=(4, 0))
            tk.Button(hdr, text="Ctrl+V 貼り付け",
                      bg="#1e2d45", fg="#8aacc8", relief="flat",
                      font=("Yu Gothic UI", 9),
                      command=self._paste_clipboard).pack(side="right", padx=(4, 0))
            tk.Button(hdr, text="✕ 削除",
                      bg="#1e2d45", fg="#c05050", relief="flat",
                      font=("Yu Gothic UI", 9),
                      command=self._clear).pack(side="right", padx=(4, 0))
        else:
            tk.Label(hdr, text="※ pip install Pillow で画像機能が使えます",
                     bg="#0d1526", fg="#c05050",
                     font=("Yu Gothic UI", 8)).pack(side="right")

        # 画像表示エリア
        self._canvas = tk.Label(self, bg="#0a0e1a", text="（画像なし）",
                                fg="#2a3a5a", font=("Yu Gothic UI", 9),
                                height=4)
        self._canvas.pack(fill="x", padx=6, pady=(0, 6))

    def _paste_clipboard(self):
        img = get_clipboard_image(self._root)
        if img:
            self._set_image(img)
        else:
            messagebox.showinfo("貼り付け", "クリップボードに画像がありません。\n"
                "QBの画像を Win+Shift+S でスクリーンショット後、\n"
                "このボタンを押してください。")

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="画像を選択",
            filetypes=[("画像ファイル", "*.png *.jpg *.jpeg *.bmp *.gif"), ("すべて", "*.*")]
        )
        if path:
            img = load_image_from_file(path)
            if img:
                self._set_image(img)

    def _set_image(self, img: "Image.Image"):
        self._image = img
        self._photo = resize_for_preview(img, max_w=320)
        self._canvas.config(image=self._photo, text="", height=0)

    def _clear(self):
        self._image = None
        self._photo = None
        self._canvas.config(image="", text="（画像なし）", height=4)

    @property
    def image(self) -> "Image.Image | None":
        return self._image


# ─────────────────────────────────────────────
# スタイル
# ─────────────────────────────────────────────
def apply_styles(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TLabel",      background="#0a0e1a", foreground="#8aacc8", font=("Yu Gothic UI", 10))
    style.configure("H.TLabel",    background="#0a0e1a", foreground="#14c4ab", font=("Yu Gothic UI", 10, "bold"))
    style.configure("TCombobox",   fieldbackground="#0d1526", background="#0d1526",
                    foreground="#c8d8e8", selectbackground="#1e3a5f")
    style.configure("TEntry",      fieldbackground="#0d1526", foreground="#c8d8e8", insertcolor="#c8d8e8")
    style.configure("TFrame",      background="#0a0e1a")
    style.configure("TNotebook",   background="#0d1526", tabmargins=[0, 0, 0, 0])
    style.configure("TNotebook.Tab", background="#0d1526", foreground="#5a7fa8",
                    font=("Yu Gothic UI", 10, "bold"), padding=[16, 6])
    style.map("TNotebook.Tab",
              background=[("selected", "#0a1a2e")],
              foreground=[("selected", "#14c4ab")])
    style.configure("Accent.TButton", background="#0d7377", foreground="white",
                    font=("Yu Gothic UI", 11, "bold"), relief="flat", padding=[0, 8])
    style.map("Accent.TButton", background=[("active", "#14a085")])
    style.configure("Sub.TButton", background="#1e2d45", foreground="#8aacc8",
                    font=("Yu Gothic UI", 10), relief="flat", padding=[0, 6])
    style.map("Sub.TButton", background=[("active", "#263d5e")])


# ─────────────────────────────────────────────
# メインアプリ
# ─────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(WINDOW_SIZE)
        self.resizable(True, True)
        self.configure(bg="#0a0e1a")
        self._parsed    = None
        self._img_front = None  # ImagePanel (preview tab)
        self._img_back  = None  # ImagePanel (preview tab)
        apply_styles(self)
        self._build_ui()

    # ── UI構築 ───────────────────────────────
    def _build_ui(self):
        # ヘッダ
        hdr = tk.Frame(self, bg="#0d1526", height=52)
        hdr.pack(fill="x")
        tk.Label(hdr, text="⚕  QB → Anki Card Generator",
                 bg="#0d1526", fg="#ffffff",
                 font=("Yu Gothic UI", 14, "bold")).pack(side="left", padx=20, pady=12)
        tk.Label(hdr, text="QBオンライン対応 v6.0",
                 bg="#0d1526", fg="#3a5a78",
                 font=("Yu Gothic UI", 9)).pack(side="right", padx=20)

        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=16, pady=12)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.Frame(main, width=210)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        left.pack_propagate(False)
        self._build_left(left)

        self.nb = ttk.Notebook(main)
        self.nb.grid(row=0, column=1, sticky="nsew")
        self._build_input_tab()
        self._build_preview_tab()

    def _lbl(self, parent, text, style="TLabel"):
        ttk.Label(parent, text=text, style=style).pack(anchor="w", pady=(10, 2))

    def _build_left(self, parent):
        self._lbl(parent, "デッキ名")
        self.var_deck = tk.StringVar(value="国試")
        deck_frame = tk.Frame(parent, bg="#0a0e1a")
        deck_frame.pack(fill="x")
        for label in ["国試", "CBT"]:
            tk.Radiobutton(
                deck_frame, text=label, variable=self.var_deck, value=label,
                bg="#0a0e1a", fg="#c8d8e8", selectcolor="#0d1526",
                activebackground="#0a0e1a", activeforeground="#14c4ab",
                font=("Yu Gothic UI", 10),
            ).pack(side="left", padx=(0, 12))

        self._lbl(parent, "科目")
        self.var_subject = tk.StringVar()
        ttk.Combobox(parent, textvariable=self.var_subject,
                     values=SUBJECTS, state="readonly").pack(fill="x")

        self._lbl(parent, "追加タグ（カンマ区切り）")
        self.var_tags = tk.StringVar()
        ttk.Entry(parent, textvariable=self.var_tags).pack(fill="x")

        tk.Frame(parent, bg="#1e3a5f", height=1).pack(fill="x", pady=14)

        self._lbl(parent, "AnkiConnect 状態", style="H.TLabel")
        self.lbl_status = ttk.Label(parent, text="● 未確認", style="TLabel")
        self.lbl_status.pack(anchor="w")
        ttk.Button(parent, text="接続確認", style="Sub.TButton",
                   command=self._check_anki).pack(fill="x", pady=(6, 0))

    def _build_input_tab(self):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text="  ① 問題を貼り付け  ")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        tk.Label(frame,
            text="QBオンラインの問題ページを全選択（Ctrl+A）してそのままコピペしてください。",
            bg="#0a0e1a", fg="#5a7fa8", font=("Yu Gothic UI", 9),
            wraplength=540, justify="left"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(10, 6), padx=4)

        self.txt_input = tk.Text(frame,
            bg="#0d1526", fg="#c8d8e8", insertbackground="#c8d8e8",
            font=("Yu Gothic UI", 11), relief="flat", wrap="word",
            padx=10, pady=8,
            highlightthickness=1, highlightbackground="#1e3a5f",
            highlightcolor="#0d7377")
        self.txt_input.grid(row=1, column=0, sticky="nsew", padx=4)

        sb = ttk.Scrollbar(frame, command=self.txt_input.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.txt_input["yscrollcommand"] = sb.set

        ttk.Button(frame, text="解析 → プレビュー →",
                   style="Accent.TButton",
                   command=self._do_parse).grid(
            row=2, column=0, columnspan=2, sticky="e", pady=10, padx=4)

    def _build_preview_tab(self):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text="  ② プレビュー・送信  ")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        canvas = tk.Canvas(frame, bg="#0a0e1a", highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(frame, command=canvas.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        canvas["yscrollcommand"] = vsb.set

        self.preview_inner = ttk.Frame(canvas)
        self.preview_inner.columnconfigure(0, weight=1)
        win_id = canvas.create_window((0, 0), window=self.preview_inner, anchor="nw")

        def _on_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win_id, width=canvas.winfo_width())

        self.preview_inner.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=8, padx=4)
        ttk.Button(btn_row, text="← 戻る", style="Sub.TButton",
                   command=lambda: self.nb.select(0)).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="  ⚕  Ankiに追加する  ",
                   style="Accent.TButton",
                   command=self._send_to_anki).pack(side="right")
        self.lbl_result = ttk.Label(btn_row, text="", style="TLabel")
        self.lbl_result.pack(side="right", padx=12)

    # ── ロジック ─────────────────────────────
    def _check_anki(self):
        try:
            anki_request("version")
            self.lbl_status.config(text="✓ 接続OK", foreground="#14c4ab")
        except Exception:
            self.lbl_status.config(text="✗ 接続失敗", foreground="#ff6b6b")
            messagebox.showerror("接続エラー",
                "AnkiConnectに接続できません。\n\n"
                "・Ankiが起動しているか確認してください\n"
                "・アドオン 2055492159 (AnkiConnect) がインストールされているか確認してください\n\n"
                'Config に "webCorsOriginList": ["*"] を追加してください')

    def _do_parse(self):
        text = self.txt_input.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("入力なし", "問題テキストを貼り付けてください。")
            return
        self._parsed = parse_qb(text)
        self._refresh_preview()
        self.nb.select(1)

    def _refresh_preview(self):
        for w in self.preview_inner.winfo_children():
            w.destroy()
        self._img_front = None
        self._img_back  = None

        p = self._parsed
        if not p:
            return

        def section(title):
            tk.Label(self.preview_inner, text=title,
                     bg="#0a0e1a", fg="#5a7fa8",
                     font=("Yu Gothic UI", 8, "bold")).pack(
                anchor="w", padx=4, pady=(12, 3))

        def card_frame():
            f = tk.Frame(self.preview_inner, bg="#ffffff",
                         highlightthickness=1, highlightbackground="#dde8f0")
            f.pack(fill="x", padx=4, pady=2)
            return f

        def card_text(parent, text, **kw):
            t = tk.Text(parent, bg="#ffffff", fg="#1a1a2e",
                        font=("Yu Gothic UI", 11), relief="flat",
                        wrap="word", padx=12, pady=10,
                        highlightthickness=0, **kw)
            t.insert("end", text)
            t.config(state="disabled")
            t.pack(fill="x")
            return t

        # FRONT
        section("FRONT（表面）")
        ff = card_frame()
        front_text = p["question"]
        if p["choices"]:
            front_text += "\n\n" + "\n".join(p["choices"])
        card_text(ff, front_text, height=max(3, front_text.count("\n") + 2))

        self._img_front = ImagePanel(self.preview_inner, "表面", self)
        self._img_front.pack(fill="x", padx=4, pady=(2, 6))

        # BACK
        section("BACK（裏面）")
        bf = card_frame()
        back_text = ""
        if p["correct"]:
            back_text += f"正解：{p['correct']}\n\n"
        if p["explanation"]:
            back_text += p["explanation"]
        card_text(bf, back_text.strip(),
                  height=min(18, max(4, back_text.count("\n") + 2)))

        self._img_back = ImagePanel(self.preview_inner, "裏面", self)
        self._img_back.pack(fill="x", padx=4, pady=(2, 6))

        # 手動修正
        tk.Label(self.preview_inner, text="✏  手動修正（必要な場合のみ）",
                 bg="#0a0e1a", fg="#5a7fa8",
                 font=("Yu Gothic UI", 8, "bold")).pack(
            anchor="w", padx=4, pady=(16, 3))

        edit_frame = tk.Frame(self.preview_inner, bg="#0d1526",
                              highlightthickness=1, highlightbackground="#1e3a5f")
        edit_frame.pack(fill="x", padx=4)
        edit_frame.columnconfigure(1, weight=1)

        self._edit_vars = {}
        for row, (label, key, h) in enumerate([
            ("問題文", "question", 2),
            ("正解",   "correct",  1),
            ("解説",   "explanation", 8),
        ]):
            tk.Label(edit_frame, text=label, bg="#0d1526", fg="#5a7fa8",
                     font=("Yu Gothic UI", 9)).grid(
                row=row, column=0, sticky="nw", padx=(10, 6), pady=(8, 4))
            t = tk.Text(edit_frame, bg="#080d18", fg="#c8d8e8",
                        insertbackground="#c8d8e8",
                        font=("Yu Gothic UI", 10), relief="flat",
                        wrap="word", height=h, padx=6, pady=4,
                        highlightthickness=1, highlightbackground="#1e3a5f",
                        highlightcolor="#0d7377")
            t.insert("end", p.get(key, ""))
            t.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=(8, 4))
            self._edit_vars[key] = t

    def _get_edited(self) -> dict:
        if not hasattr(self, "_edit_vars"):
            return self._parsed
        return {
            "question":    self._edit_vars["question"].get("1.0", "end").strip(),
            "choices":     self._parsed["choices"],
            "correct":     self._edit_vars["correct"].get("1.0", "end").strip(),
            "explanation": self._edit_vars["explanation"].get("1.0", "end").strip(),
        }

    def _send_to_anki(self):
        if not self._parsed:
            messagebox.showwarning("未解析", "先に問題を解析してください。")
            return

        p       = self._get_edited()
        deck    = self.var_deck.get()
        subject = self.var_subject.get().strip()
        extra   = [t.strip() for t in self.var_tags.get().split(",") if t.strip()]
        tags    = (["科目::" + subject] if subject else []) + ["QB"] + extra

        img_f = self._img_front.image if self._img_front else None
        img_b = self._img_back.image  if self._img_back  else None

        front = build_front(p, subject, img_f)
        back  = build_back(p, img_b)

        try:
            anki_request("createDeck", deck=deck)
            model_name  = get_basic_model_name()
            field_names = anki_request("modelFieldNames", modelName=model_name)
            front_field = field_names[0] if field_names else "Front"
            back_field  = field_names[1] if len(field_names) > 1 else "Back"
            anki_request("addNote", note={
                "deckName":  deck,
                "modelName": model_name,
                "fields":    {front_field: front, back_field: back},
                "tags":      tags,
                "options":   {"allowDuplicate": False, "duplicateScope": "deck"},
            })
            self.lbl_result.config(text="✓ 追加しました", foreground="#14c4ab")
            self.txt_input.delete("1.0", "end")
            self._parsed = None
            self.nb.select(0)
        except Exception as e:
            self.lbl_result.config(text="✗ 失敗", foreground="#ff6b6b")
            messagebox.showerror("Ankiエラー",
                f"カードの追加に失敗しました。\n\n{e}\n\n"
                "・Ankiが起動しているか確認してください。\n"
                "・AnkiConnectのCORS設定を確認してください。")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
