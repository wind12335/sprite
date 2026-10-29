"""迷你工具总线（MCP风格）：把 联网搜索/知识库/资讯 注册为带协议的标准工具，
模型自主决定调用哪个、最多 N 轮，最终带依据回答。厂商无关（纯文本协议，
DeepSeek/Qwen 同样可用），将来接社区 MCP 服务器只需实现同一注册接口。"""
import asyncio
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


def build_tools(kb=None, persona_kb_hint=True):
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
            hits = kb.search(q, k=3)
            if not hits:
                return "(本地知识库没有相关内容)"
            return "\n\n".join(f"【{h.get('file', '笔记')}】{h.get('text', '')[:400]}" for h in hits)
        tools["kb_search"] = Tool(
            "kb_search", "检索本地知识库（你的AI学习笔记/论文精读/综述），适合概念与已学内容",
            kb_search)
    return tools


TOOL_PROTOCOL = """

【工具系统】你可以调用工具获取信息：
{tool_list}

规则：需要工具时，你的整条回复只输出一行JSON：{{"tool":"工具名","query":"参数"}}
工具结果会以[工具结果]开头返回给你，你可以继续调用（最多{max_iters}次），
也可以基于结果直接给出最终回答（最终回答绝不要输出JSON）。
优先 kb_search 查已学内容；新事实/新闻/版本用 web_search；闲聊不需要工具。"""


class ToolBus:
    """单次问答的工具调用循环：模型驱动，最多 max_iters 轮工具调用。"""

    def __init__(self, llm, tools: dict, max_iters: int = 3):
        self.llm, self.tools, self.max_iters = llm, tools, max_iters

    def _protocol(self) -> str:
        listing = "\n".join(f"- {t.name}: {t.desc}" for t in self.tools.values())
        return TOOL_PROTOCOL.format(tool_list=listing, max_iters=self.max_iters)

    def _parse_call(self, text: str):
        """返回 (name, query) / ("__bad__", 拼错名) / None(=最终回答)。"""
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
            import difflib
            close = difflib.get_close_matches(name, list(self.tools), n=1, cutoff=0.6)
            if close:
                name = close[0]
            else:
                return "__bad__", str(name)
        return name, str(obj.get("query") or obj.get("args") or "")

    def run(self, system: str, user_text: str, max_tokens: int = 600, trace=None) -> str:
        msgs = [{"role": "system", "content": system + self._protocol()},
                {"role": "user", "content": user_text}]
        for _ in range(self.max_iters + 1):
            out = self.llm.chat(msgs, max_tokens=max_tokens).strip()
            call = self._parse_call(out)
            if call is None:
                # 仍是工具形态却解析失败：不当最终回答，纠一次
                if out.lstrip().startswith("{") and "{" in out and "}" in out:
                    msgs.append({"role": "assistant", "content": out})
                    msgs.append({"role": "user", "content":
                                 "（上一条JSON格式有误，请重发正确的工具调用JSON，或直接给最终回答）"})
                    continue
                return out  # 最终回答
            name, query = call
            if name == "__bad__":
                msgs.append({"role": "assistant", "content": out})
                msgs.append({"role": "user", "content":
                             f"（没有工具「{query}」。可用：{', '.join(self.tools)}。请重调或直接回答）"})
                continue
            result = self.tools[name].run(query)
            if trace is not None:
                trace.append((name, query, result[:120]))
            msgs.append({"role": "assistant", "content": out})
            msgs.append({"role": "user",
                         "content": f"[工具结果|{name}]\n{result}\n\n（请继续：再调工具或给最终回答）"})
        # 轮次耗尽：不带工具协议强制要最终回答
        msgs[-1]["content"] += "\n（工具次数已用完，请立刻给出最终回答）"
        return self.llm.chat(msgs, max_tokens=max_tokens).strip()
