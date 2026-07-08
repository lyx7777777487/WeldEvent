"""Seed knowledge — realistic welding domain data across all 6 knowledge ports.

从 app.py 拆出的种子数据。所有种子数据收进 build_seed_knowledge() 函数内部，
作为局部变量返回，避免模块加载时执行大量 dataclass 实例化。
"""

from __future__ import annotations

from cognitiveplane.shared.dto.knowledge import (
    CaseLibraryResult,
    EquipmentKnowledgeResult,
    KnowledgeResult,
    ProcessKnowledgeResult,
    RuleResult,
    StandardsResult,
)
from cognitiveplane.shared.dto_decision.outputs import Constraint, ParameterSet
from cognitiveplane.shared.enums import KnowledgeType
from cognitiveplane.shared.types import KnowledgeId


def build_seed_knowledge() -> dict[str, list]:
    """构造 6 类种子数据，供 StubKnowledgeAdapter 使用。

    返回 dict 键名与 StubKnowledgeAdapter._INPUT_DISPATCH 一致：
      rag_query / standards_query / case_library_query /
      process_query / rule_query / equipment_query
    """

    _uid_counter = 0

    def _uid() -> str:
        """Generate a deterministic UUID for seed data."""
        from uuid import uuid5, NAMESPACE_DNS
        nonlocal _uid_counter
        _uid_counter += 1
        return str(uuid5(NAMESPACE_DNS, f"weldevent-seed-{_uid_counter:03d}"))

    # ── RAG 查询（通用知识检索） ──

    seed_rag: list[KnowledgeResult] = [
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.STANDARD,
            content=(
                "NB/T47014-2011 第 6.3 条：\n"
                "对于厚度 > 20mm 的压力容器焊缝（Q345R 材料）：\n"
                "- 预热温度: ≥100°C（t>30mm 时 ≥120°C）\n"
                "- 层间温度: ≤200°C\n"
                "- 焊后热处理: 580±20°C"
            ),
            relevance_score=0.95,
            source_reference="NB/T47014-2011 第6.3条",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.STANDARD,
            content=(
                "夹渣验收标准（NB/T47014）：\n"
                "- 单个夹渣长度: ≤ t/3（t 为板厚）\n"
                "- 夹渣间距: ≥ 6L\n"
                "- t=22mm → 夹渣单长限值 ≤ 7.3mm"
            ),
            relevance_score=0.92,
            source_reference="NB/T47014-2011 第6.3.2条",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.PROCESS_SPEC,
            content=(
                "GMAW T型角焊缝检测工艺参数参考：\n"
                "- 焊接电流: 200~280A\n"
                "- 电弧电压: 24~32V\n"
                "- 焊接速度: 300~500 mm/min\n"
                "- 保护气体: Ar+CO2 混合气"
            ),
            relevance_score=0.88,
            source_reference="焊接工艺规程 WPS-GMAW-TJ-001",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.STANDARD,
            content=(
                "GB/T3323-2005 射线检测质量分级：\n"
                "- I级：无裂纹、未熔合、未焊透；圆形缺陷不超过规定数\n"
                "- II级：不允许裂纹和未熔合；圆形缺陷限值为I级的1.5倍\n"
                "- III级：不允许裂纹和未熔合；圆形缺陷限值为I级的2倍\n"
                "- IV级：超过III级标准"
            ),
            relevance_score=0.94,
            source_reference="GB/T3323-2005 第5条",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.STANDARD,
            content=(
                "NB/T47014-2011 气孔验收标准：\n"
                "- 单个气孔直径: ≤ t/4（t 为板厚），最大 6mm\n"
                "- 气孔率: ≤ 3%（射线检测投影面积比）\n"
                "- 密集气孔: 任意 100mm×100mm 区域内不超过 5 个\n"
                "- t=22mm → 单个气孔限值 ≤ 5.5mm"
            ),
            relevance_score=0.93,
            source_reference="NB/T47014-2011 第6.3.1条",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.STANDARD,
            content=(
                "NB/T47014-2011 咬边验收标准：\n"
                "- 连续咬边长度: ≤ 100mm\n"
                "- 咬边深度: ≤ 0.5mm（板厚 t≤20mm）或 ≤ t/40（t>20mm）\n"
                "- 两侧咬边总长: 不超过焊缝总长的 20%"
            ),
            relevance_score=0.90,
            source_reference="NB/T47014-2011 第6.3.3条",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.PROCESS_SPEC,
            content=(
                "GTAW 对接焊缝工艺参数参考（不锈钢 304）：\n"
                "- 焊接电流: 80~150A（直流正接）\n"
                "- 电弧电压: 10~16V\n"
                "- 焊接速度: 50~150 mm/min\n"
                "- 钨极直径: 2.0~3.2mm\n"
                "- 保护气体: 纯 Ar，流量 8~15 L/min"
            ),
            relevance_score=0.87,
            source_reference="焊接工艺规程 WPS-GTAW-SS-001",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.PROCESS_SPEC,
            content=(
                "SMAW 对接焊缝工艺参数参考（低合金钢 Q345R）：\n"
                "- 焊条直径: 3.2mm / 4.0mm\n"
                "- 焊接电流: 100~140A（φ3.2）/ 150~200A（φ4.0）\n"
                "- 电弧电压: 22~28V\n"
                "- 层间温度: 100~200°C\n"
                "- 预热要求: t>25mm 时 ≥80°C"
            ),
            relevance_score=0.86,
            source_reference="焊接工艺规程 WPS-SMAW-LA-001",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.CASE_LIBRARY,
            content=(
                "案例：Q345R 压力容器纵缝气孔缺陷\n"
                "工况：GMAW，板厚 22mm，焊接电流 260A，电压 28V\n"
                "缺陷：密集气孔，最大直径 3.2mm，气孔率 4.2%\n"
                "原因分析：保护气流量不足（12L/min→应≥15L/min），现场有穿堂风\n"
                "处置：碳弧气刨清除后补焊，增加挡风措施，提高气流量至 18L/min\n"
                "经验：室外作业必须设置防风棚，气流量不低于 15L/min"
            ),
            relevance_score=0.91,
            source_reference="案例库 CASE-2024-0042",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.CASE_LIBRARY,
            content=(
                "案例：304不锈钢管对接焊缝裂纹\n"
                "工况：GTAW，管径 φ89×5mm，焊接电流 120A\n"
                "缺陷：焊缝中心纵向裂纹，长度 15mm\n"
                "原因分析：焊接热输入过大导致晶间腐蚀敏感化，未做固溶处理\n"
                "处置：打磨消除裂纹，控制热输入≤15kJ/cm，焊后做固溶处理\n"
                "经验：奥氏体不锈钢焊接热输入应严格控制，多层焊层温≤150°C"
            ),
            relevance_score=0.89,
            source_reference="案例库 CASE-2024-0067",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.CASE_LIBRARY,
            content=(
                "案例：Q345R 容器环缝夹渣缺陷\n"
                "工况：SMAW，板厚 30mm，多层多道焊\n"
                "缺陷：层间夹渣，最大长度 8mm，深度 2mm\n"
                "原因分析：层间清理不彻底，焊道排列不合理导致死角\n"
                "处置：碳弧气刨清除夹渣区域，重新补焊，调整焊道排列\n"
                "经验：厚板多层焊每层必须彻底清渣，焊道排列避免死角"
            ),
            relevance_score=0.88,
            source_reference="案例库 CASE-2024-0089",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.STANDARD,
            content=(
                "GB/T3323-2005 焊缝射线检测技术要求：\n"
                "- 透照方式: 单壁透照（外径>100mm）或双壁透照\n"
                "- 像质计: 应达到的像质指数按标准表2选取\n"
                "- 黑度范围: 2.0~4.0（D型胶片）\n"
                "- 标记: 每张底片应有工件编号、焊缝编号、部位标记"
            ),
            relevance_score=0.85,
            source_reference="GB/T3323-2005 第4条",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.STANDARD,
            content=(
                "NB/T47014-2011 未焊透验收标准：\n"
                "- 单面焊未焊透深度: ≤ t×15%，最大 2mm\n"
                "- 未焊透长度: 不超过该级夹渣限值\n"
                "- 根部未焊透: 按圆形缺陷评级\n"
                "- t=22mm → 未焊透深度限值 ≤ 3.3mm"
            ),
            relevance_score=0.87,
            source_reference="NB/T47014-2011 第6.3.4条",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.PROCESS_SPEC,
            content=(
                "GMAW 对接焊缝工艺参数参考（碳钢 Q235B）：\n"
                "- 焊接电流: 180~260A\n"
                "- 电弧电压: 22~30V\n"
                "- 焊接速度: 250~450 mm/min\n"
                "- 焊丝直径: 1.0~1.2mm\n"
                "- 保护气体: 80%Ar+20%CO2，流量 15~20 L/min\n"
                "- 干伸长: 10~15mm"
            ),
            relevance_score=0.84,
            source_reference="焊接工艺规程 WPS-GMAW-CS-001",
        ),
        KnowledgeResult(
            knowledge_id=KnowledgeId(value=_uid()),
            knowledge_type=KnowledgeType.CASE_LIBRARY,
            content=(
                "案例：Q345R 容器焊缝咬边缺陷\n"
                "工况：GMAW 角焊缝，板厚 16mm，电流 280A\n"
                "缺陷：连续咬边，最大深度 1.2mm，长度 120mm\n"
                "原因分析：焊接电流偏大，焊速偏慢，焊枪角度不当\n"
                "处置：打磨咬边区域，降低电流至 240A，调整焊枪角度15°\n"
                "经验：角焊缝电流不宜超过 260A，焊枪与工件夹角保持 70-80°"
            ),
            relevance_score=0.86,
            source_reference="案例库 CASE-2024-0103",
        ),
    ]

    # ── 标准查询 ──

    seed_standards: list[StandardsResult] = [
        StandardsResult(
            standard_id="NB/T47014-2011",
            section="6.3",
            clause="6.3.1",
            text="圆形缺陷（气孔）验收：单个气孔直径≤t/4，最大6mm；气孔率≤3%；密集气孔在100mm×100mm区域内不超过5个",
            relevance=0.95,
        ),
        StandardsResult(
            standard_id="NB/T47014-2011",
            section="6.3",
            clause="6.3.2",
            text="条形缺陷（夹渣）验收：单个夹渣长度≤t/3；夹渣间距≥6L；t=22mm时限值≤7.3mm",
            relevance=0.94,
        ),
        StandardsResult(
            standard_id="NB/T47014-2011",
            section="6.3",
            clause="6.3.3",
            text="咬边验收：连续咬边长度≤100mm；深度≤0.5mm（t≤20mm）或≤t/40（t>20mm）；两侧咬边总长不超过焊缝总长20%",
            relevance=0.92,
        ),
        StandardsResult(
            standard_id="NB/T47014-2011",
            section="6.3",
            clause="6.3.4",
            text="未焊透验收：单面焊未焊透深度≤t×15%，最大2mm；未焊透长度不超过该级夹渣限值",
            relevance=0.91,
        ),
        StandardsResult(
            standard_id="NB/T47014-2011",
            section="6.3",
            clause="6.3.5",
            text="预热温度要求：Q345R材料厚度>20mm时预热≥100°C；厚度>30mm时预热≥120°C；层间温度≤200°C",
            relevance=0.93,
        ),
        StandardsResult(
            standard_id="GB/T3323-2005",
            section="5",
            clause="5.1",
            text="射线检测质量分级：I级无裂纹未熔合未焊透；II级不允许裂纹未熔合，圆形缺陷限值为I级1.5倍；III级限值为I级2倍；IV级超过III级",
            relevance=0.94,
        ),
        StandardsResult(
            standard_id="GB/T3323-2005",
            section="4",
            clause="4.1",
            text="射线检测技术要求：透照方式按工件条件选取；像质指数按标准表2；底片黑度2.0~4.0；每张底片应有工件编号、焊缝编号、部位标记",
            relevance=0.88,
        ),
        StandardsResult(
            standard_id="ISO 3834-2",
            section="4",
            clause="4.2",
            text="焊接质量要求：制造商应建立焊接工艺评定体系；焊工应持有有效资格证书；应进行焊接工艺试验并记录",
            relevance=0.82,
        ),
    ]

    # ── 案例库查询 ──

    seed_cases: list[CaseLibraryResult] = [
        CaseLibraryResult(
            case_id="CASE-2024-0042",
            defect_description="Q345R容器纵缝密集气孔，最大直径3.2mm，气孔率4.2%，GMAW工艺板厚22mm",
            resolution="碳弧气刨清除后补焊，增加挡风措施，保护气流量从12L/min提高至18L/min",
            outcome="补焊后射线检测II级合格，后续批次无气孔缺陷",
            similarity_score=0.95,
        ),
        CaseLibraryResult(
            case_id="CASE-2024-0067",
            defect_description="304不锈钢管对接焊缝中心纵向裂纹15mm，GTAW工艺管径φ89×5mm",
            resolution="打磨消除裂纹，控制热输入≤15kJ/cm，焊后做固溶处理",
            outcome="修复后渗透检测合格，晶间腐蚀试验通过",
            similarity_score=0.90,
        ),
        CaseLibraryResult(
            case_id="CASE-2024-0089",
            defect_description="Q345R容器环缝层间夹渣，最大长度8mm深度2mm，SMAW工艺板厚30mm",
            resolution="碳弧气刨清除夹渣区域重新补焊，调整焊道排列避免死角",
            outcome="补焊后射线检测II级合格",
            similarity_score=0.92,
        ),
        CaseLibraryResult(
            case_id="CASE-2024-0103",
            defect_description="Q345R角焊缝连续咬边，最大深度1.2mm长度120mm，GMAW工艺板厚16mm",
            resolution="打磨咬边区域，降低电流至240A，调整焊枪角度15°",
            outcome="修复后外观检测合格，后续焊接参数调整后无咬边",
            similarity_score=0.88,
        ),
        CaseLibraryResult(
            case_id="CASE-2024-0128",
            defect_description="16MnR容器焊缝未熔合，长度25mm深度3mm，SMAW工艺板厚35mm",
            resolution="碳弧气刨清除未熔合区域，调整焊条角度和运条方式，补焊",
            outcome="补焊后射线检测I级合格",
            similarity_score=0.91,
        ),
        CaseLibraryResult(
            case_id="CASE-2024-0156",
            defect_description="Q235B钢结构焊缝焊瘤，高度5mm宽度8mm，GMAW工艺板厚12mm",
            resolution="打磨去除焊瘤，调整焊接速度和电压匹配，降低干伸长",
            outcome="修复后外观检测合格，焊缝成形良好",
            similarity_score=0.85,
        ),
    ]

    # ── 工艺知识查询 ──

    seed_process: list[ProcessKnowledgeResult] = [
        ProcessKnowledgeResult(
            process_id="PROC-GMAW-CS-001",
            recommended_parameters=ParameterSet(parameters={
                "process_type": "GMAW",
                "material": "Q235B",
                "joint_type": "butt",
                "welding_current_A": "180~260",
                "arc_voltage_V": "22~30",
                "travel_speed_mm_min": "250~450",
                "wire_diameter_mm": "1.0~1.2",
                "shielding_gas": "80%Ar+20%CO2",
                "gas_flow_L_min": "15~20",
            }),
            quality_criteria={
                "外观": "焊缝成形均匀，无咬边、焊瘤",
                "尺寸": "余高0~3mm，宽度差≤3mm",
                "内部": "射线检测不低于II级",
            },
            common_defects=["气孔", "咬边", "焊瘤", "未熔合"],
        ),
        ProcessKnowledgeResult(
            process_id="PROC-GMAW-LA-001",
            recommended_parameters=ParameterSet(parameters={
                "process_type": "GMAW",
                "material": "Q345R",
                "joint_type": "fillet",
                "welding_current_A": "200~280",
                "arc_voltage_V": "24~32",
                "travel_speed_mm_min": "300~500",
                "wire_diameter_mm": "1.2",
                "shielding_gas": "Ar+CO2混合气",
                "gas_flow_L_min": "15~20",
                "preheat_temp_C": "≥100(t>20mm)",
            }),
            quality_criteria={
                "外观": "焊脚尺寸符合设计要求，无咬边",
                "内部": "射线或超声波检测不低于II级",
                "预热": "板厚>20mm时预热≥100°C",
            },
            common_defects=["气孔", "夹渣", "咬边", "未焊透"],
        ),
        ProcessKnowledgeResult(
            process_id="PROC-GTAW-SS-001",
            recommended_parameters=ParameterSet(parameters={
                "process_type": "GTAW",
                "material": "304SS",
                "joint_type": "butt",
                "welding_current_A": "80~150",
                "arc_voltage_V": "10~16",
                "travel_speed_mm_min": "50~150",
                "tungsten_diameter_mm": "2.0~3.2",
                "shielding_gas": "纯Ar",
                "gas_flow_L_min": "8~15",
                "heat_input_kJ_cm": "≤15",
            }),
            quality_criteria={
                "外观": "焊缝呈银白色或淡黄色，无氧化",
                "内部": "射线检测不低于II级",
                "晶间腐蚀": "按GB/T4334试验合格",
            },
            common_defects=["裂纹", "气孔", "未熔合", "氧化"],
        ),
        ProcessKnowledgeResult(
            process_id="PROC-SMAW-LA-001",
            recommended_parameters=ParameterSet(parameters={
                "process_type": "SMAW",
                "material": "Q345R",
                "joint_type": "butt",
                "electrode_diameter_mm": "3.2/4.0",
                "welding_current_A": "100~140(φ3.2) / 150~200(φ4.0)",
                "arc_voltage_V": "22~28",
                "interpass_temp_C": "100~200",
                "preheat_temp_C": "≥80(t>25mm)",
            }),
            quality_criteria={
                "外观": "焊缝成形均匀，余高0~3mm",
                "内部": "射线检测不低于II级",
                "层间": "层间温度100~200°C，每层清渣彻底",
            },
            common_defects=["夹渣", "气孔", "未焊透", "咬边"],
        ),
    ]

    # ── 规则查询 ──

    seed_rules: list[RuleResult] = [
        RuleResult(
            rule_id="RULE-PREHEAT-001",
            rule_text="Q345R材料板厚>20mm时，焊前预热温度不低于100°C；板厚>30mm时不低于120°C",
            applicability=0.95,
            constraints=[Constraint(name="preheat_temp", value="≥100°C", source="NB/T47014-2011")],
        ),
        RuleResult(
            rule_id="RULE-INTERPASS-001",
            rule_text="Q345R材料多层焊层间温度应控制在100~200°C之间，不得超过200°C",
            applicability=0.93,
            constraints=[Constraint(name="interpass_temp_max", value="200°C", source="NB/T47014-2011")],
        ),
        RuleResult(
            rule_id="RULE-PWHT-001",
            rule_text="Q345R材料焊后热处理温度580±20°C，保温时间按2min/mm计算，最短不少于1小时",
            applicability=0.90,
            constraints=[Constraint(name="pwht_temp", value="580±20°C", source="NB/T47014-2011")],
        ),
        RuleResult(
            rule_id="RULE-POROSITY-001",
            rule_text="气孔验收：单个气孔直径≤t/4且最大6mm；气孔率≤3%；密集气孔100mm×100mm区域内≤5个",
            applicability=0.94,
            constraints=[Constraint(name="max_porosity_diameter", value="min(t/4,6)mm", source="NB/T47014-2011")],
        ),
        RuleResult(
            rule_id="RULE-SLAG-001",
            rule_text="夹渣验收：单个夹渣长度≤t/3；夹渣间距≥6L；t=22mm时限值≤7.3mm",
            applicability=0.92,
            constraints=[Constraint(name="max_slag_length", value="t/3 mm", source="NB/T47014-2011")],
        ),
    ]

    # ── 设备知识查询 ──

    seed_equipment: list[EquipmentKnowledgeResult] = [
        EquipmentKnowledgeResult(
            equipment_id="EQUIP-XRAY-001",
            specifications={"type": "X射线探伤机", "model": "XXG-3005", "voltage_range": "100~300kV", "current_range": "1~5mA"},
            operational_limits={"最大穿透厚度": "45mm(钢)", "连续工作时间": "≤30min", "辐射安全距离": "≥3m"},
            maintenance_requirements=["每日检查电缆绝缘", "每月校准曝光参数", "每年辐射安全检测"],
        ),
        EquipmentKnowledgeResult(
            equipment_id="EQUIP-UT-001",
            specifications={"type": "数字超声波探伤仪", "model": "CTS-1002", "频率范围": "0.5~15MHz", "通道数": "4"},
            operational_limits={"工作温度": "-10~50°C", "电池续航": "≥8h", "探头温度": "≤60°C"},
            maintenance_requirements=["每次使用后清洁探头", "每月校准仪器线性", "每季度更换耦合剂"],
        ),
        EquipmentKnowledgeResult(
            equipment_id="EQUIP-GMAW-001",
            specifications={"type": "GMAW焊接电源", "model": "NB-500", "电流范围": "50~500A", "负载持续率": "60%"},
            operational_limits={"最大焊接电流": "500A", "输入电压": "380V±10%", "环境温度": "-10~40°C"},
            maintenance_requirements=["每日清洁送丝轮", "每周检查导电嘴磨损", "每月校准电流表"],
        ),
    ]

    return {
        "rag_query": seed_rag,
        "standards_query": seed_standards,
        "case_library_query": seed_cases,
        "process_query": seed_process,
        "rule_query": seed_rules,
        "equipment_query": seed_equipment,
    }
