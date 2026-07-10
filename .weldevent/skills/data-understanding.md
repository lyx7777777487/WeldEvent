---
name: data_understanding
description: 数据理解与数据集质量评估
mode: inline
triggers:
  - 数据集
  - 数据质量
  - 数据分布
  - 数据探查
  - 数据清洗
  - 标签质量
  - 标注质量
  - 数据偏差
  - dataset
  - 数据分析
tools:
  - delegate
  - analyze_image
  - read_weldmap
  - search_vision_knowledge
priority: 15
---

# 数据理解

当用户提到数据集分析、数据质量、数据分布、数据探查时，你应该：

## 轻量场景（少量图片/单张图理解）

直接用 `analyze_image` 分析单张图，用你自己的认知能力理解数据内容。不需要委派。

## 深度场景（整批数据/数据集级分析）

委派给 `data-understanding` subagent 执行深度理解：

```
delegate('data-understanding', '分析 /path/to/images 数据集的数据质量，无标注数据')
delegate('data-understanding', '分析 /path/to/labeled 数据集的标签质量，有标注数据，标签: {...}')
```

subagent 会自动完成：
1. 统计与 CV 特征分析（尺寸/灰度/清晰度/重复/异常）
2. 多模态语义理解（对象识别/缺陷类别/场景语义/数据偏差）
3. 有标注数据额外做标签统计分析
4. 返回结构化理解报告 + 可操作建议

## 公开数据集参考

用户问"有没有公开的焊缝/缺陷数据集可用"时，调 `search_vision_knowledge` 查工业图像数据集元信息（MVTec AD/VisA/RIAWELC/SWRD/WDXI 等 14 个数据集），返回数据集名称、规模、缺陷类型、适用场景。

## 判断原则

- 用户说"这张图怎么样" → 轻量场景，直接 analyze_image
- 用户说"这批数据怎么样"/"数据集质量" → 深度场景，delegate 给 data-understanding
- 用户说"标签质量"/"标注一致性" → 深度场景，delegate 给 data-understanding（有标注管线）
- 用户说"有没有公开数据集"/"参考数据集" → 调 search_vision_knowledge
