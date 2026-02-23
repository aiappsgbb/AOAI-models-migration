"""
Evaluation Metrics Module
Provides comprehensive metrics calculation for model evaluation
"""

import json
import re
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from collections import Counter
import numpy as np

logger = logging.getLogger(__name__)
from sklearn.metrics import (
    accuracy_score, 
    precision_recall_fscore_support,
    confusion_matrix,
    cohen_kappa_score
)


@dataclass
class ClassificationMetrics:
    """Metrics for classification task evaluation"""
    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    kappa: float = 0.0
    category_accuracy: Dict[str, float] = field(default_factory=dict)
    confusion_matrix: Optional[np.ndarray] = None
    confusion_matrix_labels: List[str] = field(default_factory=list)
    # New: sub-field accuracy
    subcategory_accuracy: float = 0.0
    priority_accuracy: float = 0.0
    sentiment_accuracy: float = 0.0
    # New: confidence calibration
    avg_confidence: float = 0.0
    confidence_calibration: List[Dict] = field(default_factory=list)  # [{bin, accuracy, confidence, count}]
    
    def to_dict(self) -> Dict:
        cm_serialised = None
        if self.confusion_matrix is not None:
            cm_serialised = self.confusion_matrix.tolist() if hasattr(self.confusion_matrix, 'tolist') else self.confusion_matrix
        return {
            'accuracy': self.accuracy,
            'precision': self.precision,
            'recall': self.recall,
            'f1_score': self.f1_score,
            'kappa': self.kappa,
            'category_accuracy': self.category_accuracy,
            'confusion_matrix': cm_serialised,
            'confusion_matrix_labels': self.confusion_matrix_labels,
            'subcategory_accuracy': self.subcategory_accuracy,
            'priority_accuracy': self.priority_accuracy,
            'sentiment_accuracy': self.sentiment_accuracy,
            'avg_confidence': self.avg_confidence,
            'confidence_calibration': self.confidence_calibration,
        }


@dataclass
class ConsistencyMetrics:
    """Metrics for response consistency evaluation"""
    reproducibility_score: float = 0.0  # Same response across runs (0-1)
    semantic_similarity: float = 0.0    # Semantic similarity of variations
    format_consistency: float = 0.0     # Consistent output format
    variance_coefficient: float = 0.0   # Variance in key outputs
    
    def to_dict(self) -> Dict:
        return {
            'reproducibility_score': self.reproducibility_score,
            'semantic_similarity': self.semantic_similarity,
            'format_consistency': self.format_consistency,
            'variance_coefficient': self.variance_coefficient
        }


@dataclass
class LatencyMetrics:
    """Metrics for latency/performance evaluation"""
    mean_latency: float = 0.0
    median_latency: float = 0.0
    p95_latency: float = 0.0
    p99_latency: float = 0.0
    min_latency: float = 0.0
    max_latency: float = 0.0
    std_latency: float = 0.0
    mean_ttft: float = 0.0  # Time to first token
    # New: cost & token analytics
    tokens_per_second: float = 0.0
    cache_hit_rate: float = 0.0        # cached_tokens / prompt_tokens * 100
    reasoning_token_pct: float = 0.0   # reasoning_tokens / completion_tokens * 100
    cost_per_request: float = 0.0      # USD estimate per request
    total_cost: float = 0.0            # Total cost across all requests
    avg_prompt_tokens: float = 0.0
    avg_completion_tokens: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            'mean_latency': self.mean_latency,
            'median_latency': self.median_latency,
            'p95_latency': self.p95_latency,
            'p99_latency': self.p99_latency,
            'min_latency': self.min_latency,
            'max_latency': self.max_latency,
            'std_latency': self.std_latency,
            'mean_ttft': self.mean_ttft,
            'tokens_per_second': self.tokens_per_second,
            'cache_hit_rate': self.cache_hit_rate,
            'reasoning_token_pct': self.reasoning_token_pct,
            'cost_per_request': self.cost_per_request,
            'total_cost': self.total_cost,
            'avg_prompt_tokens': self.avg_prompt_tokens,
            'avg_completion_tokens': self.avg_completion_tokens,
        }


@dataclass
class QualityMetrics:
    """Metrics for response quality evaluation"""
    format_compliance: float = 0.0      # Follows expected format
    instruction_following: float = 0.0   # Follows all instructions
    completeness: float = 0.0           # All required elements present
    relevance: float = 0.0              # Response relevant to query
    entity_extraction_accuracy: float = 0.0
    follow_up_quality: float = 0.0      # Quality of follow-up questions
    # Dialog-specific metrics
    rule_compliance: float = 0.0        # How well follow-up rules are respected
    empathy_score: float = 0.0          # Empathetic tone detection
    optimal_similarity: float = 0.0     # Similarity to the gold-standard optimal follow-up
    resolution_efficiency: float = 0.0  # Questions asked vs expected resolution turns
    question_count_avg: float = 0.0     # Average number of follow-up questions generated
    
    def to_dict(self) -> Dict:
        return {
            'format_compliance': self.format_compliance,
            'instruction_following': self.instruction_following,
            'completeness': self.completeness,
            'relevance': self.relevance,
            'entity_extraction_accuracy': self.entity_extraction_accuracy,
            'follow_up_quality': self.follow_up_quality,
            'rule_compliance': self.rule_compliance,
            'empathy_score': self.empathy_score,
            'optimal_similarity': self.optimal_similarity,
            'resolution_efficiency': self.resolution_efficiency,
            'question_count_avg': self.question_count_avg,
        }


class MetricsCalculator:
    """Comprehensive metrics calculator for model evaluation."""

    def __init__(self):
        self._category_labels = []

    @staticmethod
    def _normalise_category(value) -> str:
        """Normalise a category value for comparison."""
        if not isinstance(value, str):
            # Handle dict/list/None that may leak from parsed JSON
            if isinstance(value, dict):
                # Try common key names for category
                value = (value.get('primary_category') or value.get('category')
                         or value.get('name') or str(value))
            elif value is None:
                return 'unknown'
            else:
                value = str(value)
        v = value.strip().lower()
        v = v.replace(' ', '_').replace('-', '_')
        while '__' in v:
            v = v.replace('__', '_')
        return v.strip('_') or 'unknown'

    def calculate_classification_metrics(
        self,
        predictions: List[Dict],
        ground_truth: List[Dict]
    ) -> ClassificationMetrics:
        """
        Calculate classification metrics comparing predictions to ground truth.
        
        Args:
            predictions: List of prediction dicts with 'category' key
            ground_truth: List of ground truth dicts with 'expected_category' key
            
        Returns:
            ClassificationMetrics object
        """
        y_pred = [self._normalise_category(p.get('category', 'unknown')) for p in predictions]
        y_true = [self._normalise_category(g.get('expected_category', g.get('category', 'unknown'))) for g in ground_truth]
        
        # Log per-scenario comparison for debugging
        for i, (pred, true) in enumerate(zip(y_pred, y_true)):
            match = 'OK' if pred == true else 'FAIL'
            logger.info(f"  [{match}] Scenario {i+1}: predicted='{pred}' expected='{true}'")
        
        matches = sum(1 for p, t in zip(y_pred, y_true) if p == t)
        logger.info(f"Classification results: {matches}/{len(y_pred)} correct ({matches/len(y_pred)*100:.1f}% accuracy)")
        logger.info(f"Unique predicted categories: {sorted(set(y_pred))}")
        logger.info(f"Unique expected categories:  {sorted(set(y_true))}")
        
        # Get unique labels
        labels = sorted(list(set(y_true + y_pred)))
        
        # Calculate basic metrics
        accuracy = accuracy_score(y_true, y_pred)
        precision, recall, f1, _ = precision_recall_fscore_support(
            y_true, y_pred, average='weighted', zero_division=0
        )
        
        # Cohen's Kappa
        try:
            kappa = cohen_kappa_score(y_true, y_pred)
        except (ValueError, TypeError):
            kappa = 0.0
            
        # Per-category accuracy
        category_accuracy = {}
        for label in labels:
            mask = [t == label for t in y_true]
            if sum(mask) > 0:
                correct = sum(1 for i, m in enumerate(mask) if m and y_pred[i] == label)
                category_accuracy[label] = correct / sum(mask)
                
        # Confusion matrix
        cm = confusion_matrix(y_true, y_pred, labels=labels)
        
        # --- NEW: Subcategory accuracy ---
        sub_matches = 0
        sub_total = 0
        for p, g in zip(predictions, ground_truth):
            exp_sub = g.get('expected_subcategory', '')
            pred_sub = p.get('subcategory', '')
            if exp_sub:  # Only count when ground truth exists
                sub_total += 1
                if self._normalise_category(pred_sub) == self._normalise_category(exp_sub):
                    sub_matches += 1
        subcategory_accuracy = sub_matches / sub_total if sub_total > 0 else 0.0
        
        # --- NEW: Priority accuracy ---
        pri_matches = 0
        pri_total = 0
        for p, g in zip(predictions, ground_truth):
            exp_pri = g.get('expected_priority', '')
            pred_pri = p.get('priority', '')
            if exp_pri:
                pri_total += 1
                if str(pred_pri).strip().lower() == str(exp_pri).strip().lower():
                    pri_matches += 1
        priority_accuracy = pri_matches / pri_total if pri_total > 0 else 0.0
        
        # --- NEW: Sentiment accuracy ---
        sent_matches = 0
        sent_total = 0
        for p, g in zip(predictions, ground_truth):
            exp_sent = g.get('expected_sentiment', '')
            pred_sent = p.get('sentiment', '')
            if exp_sent:
                sent_total += 1
                if str(pred_sent).strip().lower() == str(exp_sent).strip().lower():
                    sent_matches += 1
        sentiment_accuracy = sent_matches / sent_total if sent_total > 0 else 0.0
        
        # --- NEW: Confidence calibration ---
        confidences = [float(p.get('confidence', 0.0)) for p in predictions]
        avg_confidence = np.mean(confidences) if confidences else 0.0
        
        calibration_bins = self._calculate_calibration(
            confidences, y_pred, y_true
        )
        
        logger.info(
            f"Sub-field accuracy: subcategory={subcategory_accuracy:.1%} "
            f"({sub_matches}/{sub_total}), priority={priority_accuracy:.1%} "
            f"({pri_matches}/{pri_total}), sentiment={sentiment_accuracy:.1%} "
            f"({sent_matches}/{sent_total}), avg_confidence={avg_confidence:.3f}"
        )
        
        return ClassificationMetrics(
            accuracy=accuracy,
            precision=precision,
            recall=recall,
            f1_score=f1,
            kappa=kappa,
            category_accuracy=category_accuracy,
            confusion_matrix=cm,
            confusion_matrix_labels=labels,
            subcategory_accuracy=subcategory_accuracy,
            priority_accuracy=priority_accuracy,
            sentiment_accuracy=sentiment_accuracy,
            avg_confidence=avg_confidence,
            confidence_calibration=calibration_bins,
        )
        
    def calculate_consistency_metrics(
        self,
        responses: List[List[str]]
    ) -> ConsistencyMetrics:
        """
        Calculate consistency metrics from multiple runs of the same prompts.
        
        Args:
            responses: List of response lists, where each inner list contains
                      responses for the same prompt across multiple runs
                      
        Returns:
            ConsistencyMetrics object
        """
        if not responses or not responses[0]:
            return ConsistencyMetrics()
            
        reproducibility_scores = []
        format_scores = []
        
        for response_set in responses:
            if len(response_set) < 2:
                continue
                
            # Exact match reproducibility
            unique_responses = len(set(response_set))
            repro = 1.0 if unique_responses == 1 else 1.0 / unique_responses
            reproducibility_scores.append(repro)
            
            # Format consistency (check if all are valid JSON or all are not)
            json_valid = [self._is_valid_json(r) for r in response_set]
            format_consistent = 1.0 if len(set(json_valid)) == 1 else 0.5
            format_scores.append(format_consistent)
            
        return ConsistencyMetrics(
            reproducibility_score=np.mean(reproducibility_scores) if reproducibility_scores else 0.0,
            format_consistency=np.mean(format_scores) if format_scores else 0.0,
            variance_coefficient=1.0 - (np.mean(reproducibility_scores) if reproducibility_scores else 0.0)
        )
        
    def calculate_latency_metrics(
        self,
        latencies: List[float],
        ttft_values: Optional[List[float]] = None,
        token_data: Optional[List[Dict]] = None,
        model_name: Optional[str] = None
    ) -> LatencyMetrics:
        """
        Calculate latency metrics from timing measurements.
        
        Args:
            latencies: List of total latency values in seconds
            ttft_values: Optional list of time-to-first-token values
            token_data: Optional per-request token data [{prompt_tokens, completion_tokens,
                        cached_tokens, reasoning_tokens}]
            model_name: Model name for cost estimation ('gpt4' or 'gpt5')
            
        Returns:
            LatencyMetrics object
        """
        if not latencies:
            return LatencyMetrics()
            
        latencies_arr = np.array(latencies)
        
        # Token analytics
        tokens_per_second = 0.0
        cache_hit_rate = 0.0
        reasoning_token_pct = 0.0
        cost_per_request = 0.0
        total_cost = 0.0
        avg_prompt_tokens = 0.0
        avg_completion_tokens = 0.0
        
        if token_data:
            total_prompt = sum(d.get('prompt_tokens', 0) for d in token_data)
            total_completion = sum(d.get('completion_tokens', 0) for d in token_data)
            total_cached = sum(d.get('cached_tokens', 0) for d in token_data)
            total_reasoning = sum(d.get('reasoning_tokens', 0) for d in token_data)
            total_all = total_prompt + total_completion
            total_time = sum(latencies)
            
            tokens_per_second = total_all / total_time if total_time > 0 else 0.0
            cache_hit_rate = (total_cached / total_prompt * 100) if total_prompt > 0 else 0.0
            reasoning_token_pct = (total_reasoning / total_completion * 100) if total_completion > 0 else 0.0
            avg_prompt_tokens = total_prompt / len(token_data)
            avg_completion_tokens = total_completion / len(token_data)
            
            # Cost estimation (per 1K tokens, from model_params.yaml)
            cost_rates = {
                'gpt4': {'input': 0.0025, 'output': 0.01, 'cached_input': 0.00125},
                'gpt5': {'input': 0.005, 'output': 0.02, 'cached_input': 0.0025, 'reasoning': 0.015},
            }
            rates = cost_rates.get(model_name, cost_rates.get('gpt4', {}))
            for d in token_data:
                pt = d.get('prompt_tokens', 0)
                ct = d.get('cached_tokens', 0)
                comp = d.get('completion_tokens', 0)
                rt = d.get('reasoning_tokens', 0)
                uncached_prompt = pt - ct
                req_cost = (
                    (uncached_prompt / 1000) * rates.get('input', 0.0025)
                    + (ct / 1000) * rates.get('cached_input', 0.00125)
                    + ((comp - rt) / 1000) * rates.get('output', 0.01)
                    + (rt / 1000) * rates.get('reasoning', rates.get('output', 0.01))
                )
                total_cost += req_cost
            cost_per_request = total_cost / len(token_data) if token_data else 0.0
        
        return LatencyMetrics(
            mean_latency=float(np.mean(latencies_arr)),
            median_latency=float(np.median(latencies_arr)),
            p95_latency=float(np.percentile(latencies_arr, 95)),
            p99_latency=float(np.percentile(latencies_arr, 99)),
            min_latency=float(np.min(latencies_arr)),
            max_latency=float(np.max(latencies_arr)),
            std_latency=float(np.std(latencies_arr)),
            mean_ttft=float(np.mean(ttft_values)) if ttft_values else 0.0,
            tokens_per_second=tokens_per_second,
            cache_hit_rate=cache_hit_rate,
            reasoning_token_pct=reasoning_token_pct,
            cost_per_request=cost_per_request,
            total_cost=total_cost,
            avg_prompt_tokens=avg_prompt_tokens,
            avg_completion_tokens=avg_completion_tokens,
        )
        
    def calculate_quality_metrics(
        self,
        responses: List[str],
        expected_format: str = "json",
        required_fields: Optional[List[str]] = None
    ) -> QualityMetrics:
        """
        Calculate response quality metrics.
        
        Args:
            responses: List of model responses
            expected_format: Expected format ('json', 'text', 'structured')
            required_fields: Required fields in JSON responses
            
        Returns:
            QualityMetrics object
        """
        if not responses:
            return QualityMetrics()
            
        format_scores = []
        completeness_scores = []
        
        required_fields = required_fields or ['classification', 'priority', 'sentiment']
        
        for response in responses:
            # Format compliance
            if expected_format == "json":
                is_valid, parsed = self._is_valid_json(response, return_parsed=True)
                format_scores.append(1.0 if is_valid else 0.0)
                
                # Completeness
                if parsed:
                    present = sum(1 for f in required_fields if f in parsed)
                    completeness_scores.append(present / len(required_fields))
                else:
                    completeness_scores.append(0.0)
            else:
                format_scores.append(1.0)  # Default to valid for text
                completeness_scores.append(1.0)
                
        return QualityMetrics(
            format_compliance=np.mean(format_scores),
            completeness=np.mean(completeness_scores),
            instruction_following=np.mean(format_scores) * np.mean(completeness_scores)
        )
        
    def calculate_follow_up_quality(
        self,
        generated_questions: List[List[str]],
        expected_questions: List[List[str]]
    ) -> float:
        """
        Calculate quality of generated follow-up questions.
        
        Args:
            generated_questions: List of generated question lists
            expected_questions: List of expected question lists
            
        Returns:
            Quality score (0-1)
        """
        if not generated_questions:
            return 0.0
            
        scores = []
        for gen, exp in zip(generated_questions, expected_questions):
            if not exp:
                scores.append(1.0 if not gen else 0.5)
                continue
                
            if not gen:
                scores.append(0.0)
                continue
                
            # Simple keyword overlap scoring
            exp_keywords = set()
            for q in exp:
                exp_keywords.update(q.lower().split())
                
            gen_keywords = set()
            for q in gen:
                gen_keywords.update(q.lower().split())
                
            overlap = len(exp_keywords & gen_keywords) / max(len(exp_keywords), 1)
            scores.append(min(overlap * 1.5, 1.0))  # Scale up slightly
            
        return np.mean(scores)

    def calculate_context_gap_coverage(
        self,
        responses: List[str],
        context_gaps: List[List[str]]
    ) -> float:
        """Calculate how well the agent's responses address the identified context gaps.

        For each scenario the context_gaps list contains short descriptions of
        information that should be requested (e.g. ["specific_issue",
        "amount_in_question", "billing_period"]).  We check whether the
        response text mentions keywords related to each gap.

        Args:
            responses: List of agent response texts
            context_gaps: List of gap lists (parallel to responses)

        Returns:
            Coverage score (0-1)
        """
        if not responses or not context_gaps:
            return 0.0

        scores: List[float] = []
        for response, gaps in zip(responses, context_gaps):
            if not gaps:
                scores.append(1.0)
                continue
            resp_lower = response.lower()
            hits = 0
            for gap in gaps:
                # Expand underscore-separated gap names into individual keywords
                keywords = gap.lower().replace('_', ' ').split()
                # A gap is "covered" if at least one of its keywords appears
                if any(kw in resp_lower for kw in keywords):
                    hits += 1
            scores.append(hits / len(gaps))

        return float(np.mean(scores))

    def calculate_rule_compliance(
        self,
        responses: List[str],
        rules_per_scenario: List[List[str]]
    ) -> float:
        """Score how well responses adhere to the follow-up rules.

        Each rule is a short instruction like 'Begin with empathy' or
        'Request only the minimum necessary details'.  We check for
        keyword-based evidence that the rule was followed.
        """
        if not responses or not rules_per_scenario:
            return 0.0

        scores: List[float] = []
        for resp, rules in zip(responses, rules_per_scenario):
            if not rules:
                scores.append(1.0)
                continue
            resp_lower = resp.lower()
            hits = 0
            for rule in rules:
                # Extract meaningful keywords from the rule (>3 chars)
                keywords = [w for w in rule.lower().replace(',', ' ').replace('.', ' ').split() if len(w) > 3]
                # Rule is considered followed if ≥40% of its keywords appear
                if keywords:
                    present = sum(1 for kw in keywords if kw in resp_lower)
                    if present / len(keywords) >= 0.35:
                        hits += 1
            scores.append(hits / len(rules))
        return float(np.mean(scores))

    def calculate_empathy_score(
        self,
        responses: List[str]
    ) -> float:
        """Detect empathetic/professional tone in dialog responses.

        Looks for empathy markers in the first ~200 chars of each response.
        """
        if not responses:
            return 0.0

        empathy_markers = [
            'sorry', 'apologize', 'understand', 'frustrat', 'inconvenien',
            'happy to help', 'glad to', 'appreciate', 'concern', 'help you',
            'assist', 'thank', 'i understand', 'let me help', 'no worries',
            'right away', 'i can see', 'that must be',
        ]
        scores: List[float] = []
        for resp in responses:
            opener = resp[:250].lower()
            hits = sum(1 for m in empathy_markers if m in opener)
            # Score: 0 markers = 0, 1 = 0.5, 2+ = 1.0
            scores.append(min(hits / 2.0, 1.0))
        return float(np.mean(scores))

    def calculate_optimal_similarity(
        self,
        responses: List[str],
        optimal_follow_ups: List[str]
    ) -> float:
        """Compare generated response to the gold-standard optimal follow-up.

        Uses word-level Jaccard similarity.
        """
        if not responses or not optimal_follow_ups:
            return 0.0

        scores: List[float] = []
        for resp, opt in zip(responses, optimal_follow_ups):
            if not opt:
                scores.append(0.5)
                continue
            resp_words = set(resp.lower().split())
            opt_words = set(opt.lower().split())
            if not opt_words:
                scores.append(0.5)
                continue
            intersection = resp_words & opt_words
            union = resp_words | opt_words
            jaccard = len(intersection) / len(union) if union else 0.0
            # Scale: Jaccard of 0.3+ is quite good for open text
            scores.append(min(jaccard * 2.5, 1.0))
        return float(np.mean(scores))

    def calculate_resolution_efficiency(
        self,
        question_counts: List[int],
        expected_turns: List[int]
    ) -> float:
        """Measure whether the agent asks an efficient number of questions.

        Score is highest when question count closely matches expected_resolution_turns.
        Over-asking or under-asking reduces the score.
        """
        if not question_counts or not expected_turns:
            return 0.0

        scores: List[float] = []
        for asked, expected in zip(question_counts, expected_turns):
            if expected <= 0:
                scores.append(1.0 if asked <= 3 else 0.5)
                continue
            ratio = asked / expected
            # Perfect = 1.0, too many or too few penalised
            if 0.8 <= ratio <= 1.5:
                scores.append(1.0)
            elif 0.5 <= ratio <= 2.0:
                scores.append(0.7)
            else:
                scores.append(0.3)
        return float(np.mean(scores))

    def _calculate_calibration(
        self,
        confidences: List[float],
        y_pred: List[str],
        y_true: List[str],
        n_bins: int = 5
    ) -> List[Dict]:
        """Calculate confidence calibration bins.
        
        Groups predictions into bins by confidence level, then computes the
        actual accuracy in each bin.  A well-calibrated model shows accuracy
        close to its stated confidence.
        
        Returns list of dicts: [{bin, accuracy, confidence, count}]
        """
        if not confidences or len(confidences) != len(y_pred):
            return []

        bins: List[Dict] = []
        edges = np.linspace(0, 1, n_bins + 1)
        for i in range(n_bins):
            lo, hi = edges[i], edges[i + 1]
            indices = [
                j for j, c in enumerate(confidences)
                if (lo <= c < hi) or (i == n_bins - 1 and c == hi)
            ]
            if not indices:
                continue
            correct = sum(1 for j in indices if y_pred[j] == y_true[j])
            bins.append({
                'bin': f"{lo:.1f}-{hi:.1f}",
                'accuracy': correct / len(indices),
                'confidence': float(np.mean([confidences[j] for j in indices])),
                'count': len(indices),
            })
        return bins

    @staticmethod
    def calculate_statistical_significance(
        results_a: List[Dict],
        results_b: List[Dict],
    ) -> Dict[str, Any]:
        """Compute statistical significance tests for two sets of evaluation results.
        
        - McNemar test for classification accuracy (paired nominal outcomes).
        - Paired t-test for latency.
        
        Returns dict with p-values and whether differences are significant.
        """
        from scipy import stats as sp_stats

        sig: Dict[str, Any] = {}

        # --- McNemar for classification accuracy ---
        if results_a and results_b and len(results_a) == len(results_b):
            # Build contingency: a_correct & b_wrong, a_wrong & b_correct
            a_correct_b_wrong = 0
            a_wrong_b_correct = 0
            for ra, rb in zip(results_a, results_b):
                ea = ra.get('expected', {}).get('category', '')
                pa = ra.get('predicted', {}).get('category', '')
                pb = rb.get('predicted', {}).get('category', '')
                a_ok = (pa == ea)
                b_ok = (pb == ea)
                if a_ok and not b_ok:
                    a_correct_b_wrong += 1
                elif not a_ok and b_ok:
                    a_wrong_b_correct += 1
            n = a_correct_b_wrong + a_wrong_b_correct
            if n > 0:
                # McNemar chi-squared (with continuity correction)
                chi2 = (abs(a_correct_b_wrong - a_wrong_b_correct) - 1) ** 2 / n
                p_value = 1 - sp_stats.chi2.cdf(chi2, df=1)
                sig['mcnemar'] = {
                    'chi2': float(chi2),
                    'p_value': float(p_value),
                    'significant': bool(p_value < 0.05),
                    'a_correct_b_wrong': int(a_correct_b_wrong),
                    'a_wrong_b_correct': int(a_wrong_b_correct),
                }
            else:
                sig['mcnemar'] = {'chi2': 0, 'p_value': 1.0, 'significant': False,
                                  'a_correct_b_wrong': 0, 'a_wrong_b_correct': 0}

        # --- Paired t-test for latency ---
        lat_a = [r.get('latency', 0) for r in results_a if r.get('latency') is not None]
        lat_b = [r.get('latency', 0) for r in results_b if r.get('latency') is not None]
        if lat_a and lat_b and len(lat_a) == len(lat_b):
            t_stat, p_value = sp_stats.ttest_rel(lat_a, lat_b)
            sig['latency_ttest'] = {
                't_statistic': float(t_stat),
                'p_value': float(p_value),
                'significant': bool(p_value < 0.05),
            }
        else:
            sig['latency_ttest'] = {'t_statistic': 0, 'p_value': 1.0, 'significant': False}

        return sig

    def _is_valid_json(self, text, return_parsed: bool = False) -> Any:
        """Check if text is valid JSON.  Accepts str or already-parsed dict/list."""
        try:
            # Already parsed (SDK v2 json_object mode can return dict)
            if isinstance(text, (dict, list)):
                return (True, text) if return_parsed else True

            if not isinstance(text, str):
                return (False, None) if return_parsed else False

            # Try to find JSON in the text
            text = text.strip()
            
            # Handle markdown code blocks
            if text.startswith('```'):
                match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
                if match:
                    text = match.group(1)
                    
            parsed = json.loads(text)
            return (True, parsed) if return_parsed else True
        except (json.JSONDecodeError, TypeError, AttributeError):
            return (False, None) if return_parsed else False
            
    def extract_classification_from_response(self, response) -> Dict:
        """
        Extract classification data from model response.

        Handles multiple JSON layouts produced by different prompts:
        - GPT-5 style:  {"classification": {"category": "…", …}, …}
        - GPT-4 style:  {"primary_category": "…", "subcategory": "…", …}
        - Flat style:   {"category": "…", "subcategory": "…", …}

        Args:
            response: Raw model response (str or dict)

        Returns:
            Extracted classification dict
        """
        # SDK v2 may already hand us a dict — normalise to str for
        # the raw_response field, but keep the parsed object.
        if isinstance(response, dict):
            is_valid, parsed = True, response
        else:
            is_valid, parsed = self._is_valid_json(response, return_parsed=True)

        if is_valid and parsed:
            # --- Resolve the nested "classification" block (GPT-5) -----------
            classification = parsed.get('classification', {})

            # --- Category: try every known key name -------------------------
            # GPT-5 nested:  classification.primary_category
            # GPT-5 flat:    primary_category
            # GPT-4 Telco:   primary_category_code
            # GPT-4 flat:    category
            raw_cat = (
                classification.get('primary_category')
                or classification.get('category')
                or parsed.get('primary_category')
                or parsed.get('primary_category_code')
                or parsed.get('category')
                or 'unknown'
            )
            # Unwrap nested dicts — some models return {"category": {"name": "..."}}
            if isinstance(raw_cat, dict):
                raw_cat = (raw_cat.get('name') or raw_cat.get('code')
                           or raw_cat.get('primary_category') or str(raw_cat))

            # --- Subcategory ------------------------------------------------
            raw_sub = (
                classification.get('primary_subcategory')
                or classification.get('subcategory')
                or parsed.get('primary_subcategory')
                or parsed.get('subcategory_code')
                or parsed.get('subcategory')
                or ''
            )

            # --- Priority (some prompts use "priority_level") ---------------
            raw_pri = (
                classification.get('priority_level')
                or classification.get('priority')
                or parsed.get('priority_level')
                or parsed.get('priority')
                or 'medium'
            )

            # --- Sentiment --------------------------------------------------
            raw_sent = (
                classification.get('overall_sentiment')
                or classification.get('sentiment')
                or parsed.get('overall_sentiment')
                or parsed.get('sentiment')
                or 'neutral'
            )

            # --- Confidence (some prompts use "confidence_score") -----------
            raw_conf = (
                classification.get('confidence')
                or classification.get('confidence_score')
                or parsed.get('confidence')
                or parsed.get('confidence_score')
                or 0.0
            )

            normalised_cat = self._normalise_category(raw_cat)
            logger.debug(
                f"Extracted classification: raw_cat='{raw_cat}' -> "
                f"normalised='{normalised_cat}', subcategory='{raw_sub}', "
                f"priority='{raw_pri}', sentiment='{raw_sent}', "
                f"confidence={raw_conf}, "
                f"had_nested_classification={'classification' in parsed}"
            )

            return {
                'category': normalised_cat,
                'subcategory': raw_sub,
                'confidence': raw_conf,
                'priority': raw_pri,
                'sentiment': raw_sent,
                'raw_response': response
            }

        logger.warning(
            f"Failed to parse classification response as JSON. "
            f"Response (first 200 chars): {str(response)[:200]}"
        )
        return {
            'category': 'unknown',
            'subcategory': '',
            'confidence': 0.0,
            'priority': 'medium',
            'sentiment': 'neutral',
            'raw_response': response,
            'parse_error': True
        }


# Example usage
if __name__ == "__main__":
    calc = MetricsCalculator()
    
    # Sample classification data
    predictions = [
        {'category': 'billing_inquiry'},
        {'category': 'technical_support'},
        {'category': 'billing_inquiry'},
        {'category': 'sales'},
    ]
    
    ground_truth = [
        {'expected_category': 'billing_inquiry'},
        {'expected_category': 'technical_support'},
        {'expected_category': 'sales'},  # Mismatch
        {'expected_category': 'sales'},
    ]
    
    class_metrics = calc.calculate_classification_metrics(predictions, ground_truth)
    print("Classification Metrics:")
    print(f"  Accuracy: {class_metrics.accuracy:.2%}")
    print(f"  F1 Score: {class_metrics.f1_score:.2%}")
    
    # Sample latency data
    latencies = [0.5, 0.6, 0.55, 0.7, 0.45, 0.8, 0.52]
    latency_metrics = calc.calculate_latency_metrics(latencies)
    print(f"\nLatency Metrics:")
    print(f"  Mean: {latency_metrics.mean_latency:.3f}s")
    print(f"  P95: {latency_metrics.p95_latency:.3f}s")
