"""让大模型以游客视角点评测评结果里的行程，找出规则检查（eval/plan_checks.py）没覆盖到的问题。

用途是「发现新问题类型」：模型的意见不稳定、可能看错，每条都要回到数据核实；确认是真问题的，
再写成 plan_checks 里的确定性规则和单元测试，以后改引擎时自动检查。不要把点评结果当回归测试用。

会产生模型调用费用（每份行程一次调用，约 3k~6k 输入 token）。先用 --dry-run 看提示词。

模型配置（密钥只从环境变量读）：
  REVIEW_MODEL / REVIEW_BASE_URL / REVIEW_API_KEY   点评专用，可指向与主 Agent 不同的模型
  未设置时回退到 LLM_MODEL / LLM_BASE_URL / LLM_API_KEY（再回退 AI_GATEWAY_*）
  例：用 Gemini 时 REVIEW_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/

用法（仓库根目录）：
  ./.venv/Scripts/python.exe eval/review.py eval/results/round2 --dry-run              # 只打印提示词，不调用
  ./.venv/Scripts/python.exe eval/review.py eval/results/round2 11_nanjing_3days       # 只点评一个用例
  ./.venv/Scripts/python.exe eval/review.py eval/results/round2                        # 全部用例
结果写到 <结果目录>/review/<用例>.json 与 review/summary.md。
"""
import argparse
import glob
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402

from eval.plan_checks import check_plan, last_plans  # noqa: E402

SEVERITIES = ("high", "medium", "low")

SYSTEM_PROMPT = """你是熟悉中国各城市的资深旅行规划师，正在审核一份由程序自动生成的行程。
请站在真实游客的角度，找出「能走通但不合理」的地方，例如：
- 动线：同一片区分几次去、折返、离开景区又回来、明明顺路却绕远；
- 时间：在不合适的时段去（天黑进户外景区、饭点被拖得很晚、清晨/深夜不合常理）、时长明显不够或过长；
- 景点关系：两个地点其实属于同一景区或同一条街区却被分开安排、子景点被当成独立景点；
- 餐饮：同一天重复吃同类菜、为吃一顿专程跑远、用餐和下一站方向相反；
- 节奏：某天太空或太满、和用户的诉求（人群、预算、忌口、点名地点）不符。

要求：
1. 只评论行程里实际出现的项目，引用具体的天数、时间段和名称；不要编造行程里没有的店名或景点。
2. 「已由规则检查发现的问题」不必重复，把精力放在规则没覆盖的问题上。
3. 判断依赖你自己的常识（如某地日落时间、两个景点是否同属一个景区）时，在 evidence 里写明是常识判断、需核实。
4. 没有明显问题时返回空列表，不要为了凑数挑刺。
5. 只输出 JSON，不要输出其他文字，格式：
{"issues": [{"day": 1, "items": ["16:38 - 17:38 明孝陵景区"], "type": "动线|时间|景点关系|餐饮|节奏|其他",
  "severity": "high|medium|low", "problem": "一句话说明问题", "evidence": "依据（数据里的距离、时间，或注明常识判断）",
  "suggestion": "怎么排更合理"}]}"""


def review_env(name: str) -> str:
    return os.getenv(f"REVIEW_{name}") or os.getenv(f"LLM_{name}") or os.getenv(f"AI_GATEWAY_{name}") or ""


def timeline_text(plan: dict) -> str:
    """把行程压成逐行的时间轴文本：模型读 JSON 原文费 token，也容易漏看路段信息。"""
    lines = [f"城市：{plan.get('city', '')}，共 {plan.get('total_days', '')} 天"]
    hotel = plan.get("hotel")
    if hotel:
        lines.append(f"住宿：{hotel.get('name', '')}")
    for note in plan.get("notes", []):
        lines.append(f"备注：{note}")
    if plan.get("unassigned_spots"):
        lines.append("未安排的地点：" + "；".join(plan["unassigned_spots"]))
    if plan.get("unmet_must_eat"):
        lines.append("没吃到的必吃：" + "；".join(plan["unmet_must_eat"]))
    for d in plan.get("days", []):
        lines.append(f"\n第 {d['day_number']} 天 {d.get('date', '')}（市内移动约 {d.get('estimated_distance_km', '')} km）")
        for it in d["items"]:
            parts = [it["time_slot"], it["title"]]
            if it.get("transit_info"):
                parts.append(f"交通：{it['transit_info']}")
            if it.get("open_time") and it["category"] in ("spot", "meal", "snack"):
                parts.append(f"营业：{it['open_time'][:60]}")
            if it.get("warnings"):
                parts.append("提示：" + "；".join(it["warnings"]))
            lines.append("  " + " ｜ ".join(parts))
    return "\n".join(lines)


def build_prompt(messages: list, plan: dict, rule_issues: list) -> str:
    known = "\n".join(f"- 第{i['day']}天 [{i['type']}] {i['detail']}" for i in rule_issues) or "（无）"
    asks = "\n".join(f"第{n}轮：{m}" for n, m in enumerate(messages, 1))
    return f"用户的需求：\n{asks}\n\n已由规则检查发现的问题：\n{known}\n\n行程：\n{timeline_text(plan)}"


def parse_issues(text: str) -> list:
    """从模型输出里取出 issues 列表；容忍 ```json 代码块和前后多余文字，字段不全的条目丢弃。"""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("模型输出里没有 JSON")
    data = json.loads(m.group(0))
    out = []
    for it in data.get("issues", []) if isinstance(data, dict) else []:
        if not isinstance(it, dict) or not it.get("problem"):
            continue
        it["severity"] = it.get("severity") if it.get("severity") in SEVERITIES else "low"
        out.append(it)
    return out


def call_model(client, model: str, prompt: str) -> tuple:
    t0 = time.time()
    res = client.chat.completions.create(
        model=model, temperature=0.2,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    usage = getattr(res, "usage", None)
    tokens = (getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))
    return res.choices[0].message.content or "", tokens, round(time.time() - t0, 1)


def review_record(record: dict, client, model: str, dry_run: bool) -> list:
    """逐轮点评一个测评用例；返回 [{turn, rule_issues, issues | error, ...}]。"""
    messages = [t.get("message", "") for t in record.get("turns", [])]
    results = []
    for turn_no, plan, types in last_plans(record):
        rule_issues = check_plan(plan, types)
        prompt = build_prompt(messages[:turn_no], plan, rule_issues)
        entry = {"turn": turn_no, "rule_issues": rule_issues}
        if dry_run:
            print(f"===== {record.get('id')} 第{turn_no}轮（约 {len(prompt)} 字）=====\n{SYSTEM_PROMPT}\n\n{prompt}\n")
            continue
        try:
            text, tokens, seconds = call_model(client, model, prompt)
            entry.update(issues=parse_issues(text), raw=text, tokens=tokens, seconds=seconds)
        except Exception as e:  # 单个用例失败不影响其他用例；错误信息去掉密钥再记录
            from app._data.amap import redact_secrets
            entry["error"] = redact_secrets(f"{type(e).__name__}: {e}")[:500]
        results.append(entry)
    return results


def write_summary(out_dir: str, model: str, reviewed: dict) -> str:
    lines = [f"# 行程点评（模型：{model}）", "",
             "模型意见未经核实：逐条回到数据确认，是真问题的写进 eval/plan_checks.py 并加单元测试。", ""]
    counts = {s: 0 for s in SEVERITIES}
    for cid, entries in reviewed.items():
        for e in entries:
            lines.append(f"## {cid} 第{e['turn']}轮")
            for r in e["rule_issues"]:
                lines.append(f"- 规则：第{r['day']}天 [{r['type']}] {r['detail']}")
            if e.get("error"):
                lines.append(f"- 调用失败：{e['error']}")
            for it in sorted(e.get("issues", []), key=lambda x: SEVERITIES.index(x["severity"])):
                counts[it["severity"]] += 1
                items = "、".join(it.get("items") or [])
                lines.append(f"- 模型 [{it['severity']}/{it.get('type', '')}] 第{it.get('day', '?')}天 {items}：{it['problem']}")
                if it.get("evidence"):
                    lines.append(f"  - 依据：{it['evidence']}")
                if it.get("suggestion"):
                    lines.append(f"  - 建议：{it['suggestion']}")
            lines.append("")
    lines.insert(3, f"模型意见：high {counts['high']} / medium {counts['medium']} / low {counts['low']}")
    path = os.path.join(out_dir, "summary.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results_dir")
    ap.add_argument("cases", nargs="*", help="只点评这些用例 id；不给则全部")
    ap.add_argument("--model", help="覆盖 REVIEW_MODEL / LLM_MODEL")
    ap.add_argument("--dry-run", action="store_true", help="只打印提示词，不调用模型")
    args = ap.parse_args(argv)
    load_dotenv(os.path.join(ROOT, ".env"))

    model = args.model or review_env("MODEL")
    client = None
    if not args.dry_run:
        if not (model and review_env("API_KEY")):
            sys.exit("缺少模型配置：设置 REVIEW_MODEL / REVIEW_API_KEY（或 LLM_*），见脚本开头说明")
        from openai import OpenAI
        client = OpenAI(api_key=review_env("API_KEY"), base_url=review_env("BASE_URL") or None)

    out_dir = os.path.join(args.results_dir, "review")
    reviewed = {}
    for path in sorted(glob.glob(os.path.join(args.results_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
        cid = record.get("id") or os.path.splitext(os.path.basename(path))[0]
        if args.cases and cid not in args.cases:
            continue
        entries = review_record(record, client, model, args.dry_run)
        if args.dry_run:
            continue
        reviewed[cid] = entries
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"{cid}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": cid, "model": model, "turns": entries}, f, ensure_ascii=False, indent=1)
        n = sum(len(e.get("issues", [])) for e in entries)
        errs = [e["error"] for e in entries if e.get("error")]
        print(f"{cid}：模型意见 {n} 条" + (f"，失败 {len(errs)} 轮：{errs[0][:120]}" if errs else ""))
    if reviewed:
        # 汇总目录里全部已点评的用例：分几次跑（先跑一条看质量、再跑其余）时不覆盖前面的
        everything = {}
        for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            everything[saved["id"]] = saved["turns"]
        print("汇总：", write_summary(out_dir, model, everything))


if __name__ == "__main__":
    main()
