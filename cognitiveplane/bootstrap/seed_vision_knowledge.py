"""视觉理解 RAG collection 的种子数据 — 工业图像数据集元信息。

本模块为视觉理解 RAG（Retrieval-Augmented Generation）collection 提供工业图像
数据集的元信息种子。当 agent 需要为"视觉理解/IQA/缺陷识别"任务选择候选训练集或
参考数据集时，可对这些条目做语义检索，快速定位与当前焊接场景相匹配的公开数据集。

注意事项：
  - 焊接数据集几乎都是 X 光 / 超声（NDT）视角，与 RGB 截面测量流程视角不完全
    对齐，更适合当作"缺陷类型语义参考"而非直接训练数据；
  - RGB 工业品 / 钢材表面数据集可用于异常检测、缺陷检测模型的迁移学习参考。
"""

from __future__ import annotations


def build_vision_knowledge_seed() -> list[dict]:
    """构造工业图像数据集元信息列表，供视觉理解 RAG collection 灌库使用。

    返回的每个 dict 字段说明：
      - dataset_name:          数据集名称
      - description:           详细描述（含适用场景与视角对齐说明）
      - source_url:            数据集来源地址
      - image_count:           图像数量（带单位，如 "5354张"）
      - defect_types:          缺陷类型列表
      - modality:              成像方式（RGB / X-ray / 超声 / 红外等）
      - industry:              所属行业（焊接 / 钢材 / 电子 / 通用等）
      - applicable_scenarios:  适用场景列表
      - license:               许可协议
      - search_text:           用于 embedding 检索的自然语言文本
    """

    datasets: list[dict] = [
        # ── 1. MVTec AD ── 通用工业异常检测基准 ──
        {
            "dataset_name": "MVTec AD",
            "description": (
                "MVTec AD 是工业异常检测领域最经典的 benchmark，覆盖瓶、电缆、胶囊、"
                "榛子、螺母、金属螺柱、药丸、螺丝、牙刷、瓦片、拉链、晶体管、网格、"
                "皮革、木块共 15 类工业品。提供像素级缺陷分割标注与无缺陷训练子集，"
                "RGB 视角拍摄，适合异常检测模型的迁移学习与基线对比，可作为焊接外观"
                "缺陷（如咬边、焊瘤、气孔外露）RGB 检测的通用参考。"
            ),
            "source_url": "https://www.mvtec.com/company/research/datasets/mvtec-ad",
            "image_count": "5354张",
            "defect_types": ["划痕", "凹痕", "污渍", "裂纹", "变色", "弯曲", "破损", "凸起", "孔洞", "杂质"],
            "modality": "RGB",
            "industry": "通用工业",
            "applicable_scenarios": ["异常检测基线对比", "无监督缺陷检测", "像素级分割迁移学习", "外观缺陷参考"],
            "license": "CC BY-NC-SA 4.0",
        },
        # ── 2. VisA ── 含PCB等复杂结构件 ──
        {
            "dataset_name": "VisA",
            "description": (
                "VisA（Visual Anomaly）由 Amazon Science 发布，规模大于 MVTec AD，"
                "包含 PCB 板、胶囊、胶囊管、坚果等复杂结构件，部分对象采用多实例拍摄。"
                "提供像素级异常标注，RGB 视角，适合复杂结构工业品的异常检测研究，"
                "可作为焊接结构件（如管板焊缝、多零件组件）外观检测的参考数据集。"
            ),
            "source_url": "https://amazon-science.github.io/visa/",
            "image_count": "10821张",
            "defect_types": ["划痕", "污渍", "变色", "缺失", "变形", "裂纹", "杂质", "错位"],
            "modality": "RGB",
            "industry": "通用工业/电子",
            "applicable_scenarios": ["复杂结构异常检测", "多实例缺陷识别", "迁移学习预训练", "外观缺陷参考"],
            "license": "CC BY 4.0",
        },
        # ── 3. Real-IAD ── 多视角、材质丰富 ──
        {
            "dataset_name": "Real-IAD",
            "description": (
                "Real-IAD 提供 30 类工业品的多视角图像，材质覆盖塑料、木材、陶瓷、"
                "金属等，每个样本采集多个角度，更贴近真实产线质检场景。RGB 视角，"
                "适合研究多视角缺陷融合与真实工业环境下的异常检测，可作为焊接工件"
                "多角度外观检测的参考。"
            ),
            "source_url": "https://real-iad.adlab.org.cn/",
            "image_count": "30类多视角",
            "defect_types": ["划痕", "污渍", "变形", "破损", "变色", "凹痕", "缺失"],
            "modality": "RGB",
            "industry": "通用工业",
            "applicable_scenarios": ["多视角异常检测", "真实产线质检参考", "缺陷融合识别", "外观缺陷参考"],
            "license": "研究用途（详见项目声明）",
        },
        # ── 4. GC10-DET ── 钢材表面缺陷（含焊缝线）──
        {
            "dataset_name": "GC10-DET",
            "description": (
                "GC10-DET 收集 3570 张高分辨率热轧钢材表面缺陷图像，标注 10 类缺陷，"
                "其中包含 Welding line（焊缝线）类别，与钢材轧制/焊接生产场景直接相关。"
                "RGB 视角拍摄，适合钢材表面缺陷检测模型训练，可作为焊接结构件表面"
                "质量评估的参考数据集。"
            ),
            "source_url": "https://github.com/dtsgao/GC10-Dataset",
            "image_count": "3570张",
            "defect_types": ["冲压孔", "焊缝线", "新月形凹痕", "水斑", "油斑", "丝状斑痕", "轧入杂质", "折痕", "腰部折痕", "锻造凹痕"],
            "modality": "RGB",
            "industry": "钢材",
            "applicable_scenarios": ["钢材表面缺陷检测", "焊缝线识别", "缺陷分类训练", "外观缺陷参考"],
            "license": "MIT",
        },
        # ── 5. NEU-CLS / NEU-DET ── 经典钢材表面缺陷集 ──
        {
            "dataset_name": "NEU-CLS / NEU-DET",
            "description": (
                "东北大学发布的经典钢材表面缺陷数据集，含 1800 张热轧钢带表面图像，"
                "标注 6 类缺陷（裂纹、夹杂、斑点、麻点、轧入氧化皮、划痕），提供"
                "检测框与分类标签。RGB 视角，是钢材表面缺陷检测研究最常用的基准，"
                "可作为焊接母材表面质量评估的参考数据集。"
            ),
            "source_url": "http://faculty.neu.edu.cn/songkechen/zh_CN/zdylm/263270/list/",
            "image_count": "1800张",
            "defect_types": ["裂纹", "夹杂", "斑点", "麻点", "轧入氧化皮", "划痕"],
            "modality": "RGB",
            "industry": "钢材",
            "applicable_scenarios": ["钢材表面缺陷检测基准", "缺陷分类训练", "母材质量评估参考", "外观缺陷参考"],
            "license": "研究用途（详见项目声明）",
        },
        # ── 6. BTAD ── 工业品异常检测 ──
        {
            "dataset_name": "BTAD",
            "description": (
                "BTAD（BeanTech Anomaly Detection）涵盖 3 类工业产品（商品、连接器、"
                "挤压件），提供像素级异常分割标注。RGB 视角，适合工业品异常检测的"
                "小样本场景研究，可作为焊接外观缺陷检测的辅助参考数据集。"
            ),
            "source_url": "https://avires.dimi.uniud.it/papers/btad/btad.zip",
            "image_count": "3类工业品",
            "defect_types": ["划痕", "污渍", "破损", "变色", "变形", "杂质"],
            "modality": "RGB",
            "industry": "通用工业",
            "applicable_scenarios": ["工业品异常检测", "小样本缺陷分割", "外观缺陷参考"],
            "license": "研究用途（详见项目声明）",
        },
        # ── 7. MPDD ── 金属零件异常检测 ──
        {
            "dataset_name": "MPDD",
            "description": (
                "MPDD（Metal Parts Defect Detection）面向金属零件表面缺陷检测，"
                "涵盖 6 类缺陷，强调方向性与光照变化对检测的影响。RGB 视角拍摄，"
                "适合金属加工件表面缺陷检测研究，可作为焊接金属工件外观检测的"
                "参考数据集。"
            ),
            "source_url": "https://github.com/stephenms289/MPDD",
            "image_count": "6类金属零件",
            "defect_types": ["划痕", "污渍", "变色", "破损", "变形", "杂质"],
            "modality": "RGB",
            "industry": "金属加工",
            "applicable_scenarios": ["金属零件缺陷检测", "方向性缺陷识别", "外观缺陷参考"],
            "license": "MIT",
        },
        # ── 8. DAGM ── 纹理异常检测 ──
        {
            "dataset_name": "DAGM",
            "description": (
                "DAGM 2007 竞赛数据集，包含 10 类人工合成纹理图像，每类含少量异常"
                "样本，适合纹理异常检测与小样本无监督方法研究。RGB 视角，可作为"
                "焊缝表面纹理一致性评估的方法学参考（非缺陷类型参考），用于借鉴"
                "纹理基线建模与异常区域定位的算法思路。"
            ),
            "source_url": "https://www.dagm.de/about-dagm/competitions/dagm-2007-challenge.html",
            "image_count": "10类纹理",
            "defect_types": ["纹理缺陷", "异常纹理", "污渍", "斑点"],
            "modality": "RGB",
            "industry": "通用工业/纹理",
            "applicable_scenarios": ["纹理异常检测", "小样本无监督检测", "方法学参考"],
            "license": "研究用途（竞赛数据）",
        },
        # ── 9. KolektorSDD ── 电子换向器表面缺陷 ──
        {
            "dataset_name": "KolektorSDD",
            "description": (
                "KolektorSDD 采集自电子换向器（commutator）生产现场，含 349 张"
                "表面缺陷图像，提供像素级分割标注。RGB 视角，缺陷为微细表面瑕疵，"
                "适合精密电子件表面缺陷分割研究，可作为焊接细微外观缺陷（如咬边、"
                "表面裂纹）分割方法参考。"
            ),
            "source_url": "https://www.vicos.si/resources/kolektorsdd/",
            "image_count": "349张",
            "defect_types": ["划痕", "裂纹", "表面瑕疵", "凹痕"],
            "modality": "RGB",
            "industry": "电子",
            "applicable_scenarios": ["精密件表面缺陷分割", "微细缺陷检测", "分割方法参考"],
            "license": "CC BY 4.0",
        },
        # ── 10. RIAWELC ── X光焊缝缺陷（大规模）──
        {
            "dataset_name": "RIAWELC",
            "description": (
                "RIAWELC 是目前规模较大的公开 X 光焊缝缺陷数据集，含 24407 张图像，"
                "标注 4 类（裂纹、气孔、未熔透、无缺陷）。X 光（NDT）视角成像，"
                "与 RGB 截面测量流程视角不完全对齐，更适合当作缺陷类型语义参考"
                "而非直接训练数据，可用于焊缝内部缺陷类型语义与判定逻辑的参考。"
            ),
            "source_url": "https://github.com/agustif/RIAWELC",
            "image_count": "24407张",
            "defect_types": ["裂纹", "气孔", "未熔透", "无缺陷"],
            "modality": "X-ray",
            "industry": "焊接",
            "applicable_scenarios": ["焊缝内部缺陷语义参考", "缺陷类型对照", "射线检测辅助参考"],
            "license": "开源（详见仓库声明）",
        },
        # ── 11. SWRD ── T型接头焊缝X光 ──
        {
            "dataset_name": "SWRD",
            "description": (
                "SWRD（Surface Welding Robust Dataset）聚焦 T 型接头焊缝，含 3600+"
                "张 X 光图像，采用多边形标注缺陷区域。X 光（NDT）视角，与 RGB 截面"
                "测量流程视角不完全对齐，更适合当作缺陷类型语义参考而非直接训练"
                "数据，可用于 T 型接头焊缝缺陷形态与边界定义的参考。"
            ),
            "source_url": "https://github.com/CMMM-materials/SWRD",
            "image_count": "3600+张",
            "defect_types": ["气孔", "夹渣", "未熔合", "裂纹", "咬边", "焊瘤", "未焊透"],
            "modality": "X-ray",
            "industry": "焊接",
            "applicable_scenarios": ["T型接头焊缝缺陷参考", "缺陷边界标注参考", "缺陷类型语义对照"],
            "license": "开源（详见仓库声明）",
        },
        # ── 12. WDXI ── X光焊缝缺陷（7类）──
        {
            "dataset_name": "WDXI",
            "description": (
                "WDXI 提供 13766 张 X 光焊缝图像，标注 7 类缺陷，规模较大。X 光"
                "（NDT）视角成像，与 RGB 截面测量流程视角不完全对齐，更适合当作"
                "缺陷类型语义参考而非直接训练数据，可用于焊缝内部多类缺陷的"
                "分类与判定逻辑参考。"
            ),
            "source_url": "https://github.com/MridulGupta01/Welding-Defects",
            "image_count": "13766张",
            "defect_types": ["裂纹", "气孔", "夹渣", "未熔合", "未焊透", "咬边", "焊瘤"],
            "modality": "X-ray",
            "industry": "焊接",
            "applicable_scenarios": ["焊缝内部缺陷分类参考", "多类缺陷语义对照", "射线检测辅助参考"],
            "license": "开源（详见仓库声明）",
        },
        # ── 13. GDXray Welding ── 焊缝气孔像素级分割 ──
        {
            "dataset_name": "GDXray Welding",
            "description": (
                "GDXray Welding 子集聚焦金属管道焊接气孔缺陷，含 88 张 X 光图像，"
                "提供像素级分割标注，标注质量精细。X 光（NDT）视角，与 RGB 截面"
                "测量流程视角不完全对齐，更适合当作缺陷类型语义参考而非直接"
                "训练数据，可用于焊缝气孔缺陷形态与分割方法的参考。"
            ),
            "source_url": "https://github.com/DIAG-DGSM/iSEGM",
            "image_count": "88张",
            "defect_types": ["气孔", "夹渣", "未熔合", "裂纹"],
            "modality": "X-ray",
            "industry": "焊接",
            "applicable_scenarios": ["焊缝气孔分割参考", "缺陷形态对照", "分割方法学参考"],
            "license": "研究用途（详见仓库声明）",
        },
        # ── 14. Roboflow x-ray-weld-defect ── Roboflow Universe 焊缝X光缺陷 ──
        {
            "dataset_name": "Roboflow x-ray-weld-defect",
            "description": (
                "Roboflow Universe 上由社区贡献的焊缝 X 光缺陷数据集，包含气孔、"
                "夹渣、裂纹等常见焊缝缺陷的检测框标注，版本与许可随上传者而异。"
                "X 光（NDT）视角，与 RGB 截面测量流程视角不完全对齐，更适合当作"
                "缺陷类型语义参考而非直接训练数据，可用于焊缝缺陷快速原型验证"
                "与标注规范参考。"
            ),
            "source_url": "https://universe.roboflow.com/",
            "image_count": "社区上传（版本不一）",
            "defect_types": ["气孔", "夹渣", "裂纹", "未焊透", "未熔合"],
            "modality": "X-ray",
            "industry": "焊接",
            "applicable_scenarios": ["焊缝缺陷原型验证", "标注规范参考", "缺陷类型语义对照"],
            "license": "随上传者声明（多为 CC BY 系列）",
        },
    ]

    # 为每个数据集拼接 search_text，便于 embedding 语义检索
    for d in datasets:
        d["search_text"] = _build_search_text(d)

    return datasets


def _build_search_text(d: dict) -> str:
    """把数据集字段拼成一段自然语言搜索文本，便于 embedding 语义检索。

    search_text 综合包含：数据集名称、图像规模、缺陷类型、行业、成像方式、
    适用场景，以及是否适合直接训练或仅作语义参考，确保 RAG 检索能从多维度命中。
    """
    # 判断是否适合直接训练：X光/超声等NDT焊接数据集更适合语义参考
    modality = d["modality"]
    industry = d["industry"]
    if modality in ("X-ray", "超声", "红外") and industry == "焊接":
        usage_hint = "该数据集为NDT视角，与RGB截面测量流程视角不完全对齐，更适合作为缺陷类型语义参考而非直接训练数据"
    else:
        usage_hint = "该数据集可作为异常检测/缺陷检测模型的训练或迁移学习参考"

    parts = [
        f"数据集{d['dataset_name']}",
        f"共{d['image_count']}图像",
        f"成像方式为{modality}",
        f"属于{d['industry']}行业",
    ]
    if d.get("defect_types"):
        parts.append(f"缺陷类型包括{'、'.join(d['defect_types'])}")
    parts.append(f"适用场景为{'、'.join(d['applicable_scenarios'])}")
    parts.append(usage_hint)
    return "，".join(parts) + "。"
