"""OneBot v11 客户端：正向 WebSocket 连接 NapCat，收事件 / 发 API。

协议要点（onebot-11 / NapCat）：
- 事件推送：JSON 文本帧，post_type=="message" 为消息事件
- API 调用：{"action": "...", "params": {...}, "echo": "n"}，响应按 echo 原样带回
"""

import asyncio
import itertools
import json

import websockets


class OneBotClient:
    def __init__(self, ws_url: str, on_message, access_token: str = ""):
        """on_message: async callable(dict) —— 收到消息事件时回调（已含 self_id 等）。"""
        self.ws_url = ws_url
        self.on_message = on_message
        self.access_token = access_token
        self._ws = None
        self._echo = itertools.count(1)
        self._pending: dict[str, asyncio.Future] = {}

    # ---------- 生命周期 ----------

    async def run(self) -> None:
        """常驻：断线自动重连（指数退避，上限5分钟）。"""
        backoff = 3
        while True:
            try:
                headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}
                async with websockets.connect(
                    self.ws_url, additional_headers=headers, ping_interval=20, ping_timeout=20
                ) as ws:
                    self._ws = ws
                    backoff = 3
                    print(f"已连接 OneBot 实现：{self.ws_url}")
                    await self._recv_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"OneBot 连接断开（{e}），{backoff}s 后重连…")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _recv_loop(self, ws) -> None:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            echo = data.get("echo")
            if echo and echo in self._pending:
                fut = self._pending.pop(echo)
                if not fut.done():
                    fut.set_result(data)
                continue
            if data.get("post_type") == "message":
                # 并发处理：绝不能 await 在线——否则 LLM 生成期间收不了新消息/回执（历史bug：回复集体迟到30秒）
                asyncio.create_task(self._safe_on_message(data))

    async def _safe_on_message(self, data: dict) -> None:
        try:
            await self.on_message(data)
        except Exception as e:
            print(f"处理消息事件出错：{e!r}")

    # ---------- API ----------

    async def call(self, action: str, params: dict, timeout: float = 30.0) -> dict:
        if self._ws is None:
            raise RuntimeError("OneBot WebSocket 未连接")
        echo = str(next(self._echo))
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[echo] = fut
        await self._ws.send(json.dumps({"action": action, "params": params, "echo": echo}))
        try:
            resp = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(echo, None)  # 防泄漏
            raise
        if resp.get("retcode") not in (0, 1):  # 0=ok 1=async ok
            raise RuntimeError(f"OneBot {action} 失败: retcode={resp.get('retcode')} {resp.get('wording')}")
        return resp.get("data") or {}

    async def send_group_text(self, group_id: int, text: str, at_user: int | None = None) -> None:
        segs = []
        if at_user:
            segs.append({"type": "at", "data": {"qq": str(at_user)}})
            text = " " + text
        segs.append({"type": "text", "data": {"text": text}})
        await self.call("send_group_msg", {"group_id": group_id, "message": segs})

    async def send_private_text(self, user_id: int, text: str) -> None:
        await self.call("send_private_msg", {"user_id": user_id,
                                             "message": [{"type": "text", "data": {"text": text}}]})
