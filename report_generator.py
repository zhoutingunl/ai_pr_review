"""评审报告生成器。

输出 Markdown 报告，用于：
    * Web 页面展示
    * GitHub Review 正文回写
"""
from __future__ import annotations

_LEVEL_BADGE = {"P0": "🔴 P0 阻塞", "P1": "🟠 P1 高风险",
                "P2": "🟡 P2 中风险", "P3": "🔵 P3 建议优化"}

_CATEGORY_NAME = {"security": "安全", "performance": "性能",
                  "reliability": "稳定性", "maintainability": "可维护性",
                  "style": "风格"}


class ReportGenerator:

    def generate(self, ctx: dict, summary: dict, issues: list[dict],
                 fixes: list[dict], score: float, risk_level: str,
                 category_scores: dict,
                 omitted_context: list[str] | None = None) -> str:
        pr = ctx["pr"]
        lines: list[str] = []
        lines.append("# AI PR Review 评审报告")
        lines.append("")
        lines.append(f"**PR**: {pr['title']}")
        lines.append(f"**变更**: {pr['changed_files']} 个文件, "
                     f"+{pr['additions']} / -{pr['deletions']}")
        lines.append(f"**综合评分**: {score} / 100　**风险等级**: "
                     f"{_LEVEL_BADGE.get(risk_level, risk_level)}")
        if omitted_context:
            lines.append(f"> ⚠️ 因 PR 体量较大，本次评审已省略上下文："
                         f"{'、'.join(omitted_context)}（规则引擎仍全量扫描 Diff）")
        lines.append("")

        # 变更总结
        lines.append("## 变更总结")
        lines.append(summary.get("overview", "") or "（无）")
        modules = summary.get("modules") or []
        if modules:
            lines.append("")
            lines.append("**影响模块**: " + "、".join(str(m) for m in modules))
        lines.append("")

        # 维度得分
        lines.append("## 维度得分")
        lines.append("| 维度 | 得分 | 权重 |")
        lines.append("|---|---|---|")
        weights = {"security": 0.4, "reliability": 0.3,
                   "performance": 0.2, "style": 0.1}
        for cat, weight in weights.items():
            lines.append(f"| {_CATEGORY_NAME.get(cat, cat)} | "
                         f"{category_scores.get(cat, 100):.0f} | {weight} |")
        lines.append("")

        # 问题列表
        lines.append(f"## 发现问题（{len(issues)} 条）")
        if not issues:
            lines.append("未发现达到置信度阈值的问题。✅")
        else:
            lines.append("| 等级 | 维度 | 位置 | 问题 | 置信度 |")
            lines.append("|---|---|---|---|---|")
            order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
            for issue in sorted(issues,
                                key=lambda i: (order.get(i["level"], 9),
                                               -i["confidence"])):
                location = issue["file"]
                if issue.get("line"):
                    location += f":{issue['line']}"
                message = issue["message"].replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| {issue['level']} | "
                    f"{_CATEGORY_NAME.get(issue['category'], issue['category'])} | "
                    f"`{location}` | {message[:120]} | "
                    f"{issue['confidence']:.2f} |")
        lines.append("")

        # 修复建议
        if fixes:
            lines.append("## AI 修复建议")
            for fix in fixes:
                location = fix["file"]
                if fix.get("line"):
                    location += f":{fix['line']}"
                lines.append(f"### `{location}`")
                if fix.get("plan"):
                    lines.append(fix["plan"])
                if fix.get("patch"):
                    lines.append("```diff")
                    lines.append(fix["patch"])
                    lines.append("```")
                if fix.get("commit_message"):
                    lines.append(f"建议 commit: `{fix['commit_message']}`")
                lines.append("")

        lines.append("---")
        lines.append("*本报告由 AI PR Review 自动生成*")
        return "\n".join(lines)
