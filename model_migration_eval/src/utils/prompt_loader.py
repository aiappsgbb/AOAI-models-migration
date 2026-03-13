"""
Prompt Loader Utility
Loads and manages prompt templates for different models and use cases
"""

import os
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Any
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
        # Cache stores (mtime, content) tuples keyed by "model/prompt_type".
        # On cache hit we stat() the file (~100× cheaper than open+read)
        # and only reload when mtime has changed.
        self._cache: Dict[str, tuple] = {}
        
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
        # Check cache first (mtime-validated)
        cache_key = f"{model}/{prompt_type}"
        file_path = self._resolve_path(model, prompt_type)

        if cache_key in self._cache and not variables:
            cached_mtime, cached_content = self._cache[cache_key]
            try:
                current_mtime = file_path.stat().st_mtime
                if current_mtime == cached_mtime:
                    return cached_content
            except OSError:
                pass  # file gone — fall through to reload

        if not file_path.exists():
            raise FileNotFoundError(f"Prompt template not found: {file_path}")
            
        # Load content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Cache with mtime
        try:
            self._cache[cache_key] = (file_path.stat().st_mtime, content)
        except OSError:
            pass
        
        # Substitute variables if provided
        if variables:
            content = self._substitute_variables(content, variables)
            
        return content

    def _resolve_path(self, model: str, prompt_type: str) -> Path:
        """Resolve the filesystem path for a prompt template."""
        file_path = self.prompts_dir / model / f"{prompt_type}.md"

        if not file_path.exists():
            # Reasoning variant → fall back to the base model's prompts
            base_model = re.sub(r'_reasoning$', '', model)
            if base_model != model:
                file_path = self.prompts_dir / base_model / f"{prompt_type}.md"

        if not file_path.exists():
            # Try without model prefix (shared template)
            file_path = self.prompts_dir / "templates" / f"{prompt_type}.md"

        return file_path
        
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
        
    def has_prompt(self, model: str, prompt_type: str) -> bool:
        """Check whether a prompt template exists for the given model and type.

        Uses the same fallback logic as ``load_prompt`` (reasoning-variant
        fallback then shared templates) but never raises.
        """
        file_path = self.prompts_dir / model / f"{prompt_type}.md"
        if file_path.exists():
            return True
        # Reasoning variant fallback
        base_model = re.sub(r'_reasoning$', '', model)
        if base_model != model:
            if (self.prompts_dir / base_model / f"{prompt_type}.md").exists():
                return True
        # Shared template fallback
        if (self.prompts_dir / "templates" / f"{prompt_type}.md").exists():
            return True
        return False

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
        
    def load_rag_prompt(
        self,
        model: str,
        query: str,
        context: str,
    ) -> list:
        """
        Load RAG prompt with query and context passages.
        
        Args:
            model: Model name
            query: The user query to answer
            context: Retrieved context passage(s)
            
        Returns:
            List of message dicts ready for API call
        """
        system_prompt = self.load_prompt(model, "rag_agent_system")
        
        user_content = (
            f"## Retrieved Context\n{context}\n\n"
            f"## Question\n{query}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

    # Structured output instruction appended to every tool-calling
    # evaluation so the evaluator can reliably parse selected tools.
    _TC_OUTPUT_FORMAT = (
        "\n\n## Required Response Format\n"
        "After your analysis, you MUST end your response with a JSON block "
        "using exactly this structure:\n"
        "```json\n"
        "{\n"
        '  "selected_tools": [\n'
        '    {"tool_name": "<exact_function_name>", "arguments": {"<param>": "<value>"}}\n'
        "  ],\n"
        '  "clarification": "<question if required params are missing, else null>",\n'
        '  "direct_response": "<answer if no tool is needed, else null>"\n'
        "}\n"
        "```\n"
        "Rules:\n"
        "- Always include selected_tools with the tool(s) you would call, "
        "even if you need to ask for missing parameters first.\n"
        "- Use the exact function names from the Available Tools list.\n"
        "- If no tool is appropriate, use an empty selected_tools array."
    )

    def load_tool_calling_prompt(
        self,
        model: str,
        query: str,
        available_tools: Optional[List[Dict]] = None,
    ) -> list:
        """
        Load tool-calling prompt with query and available tools.
        
        Args:
            model: Model name
            query: The user query
            available_tools: List of tool definitions (OpenAI function schema)
            
        Returns:
            List of message dicts ready for API call
        """
        system_prompt = self.load_prompt(model, "tool_calling_agent_system")

        tools_desc = ""
        if available_tools:
            tools_desc = (
                "## Available Tools\n"
                + json.dumps(available_tools, indent=2, ensure_ascii=False)
                + "\n\n"
            )

        user_content = (
            f"{tools_desc}## User Request\n{query}"
            f"{self._TC_OUTPUT_FORMAT}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

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
            models: List of models to compare (default: all available models)
            
        Returns:
            Dictionary mapping model names to prompt content
        """
        if models is None:
            # Default: all model directories that contain prompts
            available = self.list_available_prompts()
            models = list(available.keys())
            
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
