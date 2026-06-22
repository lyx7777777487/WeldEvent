"""IQA Activity — 图像质量评估执行单元。

Source: WeldEvent架构方案 §5.1 (IQA职责)

设计原则:
  - 规则层为主（确定性CV函数，<10ms）
  - MLLM可选增强（语义级检测，条件触发）
  - Activity是被动的执行单元，不具备自主性

接口一致性:
  - 使用controlplane.domain.activity的ActivityInput/ActivityOutput
  - workflow_id从workflow_context.workflow_id获取

职责:
  1. 图像分辨率检查
  2. 曝光质量检查
  3. 对焦质量检查（Laplacian方差）
  4. 完整性检查（焊缝区域是否被裁切）
  5. MLLM深度视觉（可选，遮挡/污染/异物检测）
  6. 路由决策（AUTO_PASS / SUGGEST_REVIEW / MANDATORY_REVIEW / REJECT）

输出:
  - 写入WeldMap: image/quality
  - 返回ActivityOutput给Control Plane
"""

import asyncio
from typing import Any

from numpy.typing import NDArray
import numpy as np

from .base import (
    BaseActivity,
    ActivityInput,
    ActivityOutput,
    ActivityStatus,
    ActivityMetadata,
)
from ..capabilities.cv_rules import (
    CVRuleChecker,
    RuleEngineOutput,
    RuleResult,
    ResolutionRule,
    ExposureRule,
    FocusRule,
    CompletenessRule,
)
from ..capabilities.mllm_provider import MllmProvider, ImageInput
from ..capabilities.vision.preprocessor import ImagePreprocessingPipeline
from ..config.quality_standard import QualityStandard, QualityStandardRegistry
from ..weldmap.client import WeldMapClient
from ..weldmap.models import (
    WorkflowId,
    ImageQualityReport,
    ResolutionCheck,
    ExposureCheck,
    FocusCheck,
    CompletenessCheck,
)


class IqaActivity(BaseActivity):
    """图像质量评估Activity
    
    规则层为主，MLLM可选增强
    
    Usage::
        # 创建IQA Activity
        iqa = IqaActivity(
            weldmap=weldmap_client,
            cv_checker=cv_rule_checker,
            mllm=mllm_provider,  # 可选
            standard_registry=registry,
        )
        
        # 执行检测（由L2 Control Plane调度）
        output = await iqa.execute(ActivityInput(
            control_point_id="CP0",
            workflow_context={"workflow_id": "WF-001"},
            params={"image_path": "/path/to/image.png"},
        ))
        
        # 查看结果
        print(output.status)  # OK / MARGINAL / NG / ERROR
        print(output.data["route_decision"])  # AUTO_PASS / SUGGEST_REVIEW / ...
    """
    
    def __init__(
        self,
        weldmap: WeldMapClient,
        cv_checker: CVRuleChecker,
        mllm: MllmProvider | None = None,
        standard_registry: QualityStandardRegistry | None = None,
        default_standard_id: str = "macro_weld",
        preprocessor: ImagePreprocessingPipeline | None = None,
    ):
        self._weldmap = weldmap
        self._cv_checker = cv_checker
        self._mllm = mllm
        self._registry = standard_registry or QualityStandardRegistry()
        self._default_standard_id = default_standard_id
        self._preprocessor = preprocessor or ImagePreprocessingPipeline.default_iqa_pipeline()
        
        self._metadata = ActivityMetadata(
            activity_id="iqa_activity",
            name="图像质量评估",
            version="v2",
            description="规则层为主，MLLM可选增强",
            capabilities=["cv", "rule", "mllm_optional"],
            execution_target="cpu",
            estimated_latency_ms=100,
        )
    
    @property
    def activity_name(self) -> str:
        return "iqa_activity"
    
    @property
    def metadata(self) -> ActivityMetadata:
        return self._metadata
    
    async def execute(self, input: ActivityInput) -> ActivityOutput:
        """执行IQA检测
        
        流程:
          1. 从workflow_context获取workflow_id
          2. 获取质量标准配置
          3. 预处理图像
          4. 规则层检测（必调）
          5. MLLM深度视觉（可选触发）
          6. 构建报告
          7. 写入WeldMap
          8. 返回结果
        """
        # 从workflow_context获取workflow_id
        workflow_id = input.workflow_context.get("workflow_id", "")
        wf_id = WorkflowId(workflow_id)
        params = input.params or {}
        
        # 1. 获取质量标准
        standard_id = params.get("quality_standard_id", self._default_standard_id)
        try:
            standard = self._registry.get(standard_id)
        except KeyError:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"质量标准不存在: {standard_id}",
            )
        
        # 2. 预处理
        preprocessed = await self._preprocessor.run(params=params)
        if not preprocessed.metadata.is_valid:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"预处理失败: {preprocessed.metadata.error}",
                data={"preprocessing_error": preprocessed.metadata.error},
            )
        
        image = preprocessed.image
        if image is None:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error="预处理后图像为空",
            )
        
        # 3. 规则层检测
        rule_output = await self._run_rule_layer(image, standard)
        
        # 4. MLLM深度视觉（可选）
        deep_findings: list[str] = []
        deep_anomalies: list[str] = []
        mllm_triggered = False
        mllm_error: str | None = None
        
        if self._should_trigger_mllm(standard, rule_output):
            mllm_triggered = True
            
            if self._mllm is None:
                mllm_error = "MLLM未配置"
            else:
                try:
                    deep_findings, deep_anomalies = await self._run_mllm(
                        image, standard
                    )
                    
                    if deep_anomalies:
                        rule_output.route_decision = self._downgrade_route(
                            rule_output.route_decision,
                            deep_anomalies,
                        )
                except asyncio.TimeoutError:
                    mllm_error = "MLLM超时"
                except Exception as e:
                    mllm_error = f"MLLM出错: {e}"
        
        # 5. 构建报告
        report = self._build_report(
            rule_output=rule_output,
            deep_findings=deep_findings,
            deep_anomalies=deep_anomalies,
            standard=standard,
        )
        
        # 6. 写入WeldMap
        try:
            await self._weldmap.write_image_quality(wf_id, report)
        except Exception as e:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"WeldMap写入失败: {e}",
            )
        
        # 7. 返回结果
        return self._map_to_output(report, mllm_triggered, mllm_error)
    
    # ------------------------------------------------------------------
    # 规则层
    # ------------------------------------------------------------------
    
    async def _run_rule_layer(
        self,
        image: NDArray[np.uint8],
        standard: QualityStandard,
    ) -> RuleEngineOutput:
        """执行规则层检测
        
        规则层使用确定性CV函数:
          - 分辨率: array.shape
          - 曝光: np.mean()
          - 对焦: Laplacian方差
          - 完整性: 暗区占比
        
        延迟: <10ms
        """
        
        # 构建规则配置
        resolution_rule = ResolutionRule(
            min_width=standard.resolution_min_width,
            min_height=standard.resolution_min_height,
        )
        exposure_rule = ExposureRule(
            min_mean_gray=standard.exposure_min_gray,
            max_mean_gray=standard.exposure_max_gray,
        )
        focus_rule = FocusRule(
            laplacian_threshold=standard.focus_laplacian_min,
        )
        completeness_rule = CompletenessRule(
            min_weld_region_ratio=standard.completeness_weld_region_min_ratio,
        )
        
        # 执行检查
        results = [
            await self._cv_checker.check_resolution(image, resolution_rule),
            await self._cv_checker.check_exposure(image, exposure_rule),
            await self._cv_checker.check_focus(image, focus_rule),
            await self._cv_checker.check_completeness(image, completeness_rule),
        ]
        
        # 计算置信度（加权）
        confidence = self._calculate_confidence(results, standard.weights)
        
        # 路由决策
        route = self._determine_route(confidence, standard)
        
        return RuleEngineOutput(
            results=results,
            overall_confidence=round(confidence, 4),
            route_decision=route,
        )
    
    def _calculate_confidence(
        self,
        results: list[RuleResult],
        weights: dict[str, float],
    ) -> float:
        """计算综合置信度（加权）"""
        
        # 映射规则ID到权重键
        rule_weight_map = {
            "RES": "resolution",
            "EXP": "exposure",
            "FOC": "focus",
            "FOCUS": "focus",
            "COMP": "completeness",
        }
        
        total_weight = 0.0
        weighted_score = 0.0
        
        for r in results:
            # 找到权重键
            weight_key = None
            for key_prefix, wkey in rule_weight_map.items():
                if key_prefix in r.rule_id:
                    weight_key = wkey
                    break
            
            weight = weights.get(weight_key, 0.25) if weight_key else 0.25
            
            # 通过的规则贡献满分，未通过的按比例贡献
            if r.passed:
                score = 1.0
            else:
                # 根据值与阈值的差距计算部分得分
                if r.threshold > 0 and r.value > 0:
                    score = min(r.value / r.threshold, 1.0) if r.value < r.threshold else 1.0
                else:
                    score = 0.0
            
            weighted_score += score * weight
            total_weight += weight
        
        return weighted_score / total_weight if total_weight > 0 else 0.0
    
    def _determine_route(
        self,
        confidence: float,
        standard: QualityStandard,
    ) -> str:
        """确定路由决策"""
        if confidence >= standard.auto_pass_threshold:
            return "AUTO_PASS"
        elif confidence >= standard.suggest_review_threshold:
            return "SUGGEST_REVIEW"
        elif confidence >= standard.mandatory_review_threshold:
            return "MANDATORY_REVIEW"
        else:
            return "REJECT"
    
    # ------------------------------------------------------------------
    # MLLM层
    # ------------------------------------------------------------------
    
    def _should_trigger_mllm(
        self,
        standard: QualityStandard,
        rule_output: RuleEngineOutput,
    ) -> bool:
        """判断是否触发MLLM
        
        触发条件:
          1. deep_vision_enabled = True
          2. 置信度 < deep_vision_trigger_threshold
        """
        if not standard.deep_vision_enabled:
            return False
        return rule_output.overall_confidence < standard.deep_vision_trigger_threshold
    
    async def _run_mllm(
        self,
        image: NDArray[np.uint8],
        standard: QualityStandard,
    ) -> tuple[list[str], list[str]]:
        """执行MLLM深度视觉检测
        
        检测项目（可配置）:
          - occlusion: 遮挡
          - lens_contamination: 镜头污染
          - foreign_object: 异物
          - reflection: 反光
          - abnormal_texture: 异常纹理
        
        延迟: 1-3s（有超时保护）
        """
        
        # 编码图像为bytes
        image_bytes = self._encode_image(image)
        
        # 构建MLLM输入
        mllm_input = ImageInput(image_data=image_bytes)
        
        # 调用MLLM（带超时）
        result = await asyncio.wait_for(
            self._mllm.inspect_image(
                mllm_input,
                criteria={"check": standard.deep_vision_check_items},
            ),
            timeout=standard.deep_vision_max_latency_ms / 1000.0,
        )
        
        findings = result.get("findings", [])
        anomalies = result.get("anomalies", [])
        
        # 确保返回列表
        if isinstance(findings, str):
            findings = [findings]
        if isinstance(anomalies, str):
            anomalies = [anomalies]
        
        return findings, anomalies
    
    def _encode_image(self, image: NDArray[np.uint8]) -> bytes:
        """编码图像为JPEG bytes"""
        try:
            import cv2
            _, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
            return buf.tobytes()
        except ImportError:
            # 无OpenCV时用numpy tobytes
            return image.tobytes()
    
    def _downgrade_route(
        self,
        current_route: str,
        anomalies: list[str],
    ) -> str:
        """降级路由决策
        
        MLLM发现异常时，根据异常严重程度降级路由
        """
        route_priority = {
            "AUTO_PASS": 0,
            "SUGGEST_REVIEW": 1,
            "MANDATORY_REVIEW": 2,
            "REJECT": 3,
        }
        current_level = route_priority.get(current_route, 0)
        
        # 有异常至少升到SUGGEST_REVIEW
        current_level = max(current_level, 1)
        
        # 严重异常升到MANDATORY_REVIEW
        severe_keywords = {"遮挡", "污染", "异物", "occlusion", "contamination", "foreign"}
        for a in anomalies:
            if any(kw in a.lower() for kw in severe_keywords):
                current_level = max(current_level, 2)
                break
        
        # 多个异常直接升到MANDATORY_REVIEW
        if len(anomalies) >= 2:
            current_level = max(current_level, 2)
        
        reverse_map = {v: k for k, v in route_priority.items()}
        return reverse_map.get(current_level, "SUGGEST_REVIEW")
    
    # ------------------------------------------------------------------
    # 报告构建
    # ------------------------------------------------------------------
    
    def _build_report(
        self,
        rule_output: RuleEngineOutput,
        deep_findings: list[str],
        deep_anomalies: list[str],
        standard: QualityStandard,
    ) -> ImageQualityReport:
        """构建质量报告
        
        报告写入WeldMap: image/quality
        """
        
        # 解析规则结果
        resolution = ResolutionCheck(
            width=0, height=0, passed=False, detail="未检测",
            min_required=(standard.resolution_min_width, standard.resolution_min_height),
        )
        exposure = ExposureCheck(
            mean_gray=0.0, passed=False, detail="未检测",
            valid_range=(standard.exposure_min_gray, standard.exposure_max_gray),
        )
        focus = FocusCheck(
            laplacian_variance=0.0, passed=False, detail="未检测",
            threshold=standard.focus_laplacian_min,
        )
        completeness = CompletenessCheck(
            weld_region_present=True, passed=True, detail="未检测",
        )
        
        for r in rule_output.results:
            if "RES" in r.rule_id:
                resolution = ResolutionCheck(
                    width=int(r.value) if r.value else 0,
                    height=0,
                    min_required=(standard.resolution_min_width, standard.resolution_min_height),
                    passed=r.passed,
                    detail=r.detail,
                )
            elif "EXP" in r.rule_id:
                exposure = ExposureCheck(
                    mean_gray=r.value,
                    valid_range=(standard.exposure_min_gray, standard.exposure_max_gray),
                    passed=r.passed,
                    detail=r.detail,
                )
            elif "FOC" in r.rule_id or "FOCUS" in r.rule_id:
                focus = FocusCheck(
                    laplacian_variance=r.value,
                    threshold=standard.focus_laplacian_min,
                    passed=r.passed,
                    detail=r.detail,
                )
            elif "COMP" in r.rule_id:
                completeness = CompletenessCheck(
                    weld_region_present=r.passed,
                    passed=r.passed,
                    detail=r.detail,
                )
        
        return ImageQualityReport(
            resolution=resolution,
            exposure=exposure,
            focus=focus,
            completeness=completeness,
            overall_confidence=rule_output.overall_confidence,
            deep_vision_findings=deep_findings,
            deep_vision_anomalies=deep_anomalies,
            route_decision=rule_output.route_decision,
            inspected_by=self.activity_name,
        )
    
    def _map_to_output(
        self,
        report: ImageQualityReport,
        mllm_triggered: bool,
        mllm_error: str | None,
    ) -> ActivityOutput:
        """映射为ActivityOutput
        
        路由决策映射:
          - AUTO_PASS → OK
          - SUGGEST_REVIEW → MARGINAL
          - MANDATORY_REVIEW → MARGINAL
          - REJECT → NG
        
        返回给L2的信息:
          - route_decision: 路由决策（L2根据此决定是否继续）
          - confidence: 综合置信度
          - checks: 各项检测结果（供PPA参考）
          - issues: 检测到的问题列表（供PPA调整策略）
          - deep_vision: MLLM检测结果
        """
        
        route = report.route_decision
        
        if route == "REJECT":
            status = ActivityStatus.NG
        elif route in ("MANDATORY_REVIEW", "SUGGEST_REVIEW"):
            status = ActivityStatus.MARGINAL
        else:
            status = ActivityStatus.OK
        
        # 收集检测到的问题（供PPA参考）
        issues: list[str] = []
        if not report.resolution.passed:
            issues.append("resolution_insufficient")
        if not report.exposure.passed:
            issues.append("exposure_abnormal")
            if report.exposure.mean_gray < report.exposure.valid_range[0]:
                issues.append("exposure_too_dark")
            elif report.exposure.mean_gray > report.exposure.valid_range[1]:
                issues.append("exposure_too_bright")
        if not report.focus.passed:
            issues.append("focus_blur")
        if not report.completeness.passed:
            issues.append("completeness_crop")
        
        # MLLM发现的问题
        if report.deep_vision_anomalies:
            for anomaly in report.deep_vision_anomalies:
                issues.append(f"deep_vision:{anomaly}")
        
        return ActivityOutput(
            status=status,
            data={
                # 路由决策（L2核心关注）
                "route_decision": route,
                "confidence": report.overall_confidence,
                
                # 各项检测结果（供PPA参考）
                "checks": {
                    "resolution": {
                        "passed": report.resolution.passed,
                        "width": report.resolution.width,
                        "height": report.resolution.height,
                        "detail": report.resolution.detail,
                    },
                    "exposure": {
                        "passed": report.exposure.passed,
                        "mean_gray": report.exposure.mean_gray,
                        "valid_range": report.exposure.valid_range,
                        "detail": report.exposure.detail,
                    },
                    "focus": {
                        "passed": report.focus.passed,
                        "laplacian_variance": report.focus.laplacian_variance,
                        "threshold": report.focus.threshold,
                        "detail": report.focus.detail,
                    },
                    "completeness": {
                        "passed": report.completeness.passed,
                        "detail": report.completeness.detail,
                    },
                },
                
                # 问题列表（供PPA调整预处理策略）
                "issues": issues,
                
                # MLLM结果
                "deep_vision": {
                    "triggered": mllm_triggered,
                    "findings": report.deep_vision_findings,
                    "anomalies": report.deep_vision_anomalies,
                    "error": mllm_error,
                },
                
                # 元数据
                "inspected_by": report.inspected_by,
                "inspected_at": report.inspected_at.isoformat(),
            },
        )