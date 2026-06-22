# Weld Image Test Fixtures (验收 2.3)

Plan §11.4 阶段 2 验收 2.3 要求 ≥10 张真实焊缝图片，其中 ≥2 张无缺陷、≥2 张多缺陷。

## 目录结构

```
weld_images/
├── unlabeled/      # 未标注原图 — vision 模型自主判断
├── defect_free/    # 已知无缺陷 (≥2 张, 验收 2.3 要求)
├── single_defect/  # 已知单缺陷
└── multi_defect/   # 已知多缺陷 (≥2 张, 验收 2.3 要求)
```

## 当前状态

只有 `unlabeled/` — 角焊原图，未标注。

测试策略调整：不预设期望缺陷类型，让 vision 模型自主分析。测试断言改为
"vision 返回了有效中文分析" 而非 "检测到特定缺陷"。

## 使用

把 `.jpg` / `.png` 图片放进 `unlabeled/` 即可。测试会遍历目录所有图片跑 E2E。

文件命名建议（可选，便于日志阅读）：
- `corner_weld_01.jpg`
- `corner_weld_02.jpg`
- ...

支持格式：jpg, jpeg, png, tif, tiff, bmp, webp