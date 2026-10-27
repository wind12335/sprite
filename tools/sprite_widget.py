"""sprite 桌宠 v20 —— 状态表情(干活/出错)+点击反馈+人设包名 —— 修复HERE定义顺序(部署文件读取无声失败) —— 持久界面切换（不销毁重建） 单实例改用锁文件+PID存活检查（端口表被WSL镜像网络污染不可信）。"""
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

try:
    from PIL import Image, ImageTk, ImageSequence
except ImportError:
    print("请先: pip install pillow"); sys.exit(1)

HERE = Path(__file__).parent  # 必须最先定义：下面的部署文件读取要用
WSL_CMD = ["wsl", "-e", "bash", "-c"]
BG = "#faf0ff"
DISPLAY_SIZE = 72  # 头像显示尺寸（缩放加载，避免原图撑爆窗口）

def _read_deploy(name, default=""):
    """读取 start.sh widget 部署时写下的路径文件（跟随部署位置，不硬编码）。"""
    try:
        v = (HERE / name).read_text(encoding="utf-8").strip()
        return v or default
    except Exception:
        return default

SPRITE_DIR = _read_deploy("sprite_dir.txt")   # WSL里仓库的Linux路径
NAPCAT_DIR = _read_deploy("napcat_dir.txt", "/root/Napcat")
PET_NAME = _read_deploy("widget_name.txt", "sprite")  # 桌宠名=config.toml [persona].name


def _log(msg: str) -> None:
    """GUI进程无控制台，日志写文件（出问题时看 widget.log）。"""
    try:
        with open(HERE / "widget.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass  # 日志失败不影响GUI


def _pid_alive_python(pid: int) -> bool:
    """检查Windows侧PID是否还活着且是python（tasklist查询，PID复用兜底）。"""
    if pid <= 0:
        return False
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=8, encoding="utf-8", errors="replace")
        return "python" in (r.stdout or "").lower()
    except Exception:
        return False  # 查不到就当死进程，宁可放行也不误挡


def _acquire_single_instance():
    """单实例锁：锁文件里的PID还活着=已有实例（权威）；端口信号仅辅助记录。

    本机WSL镜像网络会让端口表出现幽灵占用（bind报EADDRINUSE但netstat查无此人），
    所以绝不能只凭端口拒绝启动。返回值：socket=拿到端口锁；False=无锁但放行；None=确有实例。
    """
    lock_file = HERE / "widget.lock"
    try:
        old_pid = int(lock_file.read_text().strip() or 0)
    except Exception:
        old_pid = 0
    if _pid_alive_python(old_pid):
        _log(f"锁文件指向存活实例 PID {old_pid} → 拒绝启动")
        return None
    if old_pid:
        _log(f"清理陈旧锁文件（PID {old_pid} 已退出）")
    try:
        lock_file.write_text(str(os.getpid()))
    except Exception as e:
        _log(f"⚠️ 锁文件写入失败({e})，跳过单实例保护")
        return False

    # 端口锁只作辅助：占用异常一律只记日志，不影响启动
    for port in (47613, 25173, 31541):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            _log(f"辅助端口锁 {port} 已占用")
            return s
        except OSError as e:
            _log(f"端口 {port} 绑定异常({getattr(e, 'winerror', e.errno)})，跳过")
    _log("端口锁全失败，以锁文件为准继续运行")
    return False

# 头像优先级：自定义GIF > 自定义PNG > 默认绘制(带眨眼)
CUSTOM_GIF = HERE / "avatar.gif"
CUSTOM_PNG = HERE / "avatar.png"
DEFAULT_IMG = HERE / "sprite_avatar.png"


def make_default():
    """生成默认表情帧：睁眼/眨眼/干活(星眼+汗滴)/出错(哭)，逐帧已存在则跳过。"""
    from PIL import ImageDraw

    def base():
        img = Image.new("RGB", (200, 200), (250, 240, 255))
        d = ImageDraw.Draw(img)
        d.ellipse([25, 15, 175, 165], fill=(255, 228, 225), outline=(216, 112, 147), width=4)
        d.ellipse([38, 85, 62, 108], fill=(255, 190, 190)); d.ellipse([138, 85, 162, 108], fill=(255, 190, 190))
        return img, d

    out = HERE / "sprite_avatar.png"
    if not out.exists():
        img, d = base()
        d.ellipse([60, 60, 85, 88], fill=(60,50,60)); d.ellipse([115, 60, 140, 88], fill=(60,50,60))
        d.ellipse([66, 66, 79, 79], fill=(255,255,255)); d.ellipse([121, 66, 134, 79], fill=(255,255,255))
        d.arc([75, 88, 125, 125], 15, 165, fill=(220, 100, 120), width=4)
        img.save(out)
    out = HERE / "sprite_avatar_blink.png"
    if not out.exists():
        img, d = base()
        d.line([60, 72, 85, 72], fill=(60,50,60), width=4); d.line([115, 72, 140, 72], fill=(60,50,60), width=4)
        d.arc([75, 88, 125, 125], 15, 165, fill=(220, 100, 120), width=4)
        img.save(out)
    out = HERE / "sprite_avatar_working.png"
    if not out.exists():
        img, d = base()  # 星星眼 + 汗滴 + O型嘴
        d.polygon([(60,74),(70,62),(80,74),(70,86)], fill=(255,170,40))
        d.polygon([(115,74),(125,62),(135,74),(125,86)], fill=(255,170,40))
        d.polygon([(60,74),(70,66),(80,74),(70,82)], fill=(255,235,150))
        d.polygon([(115,74),(125,66),(135,74),(125,82)], fill=(255,235,150))
        d.ellipse([92, 92, 108, 112], fill=(220, 100, 120))
        d.polygon([(152,30),(164,44),(156,46),(148,34)], fill=(120,190,240))  # 汗滴
        img.save(out)
    out = HERE / "sprite_avatar_error.png"
    if not out.exists():
        img, d = base()  # 闭眼哭 + 泪滴 + 撇嘴
        d.arc([58, 62, 88, 84], 20, 160, fill=(60,50,60), width=4)
        d.arc([113, 62, 143, 84], 20, 160, fill=(60,50,60), width=4)
        d.arc([75, 112, 125, 148], 195, 345, fill=(220, 100, 120), width=4)
        d.polygon([(88,92),(98,106),(90,108),(82,96)], fill=(120,190,240))  # 泪滴
        img.save(out)


class AvatarAnimator:
    """管理头像动画帧（GIF帧循环 或 PNG表情帧），按状态(idle/working/error)切帧组。"""
    def __init__(self, root):
        self.root = root
        self.frames = []       # 兼容旧引用：当前idle帧组
        self.variants = {}     # {状态: [(PhotoImage, duration_ms), ...]}
        self.variant = "idle"
        self.frame_idx = 0
        self.bob_offset = 0    # 上下浮动偏移
        self.bob_direction = 1

        if CUSTOM_GIF.exists():
            self._load_gif()
        elif CUSTOM_PNG.exists():
            self._load_png(CUSTOM_PNG)
        else:
            make_default()
            self._load_png(DEFAULT_IMG, blink=True)

    def _load_gif(self):
        frames = []
        gif = Image.open(CUSTOM_GIF)
        for frame in ImageSequence.Iterator(gif):
            duration = frame.info.get("duration", 100) or 100
            frames.append((self._photo(frame), duration))
        self.variants["idle"] = frames
        self.frames = frames
        _log(f"已加载GIF头像（{len(frames)}帧）")

    def _try_frame(self, path, dur):
        if path.exists():
            return (self._photo(Image.open(path)), dur)
        return None

    def _load_png(self, path, blink=False):
        p = path.parent
        normal = (self._photo(Image.open(path)), 3000)
        if not blink:
            # 自定义头像也支持表情套图：同名 _blink/_working/_error 姊妹帧
            idle = [normal]
            blink_f = self._try_frame(path.with_name(path.stem + "_blink.png"), 150)
            work_f = self._try_frame(path.with_name(path.stem + "_working.png"), 650)
            err_f = self._try_frame(path.with_name(path.stem + "_error.png"), 3000)
            if blink_f: idle.append(blink_f)
            self.variants["idle"] = idle
            self.frames = idle
            if work_f: self.variants["working"] = [work_f, normal]
            if err_f: self.variants["error"] = [err_f]
            n = 1 + sum(x is not None for x in (blink_f, work_f, err_f))
            _log(f"已加载自定义头像（{n}态套图）" if n > 1 else "已加载自定义头像（静态）")
            return
        blink_f = (self._photo(Image.open(p / "sprite_avatar_blink.png")), 150)
        self.variants["idle"] = [normal, blink_f]
        self.frames = self.variants["idle"]
        work_png, err_png = p / "sprite_avatar_working.png", p / "sprite_avatar_error.png"
        if work_png.exists() and err_png.exists():
            self.variants["working"] = [(self._photo(Image.open(work_png)), 650), normal]
            self.variants["error"] = [(self._photo(Image.open(err_png)), 3000)]
            _log("已加载默认头像（睁眼/眨眼/干活/出错 四态）")
        else:
            _log("已加载默认头像（带眨眼）")

    def set_variant(self, name):
        """切换状态帧组；自定义头像没有专属帧组时自动回退idle帧。"""
        frames = self.variants.get(name) or self.variants.get("idle") or []
        if frames:
            self.variant = name
            self.frame_idx = 0

    @staticmethod
    def _photo(pil_img):
        """先补成正方形再缩放到显示尺寸：非方形图不会被硬拉变形。"""
        pil_img = pil_img.convert("RGBA")
        w, h = pil_img.size
        side = max(w, h)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(pil_img, ((side - w) // 2, (side - h) // 2))
        return ImageTk.PhotoImage(canvas.resize((DISPLAY_SIZE, DISPLAY_SIZE), Image.LANCZOS))

    def _active(self):
        return self.variants.get(self.variant) or self.variants.get("idle") or []

    def next_frame(self):
        frames = self._active()
        if not frames: return None, 3000
        self.frame_idx = (self.frame_idx + 1) % len(frames)
        img, dur = frames[self.frame_idx]
        return img, dur

    def current(self):
        frames = self._active()
        if not frames: return None, 3000
        img, dur = frames[self.frame_idx]
        return img, dur


class Widget:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("sprite")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.results = queue.Queue()
        self.drag = {"x": 0, "y": 0, "moved": False}
        self.animator = AvatarAnimator(self.root)
        self.mode = "collapsed"
        self.state = "idle"            # idle / working / error
        self._nudged = False
        self._cur_w, self._cur_h = 100, 124

        self._build_ui()  # 两套界面只建一次，永不销毁（杜绝切换白屏竞态）
        sw = self.root.winfo_screenwidth(); sh = self.root.winfo_screenheight()
        self.root.geometry(f"100x124+{sw-130}+{sh-220}")

        # 部署自测钩子：存在标记文件则3秒后自动展开（供无GUI环境验证渲染）
        if (HERE / "widget_autotest").exists():
            self.root.after(3000, self._expand)
        self.root.after(300, self._poll)
        self.root.after(400, self._monitor)
        self._animate()
        # 全局兜底退出：任何模式下右键都有菜单，Esc 收起
        self.root.bind_all("<Button-3>", lambda e: self._menu())
        self.root.bind_all("<Escape>", lambda e: self._collapsed())
        self.root.mainloop()

    def _build_ui(self):
        """一次性构建收起态与展开态两套界面。切换=显示/隐藏，不销毁不重建。"""
        img, _ = self.animator.current()

        # ── 收起态：小窗 + 头像 ──
        self.collapsed_frame = tk.Frame(self.root, bg=BG, bd=2, relief="solid", cursor="hand2")
        self.avatar_small = tk.Label(self.collapsed_frame, image=img, bg=BG, cursor="hand2")
        self.avatar_small.image = img  # 防GC：引用挂长生命周期属性
        self.avatar_small.pack(padx=3, pady=(3, 0))
        self.status_strip = tk.Label(self.collapsed_frame, text="● 待命",
                                     font=("Microsoft YaHei UI", 9), bg=BG, fg="#66BB6A")
        self.status_strip.pack(pady=(0, 4))
        for w in [self.collapsed_frame, self.avatar_small]:
            # 点击/拖拽分离：按下记录、移动超阈值=拖窗、松开未拖动=点击展开
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._release_expand)
            w.bind("<Button-3>", lambda e: self._menu())
        self.collapsed_frame.pack(fill="both", expand=True, padx=2, pady=2)

        # ── 展开态：完整面板（初始不显示）──
        self.expanded_frame = tk.Frame(self.root, bg=BG, bd=2, relief="solid")

        header = tk.Frame(self.expanded_frame, bg="#e8d5f0", cursor="fleur")
        header.pack(fill="x")
        self.avatar_label = tk.Label(header, image=img, bg="#e8d5f0")
        self.avatar_label.image = img  # 防GC
        self.avatar_label.pack(side="left", padx=4, pady=2)
        tk.Label(header, text=f" {PET_NAME} ✨", font=("Microsoft YaHei UI", 11, "bold"),
                 bg="#e8d5f0", fg="#6a2a8a").pack(side="left")
        self.status_dot = tk.Label(header, text="●", bg="#e8d5f0", fg="#66BB6A",
                                   font=("Microsoft YaHei UI", 10))
        self.status_dot.pack(side="left")
        self.status_text = tk.Label(header, text="待命", bg="#e8d5f0", fg="#66BB6A",
                                    font=("Microsoft YaHei UI", 9))
        self.status_text.pack(side="left")
        tk.Button(header, text="✕", command=self._quit,
                  font=("Microsoft YaHei UI", 9, "bold"), bg="#e57373", fg="white",
                  relief="flat", cursor="hand2", bd=0, padx=6
                  ).pack(side="right", padx=(0, 4), pady=2)
        tk.Button(header, text="收起 ▸", command=self._collapsed,
                  font=("Microsoft YaHei UI", 9), bg="#c39bd3", fg="white",
                  relief="flat", cursor="hand2", bd=0, padx=8
                  ).pack(side="right", padx=4, pady=2)
        for w in [header] + list(header.winfo_children()):
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonPress-1>", self._drag_start)

        btn_frame = tk.Frame(self.expanded_frame, bg=BG)
        btn_frame.pack(fill="x", padx=6, pady=(3, 0))
        btns = [
            ("🔊 自检输出", self._selftest, "#00BCD4"),
            ("📺 B站 启动", lambda: self._run("./start.sh bili", "B站启动"), "#4CAF50"),
            ("📺 B站 停止", lambda: self._run("./stop.sh bili", "B站停止"), "#ef5350"),
            ("💬 QQ 启动", lambda: self._run("./start.sh qq", "QQ启动"), "#2196F3"),
            ("💬 QQ 停止", lambda: self._run("./stop.sh qq", "QQ停止"), "#ef5350"),
            ("📝 今日综述", lambda: self._run(".venv/bin/python -u main.py daily-digest --prune 2>&1 | tail -10", "综述", timeout=420), "#FF9800"),
            ("🕐 补昨日综述", lambda: self._run(".venv/bin/python -u main.py daily-digest --date $(date -d yesterday +%F) --prune 2>&1 | tail -12", "补综述", timeout=420), "#FF7043"),
            ("📥 抓新论文", lambda: self._run(".venv/bin/python -u main.py ingest --absorb 2>&1 | tail -12", "抓论文", timeout=600), "#795548"),
            ("📄 吸收论文", self._pick_paper, "#8BC34A"),
            ("📊 状态", lambda: self._run("./status.sh 2>&1", "状态"), "#3F51B5"),
            ("🧹 保洁", lambda: self._run(".venv/bin/python -u main.py housekeep --no-notify 2>&1 | tail -5", "保洁"), "#9C27B0"),
            ("⚙️ 编辑配置", self._open_config, "#FFC107"),
        ]
        for text, cmd, color in btns:
            tk.Button(btn_frame, text=text, command=cmd,
                      font=("Microsoft YaHei UI", 10), bg=color, fg="white",
                      relief="flat", cursor="hand2", height=1, bd=0
                      ).pack(fill="x", pady=1)

        tk.Label(self.expanded_frame, text="─ 输出 ─", font=("Microsoft YaHei UI", 8),
                 bg=BG, fg="#999").pack(anchor="w", padx=10, pady=(5, 0))
        self.output_area = tk.Text(self.expanded_frame, wrap="word", font=("Consolas", 9),
                                   bg="#1a1a2e", fg="#00e676", bd=0,
                                   padx=8, pady=6, height=8, relief="flat")
        self.output_area.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self.output_area.insert("1.0", "等待操作...\n点击上方按钮执行")
        self.output_area.config(state="disabled")

    def _animate(self):
        """头像动画循环：两个头像标签（收起态/展开态）都安全刷新。"""
        img, dur = self.animator.next_frame()
        if img:
            for lbl in (getattr(self, "avatar_label", None),
                        getattr(self, "avatar_small", None)):
                if lbl is not None:
                    try:
                        lbl.config(image=img)
                        lbl.image = img  # 防GC
                    except tk.TclError:
                        pass  # 窗口销毁竞态，跳过本帧
        self.root.after(dur if self.animator.frames else 3000, self._animate)

    def _poll(self):
        try:
            while True:
                try:
                    label, text = self.results.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._show(label, text)
                except Exception as e:
                    _log(f"_poll 显示[{label}]异常: {e!r}")
        finally:
            self.root.after(300, self._poll)  # finally里重调度：任何异常都杀不死轮询

    def _monitor(self):
        """飞行记录仪：窗口尺寸/位置/模式一变化就落日志，定位渲染异常。"""
        cur = (self.mode, self.root.winfo_width(), self.root.winfo_height(),
               self.root.winfo_x(), self.root.winfo_y())
        if cur != getattr(self, "_mon_last", None):
            _log(f"监测: 模式={cur[0]} 窗口={cur[1]}x{cur[2]} 位置=({cur[3]},{cur[4]})")
            self._mon_last = cur
        self.root.after(400, self._monitor)

    def _show(self, label, text):
        # 登录过期提示只对QQ启动类任务触发，避免误杀其他输出
        if label == "QQ启动" and ("未就绪" in text or "掉线" in text):
            self._set_state("error")
            # 先同步给占位提示，输出区绝不停留在空白
            self._show_text("⚠️ 检测到QQ登录态过期！\n正在读取WebUI Token…")
            self._handle_login_expired()
            return
        self._set_state("error" if (text.startswith("错误") or text.startswith("⚠️")) else "idle")
        self._show_text(f"[{label}]\n{text}")

    def _show_text(self, msg):
        """覆写输出区（只允许主线程调用；后台线程一律走 root.after 投递）。"""
        if not hasattr(self, "output_area"):
            return
        self.output_area.config(state="normal")
        self.output_area.delete("1.0", "end")
        self.output_area.insert("1.0", msg)
        self.output_area.config(state="disabled")

    def _handle_login_expired(self):
        """登录过期：动态读Token + 弹浏览器 + 显示指引（UI更新全部经 root.after 回主线程）。"""
        def fetch_token():
            try:
                r = subprocess.run(
                    WSL_CMD + [
                        f"sudo cat {NAPCAT_DIR}/opt/QQ/resources/app/app_launcher/napcat/config/webui.json"
                        " | grep '\"token\"' | head -1 | sed 's/.*: \"//;s/\".*//'"
                    ],
                    capture_output=True, timeout=10,
                    encoding="utf-8", errors="replace",
                )
                token = (r.stdout or "").strip()
            except Exception:
                token = "(读取失败，手动查看 webui.json)"
            msg = (
                "⚠️⚠️ QQ登录态过期！⚠️⚠️\n\n"
                "已自动打开WebUI，请扫码：\n"
                "1. 浏览器已弹出 http://localhost:6099/webui\n"
                f"2. Token: {token}\n"
                "3. QQ登录页 → 手机扫码(小号)\n"
                "4. 完成后回来点「💬 QQ 启动」\n"
            )
            # 线程安全：所有UI操作投递回主线程执行
            self.root.after(0, lambda: self._show_text(msg))
            self.root.after(0, lambda: __import__("webbrowser").open("http://localhost:6099/webui"))
        threading.Thread(target=fetch_token, daemon=True).start()

    def _open_config(self):
        """用记事本打开config.toml（动态获取Windows路径，不硬编码发行版名）。"""
        # 用 wslpath 把 Linux 路径转成 Windows UNC 路径（自动识别发行版名）
        try:
            r = subprocess.run(
                ["wsl", "-e", "bash", "-c",
                 f"wslpath -w {SPRITE_DIR}/config.toml"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            config_path = r.stdout.strip()
        except Exception:
            config_path = ""
        if not config_path:
            if hasattr(self, "output_area"):
                self.output_area.config(state="normal")
                self.output_area.delete("1.0", "end")
                self.output_area.insert("1.0",
                    "[设置] 无法获取配置路径。\n"
                    "请在WSL终端手动编辑：\n"
                    "nano ~/workspace/sprite/config.toml\n")
                self.output_area.config(state="disabled")
            return

        subprocess.Popen(["notepad.exe", config_path])
        if hasattr(self, "output_area"):
            self.output_area.config(state="normal")
            self.output_area.delete("1.0", "end")
            self.output_area.insert("1.0",
                "[设置] config.toml 已在记事本打开\n"
                "修改后保存(Ctrl+S) → 重启对应服务生效\n\n"
                "常用配置：\n"
                "  [obsidian] vault_path = vault路径\n"
                "  [llm] api_key = GLM Key\n"
                "  [watch] keywords = 关键词\n"
                "  [qq] owner_qq = 主人QQ\n")
            self.output_area.config(state="disabled")

    def _run(self, cmd, label, timeout=120):
        self._set_state("working")
        if not SPRITE_DIR:
            self.results.put((label,
                "⚠️ 未找到部署路径文件 sprite_dir.txt。\n"
                "请先在WSL仓库目录执行: ./start.sh widget\n"
                "（它会部署桌宠并写入路径，之后按钮即可用）"))
            return
        _log(f"按钮[{label}] 派发: {cmd[:60]}")
        def worker():
            self.results.put((label, "执行中..."))
            try:
                r = subprocess.run(WSL_CMD + [f"cd {SPRITE_DIR} && {cmd}"],
                                   capture_output=True, timeout=timeout,
                                   encoding="utf-8", errors="replace")
                out = ((r.stdout or "") + (r.stderr or "")).strip()
                _log(f"任务[{label}] 完成，{len(out)}字符")
                self.results.put((label, out[:800] if out else "(无输出)"))
            except subprocess.TimeoutExpired:
                # wsl.exe被杀但WSL内的任务往往还在跑，别当成失败
                _log(f"任务[{label}] 超{timeout}秒放弃等待")
                self.results.put((label,
                    f"⏱ 等待超{timeout}秒，窗口不再等待。\n"
                    f"任务大概率仍在WSL后台执行，稍后可验证：\n"
                    f"cd {SPRITE_DIR} && {cmd}"))
            except Exception as e:
                _log(f"任务[{label}] 异常: {e!r}")
                self.results.put((label, f"错误: {e}"))
        threading.Thread(target=worker, daemon=True).start()

    def _collapsed(self):
        """收起：隐藏面板、显示小窗。不销毁任何控件。"""
        self.mode = "collapsed"
        self.state = "idle"            # idle / working / error
        self._nudged = False
        self._cur_w, self._cur_h = 100, 124
        self.expanded_frame.pack_forget()
        self.collapsed_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self.root.minsize(0, 0)
        self.root.geometry(f"100x124+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def _expand(self):
        """展开：显示面板、隐藏小窗。不销毁不重建，从机制上杜绝切换白屏。"""
        self.mode = "expanded"
        x0, y0 = self.root.winfo_x(), self.root.winfo_y()
        self.collapsed_frame.pack_forget()
        self.expanded_frame.pack(fill="both", expand=True, padx=2, pady=2)

        # 高度自适应：150% DPI下控件普遍变大，写死高度会把输出区挤成0像素
        self.root.update_idletasks()
        W = max(250, self.expanded_frame.winfo_reqwidth() + 8)
        H = max(560, self.expanded_frame.winfo_reqheight() + 10)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(0, min(x0, sw - W - 8))
        y = max(0, min(y0, sh - H - 48))  # 底部预留任务栏
        self.root.minsize(W, H)
        self._cur_w, self._cur_h = W, H  # 拖拽时用的目标尺寸
        self.root.geometry(f"{W}x{H}+{x}+{y}")
        # 有些环境下首次geometry会被后续布局处理覆盖 → 验证+二次强制
        self.root.update_idletasks()
        aw, ah = self.root.winfo_width(), self.root.winfo_height()
        if abs(aw - W) > 4 or abs(ah - H) > 4:
            _log(f"⚠️ 首次geometry未生效(实测{aw}x{ah})，二次强制")
            self.root.geometry(f"{W}x{H}+{x}+{y}")
            self.root.update_idletasks()

        def _verify():
            # 对抗延迟的尺寸回缩：只要还在展开态且尺寸缩水，就地恢复
            if getattr(self, "mode", "") != "expanded":
                return
            cw, ch = self.root.winfo_width(), self.root.winfo_height()
            if cw < W - 4 or ch < H - 4:
                _log(f"⚠️ 展开态被压缩到{cw}x{ch}，恢复")
                self.root.geometry(
                    f"{W}x{H}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self.root.after(50, _verify)
        self.root.after(200, _verify)
        _log(f"展开: 设{W}x{H} 实测{self.root.winfo_width()}x{self.root.winfo_height()} "
             f"起点({x0},{y0})→({x},{y}) 屏{sw}x{sh} 需高{self.expanded_frame.winfo_reqheight()}")

    def _selftest(self):
        """绕过队列/线程/WSL直写输出区——一次点击定位断点在哪一环。"""
        msg = (f"🔊 自检OK {time.strftime('%H:%M:%S')}\n\n"
               "输出区直写正常。\n"
               "→ 若功能按钮仍无输出：问题在WSL命令/队列，看 widget.log\n"
               "→ 若本行都看不见：输出区布局问题（高度被挤没）")
        self._show_text(msg)
        _log("自检按钮：已直写输出区")

    def _pick_paper(self):
        from tkinter import filedialog
        files = filedialog.askopenfilenames(title="选择论文PDF",
                                            filetypes=[("PDF", "*.pdf")])
        if not files: return
        wsl_files = [f"/mnt/{f[0].lower()}/{f[3:].replace(chr(92), '/')}" for f in files]
        self._run(f".venv/bin/python -u main.py paper {' '.join(wsl_files)} 2>&1 | tail -15", "论文入库", timeout=600)

    def _quit(self):
        """退出：先正常销毁窗口，再硬退出兜底——任何线程都拦不住。"""
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def _menu(self):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="展开 / 收起", command=self._toggle)
        m.add_separator()
        m.add_command(label="退出", command=self._quit)
        m.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def _toggle(self):
        if getattr(self, "mode", "collapsed") == "expanded":
            self._collapsed()
        else:
            self._expand()

    STATE_META = {"idle": ("● 待命", "#66BB6A"), "working": ("● 干活中…", "#FFA726"),
                  "error": ("● 出错了", "#EF5350")}

    def _set_state(self, state):
        """状态驱动：表情帧组 + 收起态状态条 + 展开态状态点。"""
        self.state = state
        self.animator.set_variant(state)
        txt, fg = self.STATE_META[state]
        if hasattr(self, "status_strip"):
            self.status_strip.config(text=txt, fg=fg)
        if hasattr(self, "status_text"):
            self.status_text.config(text=txt.replace("● ", ""), fg=fg)
            self.status_dot.config(fg=fg)

    def _nudge(self, dy):
        """点击反馈：整个窗体下沉dy像素再弹回，任何头像通用。"""
        try:
            self.root.geometry(f"+{self.root.winfo_x()}+{self.root.winfo_y() + dy}")
        except tk.TclError:
            pass

    def _drag_start(self, e):
        self.drag = {"x": e.x, "y": e.y, "moved": False}
        if self.mode == "collapsed" and not self._nudged:
            self._nudged = True
            self._nudge(3)

    def _drag_move(self, e):
        if self._nudged:
            self._nudge(-3); self._nudged = False
        # 记录是否发生了真实拖动（用于区分"点击"与"拖拽"）
        if abs(e.x - self.drag["x"]) + abs(e.y - self.drag["y"]) > 6:
            self.drag["moved"] = True
        # 关键修复：必须用带尺寸的完整geometry！Windows无边框窗口上
        # 只带位置的 geometry("+x+y") 会把尺寸重置回100x100（十连复现实锤）
        w = getattr(self, "_cur_w", 100)
        h = getattr(self, "_cur_h", 100)
        self.root.geometry(
            f"{w}x{h}"
            f"+{self.root.winfo_x() + e.x - self.drag['x']}"
            f"+{self.root.winfo_y() + e.y - self.drag['y']}")

    def _release_expand(self, e):
        """收起态松开鼠标：没拖动过就是点击 → 展开。"""
        if self._nudged:
            self._nudge(-3); self._nudged = False
        if not self.drag.get("moved"):
            self._expand()

if __name__ == "__main__":
    _lock = _acquire_single_instance()
    if _lock is None:
        _log("启动被拒绝：检测到已有桌宠实例")
        import tkinter.messagebox as _mb
        _r = tk.Tk(); _r.withdraw()
        _mb.showinfo("sprite 桌宠", "桌宠已经在运行了（看看屏幕右下角）。\n本次启动自动退出。")
        sys.exit(0)
    _log(f"桌宠启动 v20 部署环境: SPRITE_DIR={SPRITE_DIR!r} NAPCAT_DIR={NAPCAT_DIR!r} PET_NAME={PET_NAME!r}")
    Widget()
