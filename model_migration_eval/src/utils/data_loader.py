"""
Data Loader Utility
Loads synthetic evaluation data and test scenarios
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Iterator
from dataclasses import dataclass


@dataclass
class ClassificationScenario:
    """Represents a classification test scenario"""
    id: str
    scenario: str
    customer_input: str
    expected_category: str
    expected_subcategory: str
    expected_priority: str
    expected_sentiment: str
    context: Dict[str, Any]
    follow_up_questions_expected: List[str]


@dataclass  
class DialogScenario:
    """Represents a dialog/follow-up test scenario"""
    id: str
    scenario: str
    conversation: List[Dict[str, str]]
    context_gaps: List[str]
    optimal_follow_up: str
    follow_up_rules: List[str]
    expected_resolution_turns: int
    category: str


@dataclass
class GeneralTestCase:
    """Represents a general capability test case"""
    id: str
    test_type: str
    prompt: str
    complexity: str
    expected_output: Optional[Any] = None
    expected_behavior: Optional[str] = None
    conversation: Optional[List[Dict]] = None
    run_count: int = 1


class DataLoader:
    """
    Utility class for loading evaluation data.
    Provides structured access to classification, dialog, and general test scenarios.
    """
    
    def __init__(self, data_dir: str = "data/synthetic"):
        """
        Initialize the data loader.
        
        Args:
            data_dir: Base directory containing synthetic data
        """
        self.data_dir = Path(data_dir)
        self._cache: Dict[str, Any] = {}

    def clear_cache(self):
        """Clear the data cache so fresh files are loaded on next access."""
        self._cache.clear()
        
    def _load_json(self, file_path: Path) -> Any:
        """Load and cache JSON file"""
        key = str(file_path)
        if key not in self._cache:
            with open(file_path, 'r', encoding='utf-8') as f:
                self._cache[key] = json.load(f)
        return self._cache[key]
        
    def load_classification_scenarios(self) -> List[ClassificationScenario]:
        """
        Load TELCO classification test scenarios.
        
        Returns:
            List of ClassificationScenario objects
        """
        file_path = self.data_dir / "classification" / "classification_scenarios.json"
        raw_data = self._load_json(file_path)
        
        return [
            ClassificationScenario(
                id=item['id'],
                scenario=item['scenario'],
                customer_input=item['customer_input'],
                expected_category=item['expected_category'],
                expected_subcategory=item['expected_subcategory'],
                expected_priority=item['expected_priority'],
                expected_sentiment=item['expected_sentiment'],
                context=item.get('context', {}),
                follow_up_questions_expected=item.get('follow_up_questions_expected', [])
            )
            for item in raw_data
        ]
        
    def load_dialog_scenarios(self) -> List[DialogScenario]:
        """
        Load dialog/follow-up test scenarios.
        
        Returns:
            List of DialogScenario objects
        """
        file_path = self.data_dir / "dialog" / "follow_up_scenarios.json"
        raw_data = self._load_json(file_path)
        
        return [
            DialogScenario(
                id=item['id'],
                scenario=item['scenario'],
                conversation=item['conversation'],
                context_gaps=item['context_gaps'],
                optimal_follow_up=item['optimal_follow_up'],
                follow_up_rules=item['follow_up_rules'],
                expected_resolution_turns=item['expected_resolution_turns'],
                category=item['category']
            )
            for item in raw_data
        ]
        
    def load_general_tests(self) -> List[GeneralTestCase]:
        """
        Load general capability test cases.
        
        Returns:
            List of GeneralTestCase objects
        """
        file_path = self.data_dir / "general" / "capability_tests.json"
        raw_data = self._load_json(file_path)
        
        return [
            GeneralTestCase(
                id=item['id'],
                test_type=item['test_type'],
                prompt=item.get('prompt', ''),
                complexity=item['complexity'],
                expected_output=item.get('expected_output'),
                expected_behavior=item.get('expected_behavior'),
                conversation=item.get('conversation'),
                run_count=item.get('run_count', 1)
            )
            for item in raw_data
        ]
        
    def get_classification_by_category(self, category: str) -> List[ClassificationScenario]:
        """Get classification scenarios filtered by expected category"""
        all_scenarios = self.load_classification_scenarios()
        return [s for s in all_scenarios if s.expected_category == category]
        
    def get_classification_by_priority(self, priority: str) -> List[ClassificationScenario]:
        """Get classification scenarios filtered by expected priority"""
        all_scenarios = self.load_classification_scenarios()
        return [s for s in all_scenarios if s.expected_priority == priority]
        
    def get_dialog_by_category(self, category: str) -> List[DialogScenario]:
        """Get dialog scenarios filtered by category"""
        all_scenarios = self.load_dialog_scenarios()
        return [s for s in all_scenarios if s.category == category]
        
    def get_tests_by_type(self, test_type: str) -> List[GeneralTestCase]:
        """Get general tests filtered by test type"""
        all_tests = self.load_general_tests()
        return [t for t in all_tests if t.test_type == test_type]
        
    def get_tests_by_complexity(self, complexity: str) -> List[GeneralTestCase]:
        """Get general tests filtered by complexity"""
        all_tests = self.load_general_tests()
        return [t for t in all_tests if t.complexity == complexity]
        
    def iter_all_scenarios(self) -> Iterator[tuple]:
        """
        Iterate over all test scenarios with their type.
        
        Yields:
            Tuples of (scenario_type, scenario_object)
        """
        for scenario in self.load_classification_scenarios():
            yield ('classification', scenario)
            
        for scenario in self.load_dialog_scenarios():
            yield ('dialog', scenario)
            
        for test in self.load_general_tests():
            yield ('general', test)
            
    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics of available test data.
        
        Returns:
            Dictionary with counts and breakdowns
        """
        classification = self.load_classification_scenarios()
        dialog = self.load_dialog_scenarios()
        general = self.load_general_tests()
        
        return {
            'total_scenarios': len(classification) + len(dialog) + len(general),
            'classification': {
                'count': len(classification),
                'categories': list(set(s.expected_category for s in classification)),
                'priorities': list(set(s.expected_priority for s in classification))
            },
            'dialog': {
                'count': len(dialog),
                'categories': list(set(s.category for s in dialog))
            },
            'general': {
                'count': len(general),
                'test_types': list(set(t.test_type for t in general)),
                'complexity_levels': list(set(t.complexity for t in general))
            }
        }


# Example usage
if __name__ == "__main__":
    loader = DataLoader("data/synthetic")
    
    print("Data Loader Utility")
    print("=" * 50)
    
    summary = loader.get_summary()
    print(f"\nTotal scenarios: {summary['total_scenarios']}")
    print(f"\nClassification: {summary['classification']['count']} scenarios")
    print(f"  Categories: {summary['classification']['categories']}")
    print(f"\nDialog: {summary['dialog']['count']} scenarios")
    print(f"\nGeneral: {summary['general']['count']} tests")
    print(f"  Test types: {summary['general']['test_types']}")
