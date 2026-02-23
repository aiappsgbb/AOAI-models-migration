"""
Model Comparator Module
Compares evaluation results between different models (GPT-4 vs GPT-5).

Supports parallel execution: both models can be evaluated simultaneously
and Foundry LLM-as-judge submissions run concurrently.
"""

import json
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import logging
import numpy as np

from .evaluator import ModelEvaluator, EvaluationResult, _run_in_loop
from .metrics import (
    ClassificationMetrics,
    ConsistencyMetrics,
    LatencyMetrics,
    QualityMetrics,
    MetricsCalculator
)
from ..clients.azure_openai import AzureOpenAIClient

logger = logging.getLogger(__name__)


@dataclass
class ComparisonDimension:
    """A single dimension of comparison between models"""
    dimension: str
    model_a_value: float
    model_b_value: float
    difference: float
    percent_change: float
    better_model: str
    significance: str  # 'high', 'medium', 'low', 'negligible'
    
    def to_dict(self) -> Dict:
        return {
            'dimension': self.dimension,
            'model_a_value': self.model_a_value,
            'model_b_value': self.model_b_value,
            'difference': self.difference,
            'percent_change': self.percent_change,
            'better_model': self.better_model,
            'significance': self.significance
        }


@dataclass
class ComparisonReport:
    """Complete comparison report between two models"""
    model_a: str
    model_b: str
    timestamp: str
    evaluation_type: str
    dimensions: List[ComparisonDimension]
    summary: Dict[str, Any]
    recommendations: List[str]
    raw_results_a: Optional[List[Dict]] = None
    raw_results_b: Optional[List[Dict]] = None
    statistical_significance: Optional[Dict[str, Any]] = None  # NEW
    foundry_scores_a: Optional[Dict[str, Any]] = None
    foundry_scores_b: Optional[Dict[str, Any]] = None
    foundry_meta: Optional[Dict[str, Any]] = None
    
    @staticmethod
    def _sanitize(obj):
        """Recursively cast numpy scalars to native Python types for JSON safety."""
        if isinstance(obj, dict):
            return {k: ComparisonReport._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [ComparisonReport._sanitize(v) for v in obj]
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    def to_dict(self) -> Dict:
        raw = {
            'model_a': self.model_a,
            'model_b': self.model_b,
            'timestamp': self.timestamp,
            'evaluation_type': self.evaluation_type,
            'dimensions': [d.to_dict() for d in self.dimensions],
            'summary': self.summary,
            'recommendations': self.recommendations,
            'statistical_significance': self.statistical_significance,
            'foundry_scores_a': self.foundry_scores_a,
            'foundry_scores_b': self.foundry_scores_b,
            'foundry_meta': self.foundry_meta,
        }
        return self._sanitize(raw)
        
    def save(self, output_dir: str = "data/results"):
        """Save comparison report to JSON file (atomic write to prevent truncation)"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        filename = f"comparison_{self.model_a}_vs_{self.model_b}_{self.evaluation_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        final_path = output_path / filename
        tmp_path = final_path.with_suffix('.json.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, indent=2)
            tmp_path.replace(final_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
            
    def to_markdown(self) -> str:
        """Generate markdown summary of comparison"""
        md = f"""# Model Comparison Report: {self.model_a} vs {self.model_b}

**Evaluation Type:** {self.evaluation_type}  
**Generated:** {self.timestamp}

## Summary

| Metric | {self.model_a} | {self.model_b} | Winner |
|--------|-------|-------|--------|
"""
        for dim in self.dimensions:
            winner = dim.better_model if dim.significance != 'negligible' else "~"
            md += f"| {dim.dimension} | {dim.model_a_value:.4f} | {dim.model_b_value:.4f} | {winner} |\n"
            
        md += "\n## Key Findings\n\n"
        for rec in self.recommendations:
            md += f"- {rec}\n"
            
        return md


class ModelComparator:
    """
    Compares evaluation results between Azure OpenAI models.
    Generates detailed comparison reports with recommendations.
    """
    
    def __init__(
        self,
        client: AzureOpenAIClient,
        evaluator: Optional[ModelEvaluator] = None,
        foundry_evaluator: Optional[Any] = None,
        parallel_models: bool = True,
    ):
        """
        Initialize the comparator.
        
        Args:
            client: AzureOpenAIClient for running evaluations
            evaluator: Optional ModelEvaluator instance
            foundry_evaluator: Optional FoundryEvaluator instance
            parallel_models: If True, evaluate both models simultaneously
        """
        self.client = client
        self.evaluator = evaluator or ModelEvaluator(client)
        self.foundry_evaluator = foundry_evaluator
        self.parallel_models = parallel_models
        
        # Significance thresholds
        self.thresholds = {
            'high': 0.10,      # 10% difference
            'medium': 0.05,   # 5% difference
            'low': 0.02       # 2% difference
        }
        
    def compare_models(
        self,
        model_a: str,
        model_b: str,
        evaluation_type: str = "classification",
        run_evaluations: bool = True,
        existing_results: Optional[Tuple[EvaluationResult, EvaluationResult]] = None,
        include_foundry: bool = False,
    ) -> ComparisonReport:
        """
        Compare two models on a specific evaluation type.
        Delegates to the async implementation for parallel execution.
        """
        return _run_in_loop(self.compare_models_async(
            model_a, model_b, evaluation_type,
            run_evaluations=run_evaluations,
            existing_results=existing_results,
            include_foundry=include_foundry,
        ))

    async def compare_models_async(
        self,
        model_a: str,
        model_b: str,
        evaluation_type: str = "classification",
        run_evaluations: bool = True,
        existing_results: Optional[Tuple[EvaluationResult, EvaluationResult]] = None,
        include_foundry: bool = False,
    ) -> ComparisonReport:
        """
        Async comparison: evaluates both models in parallel (when
        ``parallel_models`` is enabled), then optionally submits both
        Foundry evaluations concurrently.
        """
        # Get evaluation results
        if existing_results:
            result_a, result_b = existing_results
        elif run_evaluations:
            result_a, result_b = await self._run_evaluations_async(
                model_a, model_b, evaluation_type
            )
        else:
            raise ValueError("Must provide existing_results or set run_evaluations=True")
            
        foundry_scores_a: Optional[Dict[str, Any]] = None
        foundry_scores_b: Optional[Dict[str, Any]] = None
        foundry_meta: Optional[Dict[str, Any]] = None

        # Optional: submit both model outputs to Foundry LLM-as-judge
        if include_foundry:
            foundry_meta = {
                'enabled': True,
                'completed': False,
                'errors': [],
                'model_a': {'eval_id': None, 'run_id': None, 'report_url': None},
                'model_b': {'eval_id': None, 'run_id': None, 'report_url': None},
            }
            if self.foundry_evaluator is None:
                foundry_meta['errors'].append('Foundry evaluator is not configured.')
            else:
                foundry_scores_a, foundry_scores_b, foundry_meta = await self._run_foundry_parallel(
                    result_a, result_b, evaluation_type, model_a, model_b, foundry_meta
                )

        # Generate comparison dimensions
        dimensions = self._generate_dimensions(
            result_a,
            result_b,
            evaluation_type,
            foundry_scores_a=foundry_scores_a,
            foundry_scores_b=foundry_scores_b,
        )
        
        # Generate summary
        summary = self._generate_summary(dimensions, model_a, model_b)
        if include_foundry:
            foundry_dims = [d for d in dimensions if d.dimension.endswith('(Foundry)')]
            summary['foundry'] = {
                'enabled': True,
                'metrics_compared': len(foundry_dims),
                'completed': bool(foundry_scores_a and foundry_scores_b),
                'errors': (foundry_meta or {}).get('errors', []),
            }
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            dimensions, result_a, result_b, model_a, model_b
        )
        
        # Statistical significance tests
        statistical_significance = None
        if result_a.raw_results and result_b.raw_results:
            try:
                statistical_significance = MetricsCalculator.calculate_statistical_significance(
                    result_a.raw_results, result_b.raw_results
                )
            except Exception:
                pass
        
        return ComparisonReport(
            model_a=model_a,
            model_b=model_b,
            timestamp=datetime.now().isoformat(),
            evaluation_type=evaluation_type,
            dimensions=dimensions,
            summary=summary,
            recommendations=recommendations,
            raw_results_a=result_a.raw_results,
            raw_results_b=result_b.raw_results,
            statistical_significance=statistical_significance,
            foundry_scores_a=foundry_scores_a,
            foundry_scores_b=foundry_scores_b,
            foundry_meta=foundry_meta,
        )
        
    def compare_full(
        self,
        model_a: str = "gpt4",
        model_b: str = "gpt5"
    ) -> Dict[str, ComparisonReport]:
        """
        Run full comparison across all evaluation types.
        
        Args:
            model_a: First model name
            model_b: Second model name
            
        Returns:
            Dictionary of ComparisonReports by evaluation type
        """
        reports = {}
        
        for eval_type in ['classification', 'dialog', 'general']:
            reports[eval_type] = self.compare_models(
                model_a, model_b, eval_type
            )
            
        return reports
        
    def _run_evaluations(
        self,
        model_a: str,
        model_b: str,
        evaluation_type: str
    ) -> Tuple[EvaluationResult, EvaluationResult]:
        """Run evaluations for both models (sync wrapper)."""
        return _run_in_loop(self._run_evaluations_async(model_a, model_b, evaluation_type))

    async def _run_evaluations_async(
        self,
        model_a: str,
        model_b: str,
        evaluation_type: str
    ) -> Tuple[EvaluationResult, EvaluationResult]:
        """Run evaluations for both models, optionally in parallel."""

        def _get_coro(model: str):
            if evaluation_type == "classification":
                return self.evaluator.evaluate_classification_async(model)
            elif evaluation_type == "dialog":
                return self.evaluator.evaluate_dialog_async(model)
            else:
                return self.evaluator.evaluate_general_async(model)

        if self.parallel_models:
            logger.info(f"Running {evaluation_type} evaluation for {model_a} and {model_b} in parallel")
            result_a, result_b = await asyncio.gather(
                _get_coro(model_a),
                _get_coro(model_b),
            )
        else:
            logger.info(f"Running {evaluation_type} evaluation for {model_a} then {model_b} sequentially")
            result_a = await _get_coro(model_a)
            result_b = await _get_coro(model_b)

        return result_a, result_b

    async def _run_foundry_parallel(
        self,
        result_a: EvaluationResult,
        result_b: EvaluationResult,
        evaluation_type: str,
        model_a: str,
        model_b: str,
        foundry_meta: Dict[str, Any],
    ) -> Tuple[Optional[Dict], Optional[Dict], Dict]:
        """Submit both Foundry evaluations concurrently via a thread pool.

        ``submit_evaluation`` is a blocking call (polls for completion), so
        we run both inside ``asyncio.to_thread`` to overlap their I/O waits.
        """
        foundry_scores_a: Optional[Dict[str, Any]] = None
        foundry_scores_b: Optional[Dict[str, Any]] = None

        async def _submit(raw_results, model_name, meta_key):
            try:
                res = await asyncio.to_thread(
                    self.foundry_evaluator.submit_evaluation,
                    raw_results=raw_results,
                    evaluation_type=evaluation_type,
                    model_name=model_name,
                    poll=True,
                    timeout=300,
                )
                foundry_meta[meta_key] = {
                    'eval_id': res.get('eval_id'),
                    'run_id': res.get('run_id'),
                    'report_url': res.get('report_url'),
                    'status': res.get('status'),
                }
                return res.get('foundry_scores')
            except Exception as e:
                foundry_meta['errors'].append(f"{model_name}: {e}")
                return None

        logger.info(f"Submitting Foundry evaluations for {model_a} and {model_b} in parallel")
        foundry_scores_a, foundry_scores_b = await asyncio.gather(
            _submit(result_a.raw_results, model_a, 'model_a'),
            _submit(result_b.raw_results, model_b, 'model_b'),
        )

        if foundry_scores_a is None:
            foundry_meta['errors'].append(f"No Foundry scores returned for {model_a}.")
        if foundry_scores_b is None:
            foundry_meta['errors'].append(f"No Foundry scores returned for {model_b}.")
        foundry_meta['completed'] = bool(foundry_scores_a and foundry_scores_b)

        return foundry_scores_a, foundry_scores_b, foundry_meta
        
    def _generate_dimensions(
        self,
        result_a: EvaluationResult,
        result_b: EvaluationResult,
        evaluation_type: str,
        foundry_scores_a: Optional[Dict[str, Any]] = None,
        foundry_scores_b: Optional[Dict[str, Any]] = None,
    ) -> List[ComparisonDimension]:
        """Generate comparison dimensions from results"""
        dimensions = []
        
        # Classification metrics
        if result_a.classification_metrics and result_b.classification_metrics:
            cm_a = result_a.classification_metrics
            cm_b = result_b.classification_metrics
            
            dimensions.extend([
                self._create_dimension("Accuracy", cm_a.accuracy, cm_b.accuracy, higher_better=True),
                self._create_dimension("F1 Score", cm_a.f1_score, cm_b.f1_score, higher_better=True),
                self._create_dimension("Precision", cm_a.precision, cm_b.precision, higher_better=True),
                self._create_dimension("Recall", cm_a.recall, cm_b.recall, higher_better=True),
            ])
            
            # NEW: Sub-field accuracy dimensions
            if cm_a.subcategory_accuracy > 0 or cm_b.subcategory_accuracy > 0:
                dimensions.append(
                    self._create_dimension("Subcategory Accuracy", cm_a.subcategory_accuracy, cm_b.subcategory_accuracy, higher_better=True)
                )
            if cm_a.priority_accuracy > 0 or cm_b.priority_accuracy > 0:
                dimensions.append(
                    self._create_dimension("Priority Accuracy", cm_a.priority_accuracy, cm_b.priority_accuracy, higher_better=True)
                )
            if cm_a.sentiment_accuracy > 0 or cm_b.sentiment_accuracy > 0:
                dimensions.append(
                    self._create_dimension("Sentiment Accuracy", cm_a.sentiment_accuracy, cm_b.sentiment_accuracy, higher_better=True)
                )
            if cm_a.avg_confidence > 0 or cm_b.avg_confidence > 0:
                dimensions.append(
                    self._create_dimension("Avg Confidence", cm_a.avg_confidence, cm_b.avg_confidence, higher_better=True)
                )
            
        # Latency metrics
        if result_a.latency_metrics and result_b.latency_metrics:
            lm_a = result_a.latency_metrics
            lm_b = result_b.latency_metrics
            
            dimensions.extend([
                self._create_dimension("Mean Latency", lm_a.mean_latency, lm_b.mean_latency, higher_better=False),
                self._create_dimension("P95 Latency", lm_a.p95_latency, lm_b.p95_latency, higher_better=False),
                self._create_dimension("Latency Std Dev", lm_a.std_latency, lm_b.std_latency, higher_better=False),
            ])
            
            # NEW: Cost & token dimensions
            if lm_a.cost_per_request > 0 or lm_b.cost_per_request > 0:
                dimensions.append(
                    self._create_dimension("Cost/Request (USD)", lm_a.cost_per_request, lm_b.cost_per_request, higher_better=False)
                )
            if lm_a.cache_hit_rate > 0 or lm_b.cache_hit_rate > 0:
                dimensions.append(
                    self._create_dimension("Cache Hit Rate %", lm_a.cache_hit_rate, lm_b.cache_hit_rate, higher_better=True)
                )
            if lm_a.reasoning_token_pct > 0 or lm_b.reasoning_token_pct > 0:
                dimensions.append(
                    self._create_dimension("Reasoning Token %", lm_a.reasoning_token_pct, lm_b.reasoning_token_pct, higher_better=False)
                )
            if lm_a.tokens_per_second > 0 or lm_b.tokens_per_second > 0:
                dimensions.append(
                    self._create_dimension("Tokens/Second", lm_a.tokens_per_second, lm_b.tokens_per_second, higher_better=True)
                )
            
        # Consistency metrics
        if result_a.consistency_metrics and result_b.consistency_metrics:
            cons_a = result_a.consistency_metrics
            cons_b = result_b.consistency_metrics
            
            dimensions.extend([
                self._create_dimension("Reproducibility", cons_a.reproducibility_score, cons_b.reproducibility_score, higher_better=True),
                self._create_dimension("Format Consistency", cons_a.format_consistency, cons_b.format_consistency, higher_better=True),
            ])
            
        # Quality metrics
        if result_a.quality_metrics and result_b.quality_metrics:
            qm_a = result_a.quality_metrics
            qm_b = result_b.quality_metrics
            
            dimensions.extend([
                self._create_dimension("Format Compliance", qm_a.format_compliance, qm_b.format_compliance, higher_better=True),
                self._create_dimension("Completeness", qm_a.completeness, qm_b.completeness, higher_better=True),
            ])
            
            if qm_a.follow_up_quality > 0 or qm_b.follow_up_quality > 0:
                dimensions.append(
                    self._create_dimension("Follow-up Quality", qm_a.follow_up_quality, qm_b.follow_up_quality, higher_better=True)
                )
            if qm_a.relevance > 0 or qm_b.relevance > 0:
                dimensions.append(
                    self._create_dimension("Context Coverage", qm_a.relevance, qm_b.relevance, higher_better=True)
                )
            if qm_a.rule_compliance > 0 or qm_b.rule_compliance > 0:
                dimensions.append(
                    self._create_dimension("Rule Compliance", qm_a.rule_compliance, qm_b.rule_compliance, higher_better=True)
                )
            if qm_a.empathy_score > 0 or qm_b.empathy_score > 0:
                dimensions.append(
                    self._create_dimension("Empathy Score", qm_a.empathy_score, qm_b.empathy_score, higher_better=True)
                )
            if qm_a.optimal_similarity > 0 or qm_b.optimal_similarity > 0:
                dimensions.append(
                    self._create_dimension("Optimal Similarity", qm_a.optimal_similarity, qm_b.optimal_similarity, higher_better=True)
                )
            if qm_a.resolution_efficiency > 0 or qm_b.resolution_efficiency > 0:
                dimensions.append(
                    self._create_dimension("Resolution Efficiency", qm_a.resolution_efficiency, qm_b.resolution_efficiency, higher_better=True)
                )
                
        # Foundry LLM-as-judge metrics (1-5)
        if foundry_scores_a and foundry_scores_b:
            agg_a = foundry_scores_a.get('aggregated', {})
            agg_b = foundry_scores_b.get('aggregated', {})
            foundry_metrics = [
                ('coherence', 'Coherence (Foundry)'),
                ('fluency', 'Fluency (Foundry)'),
                ('relevance', 'Relevance (Foundry)'),
                ('similarity', 'Similarity (Foundry)'),
                ('task_adherence', 'Task Adherence (Foundry)'),
                ('intent_resolution', 'Intent Resolution (Foundry)'),
                ('response_completeness', 'Response Completeness (Foundry)'),
            ]
            for key, label in foundry_metrics:
                va = agg_a.get(key)
                vb = agg_b.get(key)
                if va is None or vb is None:
                    continue
                try:
                    dimensions.append(
                        self._create_dimension(label, float(va), float(vb), higher_better=True)
                    )
                except (TypeError, ValueError):
                    continue

        return dimensions
        
    def _create_dimension(
        self,
        name: str,
        value_a: float,
        value_b: float,
        higher_better: bool
    ) -> ComparisonDimension:
        """Create a single comparison dimension"""
        difference = value_b - value_a
        
        if value_a != 0:
            percent_change = (difference / abs(value_a)) * 100
        elif value_b != 0:
            percent_change = 100.0
        else:
            percent_change = 0.0
            
        # Determine better model
        if higher_better:
            better = "model_b" if value_b > value_a else "model_a" if value_a > value_b else "tie"
        else:
            better = "model_a" if value_a < value_b else "model_b" if value_b < value_a else "tie"
            
        # Determine significance
        abs_pct_change = abs(percent_change) / 100
        if abs_pct_change >= self.thresholds['high']:
            significance = 'high'
        elif abs_pct_change >= self.thresholds['medium']:
            significance = 'medium'
        elif abs_pct_change >= self.thresholds['low']:
            significance = 'low'
        else:
            significance = 'negligible'
            
        return ComparisonDimension(
            dimension=name,
            model_a_value=value_a,
            model_b_value=value_b,
            difference=difference,
            percent_change=percent_change,
            better_model=better,
            significance=significance
        )
        
    def _generate_summary(
        self,
        dimensions: List[ComparisonDimension],
        model_a: str,
        model_b: str
    ) -> Dict[str, Any]:
        """Generate summary statistics from dimensions"""
        wins_a = sum(1 for d in dimensions if d.better_model == "model_a" and d.significance != 'negligible')
        wins_b = sum(1 for d in dimensions if d.better_model == "model_b" and d.significance != 'negligible')
        ties = len(dimensions) - wins_a - wins_b
        
        high_impact = [d for d in dimensions if d.significance == 'high']
        
        return {
            'total_dimensions': len(dimensions),
            f'{model_a}_wins': wins_a,
            f'{model_b}_wins': wins_b,
            'ties': ties,
            'high_impact_dimensions': [d.dimension for d in high_impact],
            'overall_winner': model_a if wins_a > wins_b else model_b if wins_b > wins_a else 'tie'
        }
        
    def _generate_recommendations(
        self,
        dimensions: List[ComparisonDimension],
        result_a: EvaluationResult,
        result_b: EvaluationResult,
        model_a: str,
        model_b: str
    ) -> List[str]:
        """Generate actionable recommendations from comparison"""
        recommendations = []
        
        # Check accuracy improvements
        accuracy_dims = [d for d in dimensions if 'accuracy' in d.dimension.lower() or 'f1' in d.dimension.lower()]
        for dim in accuracy_dims:
            if dim.significance in ['high', 'medium'] and dim.better_model == 'model_b':
                recommendations.append(
                    f"Consider migrating to {model_b} for improved {dim.dimension} "
                    f"({dim.percent_change:+.1f}% improvement)"
                )
                
        # Check latency concerns
        latency_dims = [d for d in dimensions if 'latency' in d.dimension.lower()]
        for dim in latency_dims:
            if dim.significance in ['high', 'medium'] and dim.better_model == 'model_a':
                recommendations.append(
                    f"Note: {model_b} shows increased {dim.dimension} "
                    f"({abs(dim.percent_change):.1f}% slower). Consider caching strategies."
                )
                
        # Check consistency
        consistency_dims = [d for d in dimensions if 'reproducibility' in d.dimension.lower() or 'consistency' in d.dimension.lower()]
        for dim in consistency_dims:
            if dim.significance in ['high', 'medium']:
                better = model_b if dim.better_model == 'model_b' else model_a
                recommendations.append(
                    f"{better} shows better {dim.dimension} - important for production stability"
                )
                
        # General migration recommendation
        if not recommendations:
            recommendations.append(
                "Models show similar performance. Consider cost and feature requirements for decision."
            )

        # Foundry-specific recommendations
        foundry_dims = [d for d in dimensions if d.dimension.endswith('(Foundry)')]
        high_impact_foundry = [d for d in foundry_dims if d.significance in ['high', 'medium']]
        for d in high_impact_foundry[:2]:
            if d.better_model == 'tie':
                continue
            better = model_b if d.better_model == 'model_b' else model_a
            metric_name = d.dimension.replace(' (Foundry)', '')
            recommendations.append(
                f"Foundry judges rate {better} higher on {metric_name} ({d.percent_change:+.1f}%)."
            )
            
        return recommendations
        
    def generate_detailed_report(
        self,
        comparison: ComparisonReport
    ) -> str:
        """
        Generate a detailed text report from comparison.
        
        Args:
            comparison: ComparisonReport to format
            
        Returns:
            Formatted report string
        """
        report = comparison.to_markdown()
        
        # Add raw result samples
        if comparison.raw_results_a and comparison.raw_results_b:
            report += "\n## Sample Result Comparisons\n\n"
            
            for i, (res_a, res_b) in enumerate(zip(
                comparison.raw_results_a[:3], 
                comparison.raw_results_b[:3]
            )):
                report += f"### Sample {i+1}\n\n"
                report += f"**Input:** {res_a.get('input', res_a.get('prompt', 'N/A'))[:100]}...\n\n"
                report += f"**{comparison.model_a}:** {res_a.get('predicted', res_a.get('response', {}))}\n\n"
                report += f"**{comparison.model_b}:** {res_b.get('predicted', res_b.get('response', {}))}\n\n"
                
        return report


# Example usage
if __name__ == "__main__":
    print("Model Comparator Module")
    print("=" * 50)
    print("\nUsage:")
    print("  comparator = ModelComparator(client)")
    print("  report = comparator.compare_models('gpt4', 'gpt5', 'classification')")
    print("  print(report.to_markdown())")
