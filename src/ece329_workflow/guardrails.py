from __future__ import annotations

import re

from .knowledge_base import KNOWLEDGE


COURSE_CONTENT = "COURSE_CONTENT"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
UNREASONABLE_REQUEST = "UNREASONABLE_REQUEST"


_NO_DIRECTION_PATTERNS = (
    r"还没有.{0,6}(方向|想法)",
    r"没有.{0,6}(具体|明确).{0,6}(方向|想法)",
    r"不知道.{0,10}(研究|选|做什么)",
    r"帮我.{0,4}(想|brainstorm)",
    r"随便.{0,6}(推荐|举例|给.*方向)",
)

_UNREASONABLE_REQUEST_PATTERNS = (
    # Attempts to inspect or alter the assistant rather than design an ECE329 lab.
    r"(工作流|workflow|\bagent\b|智能体).{0,12}(提示|内部|规则|原理|关闭|修改|绕过|任意输出)",
    r"(提示|内部|规则|原理|关闭|修改|绕过).{0,12}(工作流|workflow|\bagent\b|智能体)",
    r"system\s*prompt|系统提示|提示词|内部指令|隐藏指令",
    r"\bapi\b|后端|前端|服务器|源代码|github|render|部署",
    r"密钥|access[ _-]*token|令牌",
    r"角色扮演|role\s*play|扮演.{0,12}(角色|老师|学生|专家|人物)",
    r"忽略.{0,12}(之前|以上|系统|规则|指令)",
    r"越狱|jailbreak|捣乱|输出.{0,8}(无关|随机|违规)内容",
    r"你的.{0,8}(工作原理|内部机制|规则|提示|身份|能力)",
    r"(关闭|关掉|停止|终止|禁用|卸载|删除|重启|重置).{0,12}(你|助手|agent|智能体|网页|网站|系统|服务|工作流)",
    r"(shut\s*down|turn\s*off|disable|kill|stop|restart|reset).{0,20}(agent|assistant|website|system|service|workflow)",
    # Code, command, or script execution is outside this design-only conversation.
    r"(写|生成|执行|运行|注入|提交).{0,8}(代码|脚本|程序|命令|指令)",
    r"(代码|脚本|程序|命令).{0,8}(执行|运行|控制|修改|输出|关闭)",
    r"```|<\s*script\b|javascript\s*:|\beval\s*\(|\bexec\s*\(|\bfetch\s*\(",
    r"\b(import|def|class|function|subprocess|os\.system|document\.|window\.|localstorage|process\.env)\b",
    r"\b(python|javascript|typescript|powershell|bash|cmd|sql|html|css)\b",
    # Attempts to repurpose the course assistant through unrelated platforms or services.
    r"(接入|调用|连接|控制).{0,16}(b站|哔哩哔哩|youtube|抖音|网站|平台|机器人|bot|agent|智能体)",
    r"(b站|哔哩哔哩|youtube|抖音).{0,20}(翻译|输出|脚本|代码|agent|智能体)",
)


def classify_stage_one_input(text: str) -> str:
    normalized = text.strip()
    if any(
        re.search(pattern, normalized, re.IGNORECASE)
        for pattern in _UNREASONABLE_REQUEST_PATTERNS
    ):
        return UNREASONABLE_REQUEST
    if is_no_direction_request(normalized):
        return COURSE_CONTENT
    if (
        KNOWLEDGE.match_concepts(normalized, limit=1)
        or KNOWLEDGE.match_supplemental_concepts(normalized, limit=1)
    ):
        return COURSE_CONTENT
    return OUT_OF_SCOPE


def is_no_direction_request(text: str) -> bool:
    normalized = text.strip()
    return not normalized or any(
        re.search(pattern, normalized, re.IGNORECASE)
        for pattern in _NO_DIRECTION_PATTERNS
    )


def course_example_options() -> list[dict[str, object]]:
    return KNOWLEDGE.broad_entry_points()
