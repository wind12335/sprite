"""迷你工具总线（MCP风格）：把 联网搜索/知识库/资讯 注册为带协议的标准工具，
模型自主决定调用哪个、最多 N 轮，最终带依据回答。厂商无关（纯文本JSON协议）。

分级路由省token（同watch两级筛选原理）：路由器(建议配flash)只做机械决策，
主模型只基于"紧凑工具备忘录"写最终人设回答——主模型输入规模≈v1直答。
"""
import difflib
import json
import re


class Tool:
    def __init__(self, name, desc, fn):
        self.name, self.desc, self.fn = name, desc, fn

    def run(self, query: str) -> str:
        try:
            out = self.fn(query.strip()) if query else self.fn()
            return str(out)[:2500] if out else "(工具无结果)"
        except Exception as e:
            return f"(工具执行出错: {e})"


def build_tools(kb=None):
    """组装本实例可用工具集。kb 为 None 时自动缺席 kb_search。"""
    tools = {}

    from ..flow.websearch import clean_query, format_results, search_web
    tools["web_search"] = Tool(
        "web_search", "联网搜索最新信息（新闻/版本/事实），返回标题+链接+摘要",
        lambda q: format_results(search_web(clean_query(q), 5)) or "(没搜到，换个词试试)")

    from ..flow.gossip import build_gossip
    tools["feed_news"] = Tool(
        "feed_news", "获取技术圈今日热门资讯(HN/Reddit/HF，30分钟缓存)，无需参数",
        lambda q="": build_gossip() or "(暂时没有新鲜资讯)")

    if kb is not None:
        def kb_search(q):
            kb.maybe_refresh()
            hits = kb.search(q, k=3)
            if not hits:
                return "(本地知识库没有相关内容)"
            return "\n\n".join(f"【{h.get('file', '笔记')}】{h.get('text', '')[:400]}" for h in hits)
        tools["kb_search"] = Tool(
            "kb_search", "检索本地知识库（你的AI学习笔记/论文精读/综述），适合概念与已学内容",
            kb_search)
    return tools


ROUTER_ENOUGH = "ENOUGH"


class ToolBus:
    """单次问答的工具调用循环。router_llm=None 时全部用主模型（旧行为）。"""

    def __init__(self, llm, tools: dict, max_iters: int = 3, router_llm=None):
        self.llm, self.tools, self.max_iters = llm, tools, max_iters
        self.router_llm = router_llm

    # ── 路由器系统提示词（只做机械决策）────────────────────────
    def _router_system(self) -> str:
        listing = "\n".join(f"- {t.name}: {t.desc}" for t in self.tools.values())
        return f"""你是工具路由器，只做机械决策，不写长回答。
可用工具：
{listing}
需要信息时只输出一行JSON：{{"tool":"工具名","query":"参数"}}
信息足够回答时只输出：ENOUGH
最多{self.max_iters}次调用。知识性/新闻/版本问题优先web_search，已学概念优先kb_search，闲聊直接ENOUGH。"""

    # ── 工具调用JSON解析：返回 (name,query) / ("__bad__",拼错名) / None
    def _parse_call(self, text: str):
        m = re.search(r"\{[^{}]*\"(tool|name)\"[^{}]*\}", text)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
        name = obj.get("tool") or obj.get("name")
        if not name:
            return None
        if name not in self.tools:
            close = difflib.get_close_matches(name, list(self.tools), n=1, cutoff=0.6)
            if close:
                name = close[0]
            else:
                return "__bad__", str(name)
        return name, str(obj.get("query") or obj.get("args") or "")

    @staticmethod
    def _looks_like_json(text: str) -> bool:
        return text.lstrip().startswith("{") and "}" in text

    # ── 主流程：路由循环(flash) → 主模型最终回答 ────────────────
    def run(self, system: str, user_text: str, max_tokens: int = 600, trace=None) -> str:
        from .guard import frame_data

        router = self.router_llm or self.llm
        msgs = [{"role": "system", "content": self._router_system()},
                {"role": "user", "content": user_text}]
        memo = []  # 工具备忘录：主模型回答只需要它，不重放路由全过程

        for _ in range(self.max_iters + 1):
            out = router.chat(msgs, max_tokens=200).strip()
            if ROUTER_ENOUGH in out or not self._looks_like_json(out):
                break  # 信息足够（或非JSON）：进入最终回答
            call = self._parse_call(out)
            if call is None:
                msgs.append({"role": "assistant", "content": out})
                msgs.append({"role": "user", "content":
                             "（JSON格式有误，重发工具调用JSON，或输出ENOUGH表示信息足够）"})
                continue
            name, query = call
            if name == "__bad__":
                msgs.append({"role": "assistant", "content": out})
                msgs.append({"role": "user", "content":
                             f"（没有工具「{query}」。可用：{', '.join(self.tools)}。重调或ENOUGH）"})
                continue
            result = self.tools[name].run(query)
            if trace is not None:
                trace.append((name, query, result[:120]))
            memo.append(f"[{name} | {query[:40]}]\n{result}")
            msgs.append({"role": "assistant", "content": out})
            msgs.append({"role": "user", "content":
                         f"[工具结果|{name}]\n{result}\n\n（继续调工具，或输出ENOUGH）"})

        final_user = user_text
        if memo:
            final_user += "\n\n" + frame_data("是刚调用工具取得的资料", "\n\n".join(memo))
        final_msgs = [{"role": "system", "content": system},
                      {"role": "user", "content": final_user}]
        return self.llm.chat(final_msgs, max_tokens=max_tokens).strip()
