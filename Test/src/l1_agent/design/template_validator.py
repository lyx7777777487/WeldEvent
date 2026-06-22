"""template_validator — 三重验证 LLM 产出的 WorkflowTemplate dict。

第 1 重: 结构 — 必填字段齐全、类型对
第 2 重: 语义 — activity_name 白名单、entry_point 存在、transition 引用合法
第 3 重: 人审 — 由 human_reviewer 处理，不在本模块

返回 ValidationResult(ok, error)；ok=False 时 error 是给 LLM 看的修正提示。
"""

from dataclasses import dataclass

# 第一版白名单 — 见 Test/docs/05-open-questions.md Q7
ALLOWED_ACTIVITIES = {"annotation"}


@dataclass
class ValidationResult:
    ok: bool
    error: str = ""
    template: dict | None = None


def validate(template: dict) -> ValidationResult:
    if not isinstance(template, dict):
        return ValidationResult(False, "template 必须是 JSON 对象")

    for key in ("id", "version", "entry_point", "control_points"):
        if key not in template:
            return ValidationResult(False, f"缺少必填字段: {key}")

    if not isinstance(template["control_points"], list) or not template["control_points"]:
        return ValidationResult(False, "control_points 必须是非空数组")

    cp_ids = set()
    for i, cp in enumerate(template["control_points"]):
        if not isinstance(cp, dict):
            return ValidationResult(False, f"control_points[{i}] 必须是对象")
        cp_id = cp.get("id")
        if not cp_id:
            return ValidationResult(False, f"control_points[{i}] 缺少 id")
        if cp_id in cp_ids:
            return ValidationResult(False, f"control_points[{i}] id 重复: {cp_id}")
        cp_ids.add(cp_id)

        binding = cp.get("activity_binding")
        if not isinstance(binding, dict):
            return ValidationResult(False, f"control_point '{cp_id}' 缺少 activity_binding")
        activity_name = binding.get("activity_name")
        if activity_name not in ALLOWED_ACTIVITIES:
            return ValidationResult(
                False,
                f"control_point '{cp_id}' 的 activity_name='{activity_name}' 不在白名单 {sorted(ALLOWED_ACTIVITIES)}"
            )

    if template["entry_point"] not in cp_ids:
        return ValidationResult(
            False,
            f"entry_point='{template['entry_point']}' 不在 control_points 的 id 列表里"
        )

    transitions = template.get("transitions", [])
    if not isinstance(transitions, list):
        return ValidationResult(False, "transitions 必须是数组")

    reachable = {template["entry_point"]}
    for t in transitions:
        if not isinstance(t, dict):
            return ValidationResult(False, "transition 必须是对象")
        from_cp = t.get("from_cp")
        if from_cp not in cp_ids:
            return ValidationResult(False, f"transition.from_cp='{from_cp}' 不存在")
        for branch in t.get("branches", []):
            to_cp = branch.get("to_cp")
            if to_cp is not None and to_cp not in cp_ids:
                return ValidationResult(False, f"transition branch to_cp='{to_cp}' 不存在")
            if to_cp is not None:
                reachable.add(to_cp)

    unreachable = cp_ids - reachable
    if unreachable:
        return ValidationResult(
            False,
            f"control_points 不可达（从 entry_point 走不到）: {sorted(unreachable)}"
        )

    return ValidationResult(True, template=template)