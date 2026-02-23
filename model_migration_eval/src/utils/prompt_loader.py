"""
Prompt Loader Utility
Loads and manages prompt templates for different models and use cases
"""

import os
import re
from pathlib import Path
from typing import Dict, Optional, Any
import yaml


class PromptLoader:
    """
    Utility class for loading and managing prompt templates.
    Supports variable substitution and model-specific prompt selection.
    """
    
    def __init__(self, prompts_dir: str = "prompts"):
        """
        Initialize the prompt loader.
        
        Args:
            prompts_dir: Base directory containing prompt files
        """
        self.prompts_dir = Path(prompts_dir)
        self._cache: Dict[str, str] = {}
        
    def load_prompt(
        self, 
        model: str, 
        prompt_type: str, 
        variables: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Load a prompt template for a specific model and type.
        
        Args:
            model: Model name (e.g., 'gpt4', 'gpt5')
            prompt_type: Type of prompt (e.g., 'classification_agent_system', 'dialog_agent_system')
            variables: Dictionary of variables to substitute in the prompt
            
        Returns:
            Loaded and processed prompt string
        """
        # Check cache first
        cache_key = f"{model}/{prompt_type}"
        if cache_key in self._cache and not variables:
            return self._cache[cache_key]
            
        # Construct file path
        file_path = self.prompts_dir / model / f"{prompt_type}.md"
        
        if not file_path.exists():
            # Try without model prefix (shared template)
            file_path = self.prompts_dir / "templates" / f"{prompt_type}.md"
            
        if not file_path.exists():
            raise FileNotFoundError(f"Prompt template not found: {file_path}")
            
        # Load content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Cache raw content
        self._cache[cache_key] = content
        
        # Substitute variables if provided
        if variables:
            content = self._substitute_variables(content, variables)
            
        return content
        
    def _substitute_variables(self, content: str, variables: Dict[str, Any]) -> str:
        """
        Substitute variables in prompt template.
        
        Supports formats:
        - {{variable_name}}
        - ${variable_name}
        - {variable_name}
        """
        for key, value in variables.items():
            # Handle different placeholder formats
            patterns = [
                f"{{{{{key}}}}}",  # {{variable}}
                f"${{{key}}}",     # ${variable}
                f"{{{key}}}"       # {variable}
            ]
            for pattern in patterns:
                content = content.replace(pattern, str(value))
                
        return content
        
    def load_classification_prompt(
        self, 
        model: str,
        customer_message: str,
        context: Optional[Dict] = None,
    ) -> list:
        """
        Load classification prompt with customer message.
        
        Args:
            model: Model name
            customer_message: The customer message to classify
            context: Optional context about the customer
            
        Returns:
            List of message dicts ready for API call
        """
        system_prompt = self.load_prompt(model, "classification_agent_system")
        
        user_content = f"Classify the following customer message:\n\n{customer_message}"
        
        if context:
            context_str = "\n".join([f"- {k}: {v}" for k, v in context.items()])
            user_content = f"Customer Context:\n{context_str}\n\n{user_content}"

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        
    def load_dialog_prompt(
        self,
        model: str,
        conversation: list,
        customer_context: Optional[Dict] = None
    ) -> list:
        """
        Load dialog prompt with conversation history.
        
        Args:
            model: Model name
            conversation: List of conversation turns
            customer_context: Optional customer context
            
        Returns:
            List of message dicts ready for API call
        """
        system_prompt = self.load_prompt(model, "dialog_agent_system")
        
        if customer_context:
            context_str = "\n".join([f"- {k}: {v}" for k, v in customer_context.items()])
            system_prompt += f"\n\n## Current Customer Context\n{context_str}"

        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history
        for turn in conversation:
            role = "user" if turn.get("role") == "customer" else "assistant"
            messages.append({
                "role": role,
                "content": turn.get("message", turn.get("content", ""))
            })
            
        return messages
        
    def list_available_prompts(self) -> Dict[str, list]:
        """
        List all available prompts organized by model.
        
        Returns:
            Dictionary mapping model names to list of available prompt types
        """
        result = {}
        # Directories that are not model prompt folders
        _skip_dirs = {'history', 'topics'}
        
        for model_dir in self.prompts_dir.iterdir():
            if model_dir.is_dir() and model_dir.name not in _skip_dirs:
                prompts = []
                for file in model_dir.glob("*.md"):
                    prompt_type = file.stem
                    prompts.append(prompt_type)
                if prompts:
                    result[model_dir.name] = sorted(prompts)
                    
        return result
        
    def compare_prompts(self, prompt_type: str, models: list = None) -> Dict[str, str]:
        """
        Load the same prompt type for multiple models for comparison.
        
        Args:
            prompt_type: Type of prompt to compare
            models: List of models to compare (default: ['gpt4', 'gpt5'])
            
        Returns:
            Dictionary mapping model names to prompt content
        """
        if models is None:
            models = ['gpt4', 'gpt5']
            
        result = {}
        for model in models:
            try:
                result[model] = self.load_prompt(model, prompt_type)
            except FileNotFoundError:
                result[model] = None
                
        return result


# Example usage
if __name__ == "__main__":
    loader = PromptLoader("prompts")
    
    print("Prompt Loader Utility")
    print("=" * 50)
    
    # List available prompts
    available = loader.list_available_prompts()
    print("\nAvailable prompts:")
    for model, prompts in available.items():
        print(f"\n{model}:")
        for p in prompts:
            print(f"  - {p}")
