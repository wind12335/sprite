"""B 站侧封装：扫码登录、评论收发、发动态、上传视频。

底层走 bilibili-api-python（非官方库，自带 WBI 签名、分片上传等细节）。
注意：本模块只应操作你自己的账号、你自己视频的评论区。
"""

import json

from bilibili_api import Credential, comment, dynamic, user, video_uploader

from ..config import CRED_FILE, DATA_DIR


def _print_qr_compact(link: str) -> None:
    """紧凑版终端二维码：半块字符两行并一行 + 真实黑白配色，约为默认渲染的 1/4 大小。"""
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(link)
    qr.make(fit=True)
    m = qr.get_matrix()
    # (上行模块,下行模块) → ANSI前景/背景 + 半块字符；黑白用色块表达，与终端主题无关
    cells = {
        (False, False): "\033[0;37;47m ",  # 白
        (True, False): "\033[0;30;47m▀",   # 上黑下白
        (False, True): "\033[0;30;47m▄",   # 上白下黑
        (True, True): "\033[0;30;40m ",    # 黑
    }
    pad = "\033[0;37;47m  "
    for y in range(0, len(m), 2):
        top = m[y]
        bot = m[y + 1] if y + 1 < len(m) else [False] * len(m[0])
        print(pad + "".join(cells[(top[x], bot[x])] for x in range(len(m[0]))) + pad + "\033[0m")
    try:  # PNG 兜底：终端实在扫不出时用图片
        qrcode.make(link).save(DATA_DIR / "qrcode.png")
        print("（扫码不顺利可打开图片：data/qrcode.png）")
    except Exception:
        pass


def qr_login() -> Credential:
    """扫码登录（直接走 B 站 passport 接口，不依赖库的脆弱解析），凭据存 data/credential.json。"""
    import time as _time

    from curl_cffi import requests

    sess = requests.Session(impersonate="chrome")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    gen = sess.get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
        headers=headers, timeout=15,
    ).json()
    if gen.get("code") != 0:
        raise SystemExit(f"生成二维码失败：{gen}")
    qr_link, qr_key = gen["data"]["url"], gen["data"]["qrcode_key"]
    _print_qr_compact(qr_link)
    print("请用 B 站 App 扫码并确认登录（二维码约 3 分钟有效）…")

    while True:
        _time.sleep(2)
        poll = sess.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": qr_key}, headers=headers, timeout=15,
        ).json()
        code = poll.get("data", {}).get("code", poll.get("code"))
        if code == 86101:
            continue
        if code == 86090:
            print("已扫码，等待确认…")
            continue
        if code == 86038:
            raise SystemExit("二维码已过期，请重新执行 login")
        if code != 0:
            raise SystemExit(f"扫码登录失败：{poll}")

        # 新版流程：poll 返回的 crossDomain 链接只带 ticket，
        # 访问它才会通过 Set-Cookie 种下真正的登录凭据
        cross_url = poll["data"]["url"]
        sess.get(cross_url, headers=headers, timeout=15)
        sessdata = sess.cookies.get("SESSDATA", "")
        bili_jct = sess.cookies.get("bili_jct", "")
        dedeuserid = sess.cookies.get("DedeUserID", "")
        if not sessdata:
            try:
                names = list(sess.cookies.keys())
            except Exception:
                names = "（无法枚举）"
            raise SystemExit(
                f"访问登录回调({cross_url[:80]}…)后仍未拿到 SESSDATA，当前cookie：{names}"
            )
        cred_dict = {
            "sessdata": sessdata,
            "bili_jct": bili_jct,
            "dedeuserid": dedeuserid,
            "refresh_token": poll["data"].get("refresh_token", ""),
        }
        # buvid3（部分接口的风控需要），crossDomain 没种上就补访问首页拿
        buvid3 = sess.cookies.get("buvid3", "")
        if not buvid3:
            try:
                sess.get("https://www.bilibili.com", headers=headers, timeout=15)
                buvid3 = sess.cookies.get("buvid3", "")
            except Exception:
                pass
        if buvid3:
            cred_dict["buvid3"] = buvid3
        # 把会话里种下的全部 cookie 都存下来：member（专栏/投稿）等创作端接口
        # 比 open 接口挑食，只带 SESSDATA/bili_jct 可能拿不到 JSON
        try:
            cred_dict["cookies"] = {k: sess.cookies.get(k, "") for k in sess.cookies.keys()}
        except Exception:
            pass
        CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
        CRED_FILE.write_text(json.dumps(cred_dict, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"登录成功，凭据已保存到 {CRED_FILE}（请勿泄露、勿提交到 git）")
        return load_credential()


def load_credential() -> Credential:
    if not CRED_FILE.exists():
        raise SystemExit("未找到登录凭据，请先执行: python main.py login")
    d = json.loads(CRED_FILE.read_text(encoding="utf-8"))
    if not d.get("sessdata"):
        raise SystemExit(
            "data/credential.json 里 sessdata 为空（旧版登录的残留），请重新执行: python main.py login"
        )
    return Credential(
        sessdata=d["sessdata"],
        bili_jct=d.get("bili_jct"),
        dedeuserid=d.get("dedeuserid"),
        buvid3=d.get("buvid3"),
        ac_time_value=d.get("refresh_token"),
    )


def _rpid(c: dict) -> int:
    """兼容新旧接口里 rpid 字段的两种形态。"""
    rpid = c.get("rpid")
    if isinstance(rpid, dict):
        rpid = rpid.get("rpid")
    return int(rpid)


class Bili:
    def __init__(self, credential: Credential):
        self.cred = credential

    async def my_info(self) -> dict:
        return await user.get_self_info(credential=self.cred)

    async def my_videos(self, count: int = 5) -> list[dict]:
        info = await self.my_info()
        u = user.User(uid=info["mid"], credential=self.cred)
        res = await u.get_videos(pn=1, ps=count)
        return res["list"]["vlist"]

    async def video_comments(self, aid: int, page_index: int = 1) -> dict:
        return await comment.get_comments(
            oid=aid,
            type_=comment.CommentResourceType.VIDEO,
            page_index=page_index,
            credential=self.cred,
        )

    async def reply_comment(self, aid: int, rpid: int, text: str) -> dict:
        """回复某条根评论（楼中楼会挂在该评论下）。"""
        return await comment.send_comment(
            text=text,
            oid=aid,
            type_=comment.CommentResourceType.VIDEO,
            root=rpid,
            credential=self.cred,
        )

    async def post_video_comment(self, aid: int, text: str) -> dict:
        """在视频下直接发表一级评论（区别于回复某条评论）。"""
        return await comment.send_comment(
            text=text,
            oid=aid,
            type_=comment.CommentResourceType.VIDEO,
            credential=self.cred,
        )

    async def post_image_dynamic(self, text: str, image_paths: list) -> dict:
        """图文动态：图片上传后与文字一起发。失败抛异常由调用方回退纯文字。"""
        from bilibili_api import dynamic
        from bilibili_api.utils.picture import Picture

        bd = dynamic.BuildDynamic()
        bd.add_text(text)
        for p in image_paths:
            up = await dynamic.upload_image(image=Picture.from_file(p), credential=self.cred)
            bd.add_image(up)
        return await dynamic.send_dynamic(info=bd, credential=self.cred)

    async def post_text_dynamic(self, text: str) -> dict:
        info = dynamic.BuildDynamic.create_by_args(text=text)
        return await dynamic.send_dynamic(info=info, credential=self.cred)

    async def post_dynamic_at(self, text: str, at_uid: int, at_uname: str) -> dict:
        """发动态并 @指定用户（对方会在 B 站 App 收到通知）。"""
        info = dynamic.BuildDynamic.empty().add_at(at_uid, at_uname).add_plain_text(text)
        return await dynamic.send_dynamic(info=info, credential=self.cred)

    async def get_uname(self, uid: int) -> str:
        u = user.User(uid=uid, credential=self.cred)
        return (await u.get_user_info())["name"]

    async def publish_article(self, title: str, content_html: str, category_id: int = 15) -> int:
        """发布专栏文章（实验性：bilibili-api 不支持写专栏，这里直接调创作中心接口）。

        需要账号已开通专栏权限。返回专栏 cv 号（aid）；B 站会先审核再公开。
        """
        from curl_cffi import requests

        # 合并登录时保存的完整 cookie（member 接口需要的不止 SESSDATA/bili_jct）
        cookies = {
            "SESSDATA": self.cred.sessdata,
            "bili_jct": self.cred.bili_jct,
            "DedeUserID": self.cred.dedeuserid or "",
        }
        try:
            extra = json.loads(CRED_FILE.read_text(encoding="utf-8")).get("cookies", {})
            cookies.update(extra)
        except Exception:
            pass
        resp = requests.post(
            "https://member.bilibili.com/x/vupre/web/article/v2/draft/add",
            data={
                "title": title,
                "content": content_html,
                "category_id": category_id,
                "csrf": self.cred.bili_jct,
                "csrf_token": self.cred.bili_jct,
            },
            cookies=cookies,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://member.bilibili.com/platform/upload/text/edit",
            },
            timeout=30,
            impersonate="chrome",
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"code={data.get('code')} msg={data.get('message')}")
        return int(data["data"]["aid"])

    async def upload_video(
        self,
        path: str,
        title: str,
        desc: str,
        tags: list[str],
        tid: int = 21,
        cover: str | None = None,
    ) -> dict:
        """上传本地视频并投稿。tid 是分区 ID（21=日常，130=音乐…，可在B站投稿页查）。"""
        page = video_uploader.VideoUploaderPage(path=path, title=title, description=desc)
        meta = video_uploader.VideoMeta(
            tid=tid, title=title, desc=desc, tags=tags, cover=cover, original=True
        )
        try:
            await meta.verify()
        except Exception as e:  # verify 只是预检，失败不阻断，投稿接口还会再校验一次
            print(f"警告：元数据预检未通过（{e}），继续尝试投稿…")
        uploader = video_uploader.VideoUploader(
            pages=[page], meta=meta, credential=self.cred
        )
        return await uploader.start()
