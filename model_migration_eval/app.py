"""
Azure OpenAI Model Migration Evaluation - Main Application
==========================================================

This application provides a comprehensive framework for evaluating
and comparing Azure OpenAI models across generations.

Usage:
    # Run the web interface
    python app.py
    
    # Or run evaluations from command line
    python -m src.evaluation.run_evaluation --model gpt4 --type classification
"""

import os
import sys
import argparse
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.web.routes import create_app
from src.clients.azure_openai import create_client_from_config
from src.evaluation.evaluator import ModelEvaluator
from src.evaluation.comparator import ModelComparator


def setup_logging(level: str = "INFO"):
    """Configure logging for the application"""
    # Create logs directory BEFORE setting up the file handler
    Path('logs').mkdir(exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('logs/app.log', mode='a')
        ]
    )


def run_web_server(host: str = "127.0.0.1", port: int = 5000, debug: bool = False):
    """Run the Flask web application"""
    app = create_app(config_path="config/settings.yaml")
    print(f"""
╔═══════════════════════════════════════════════════════════╗
║     Azure OpenAI Model Migration Evaluation Framework     ║
╠═══════════════════════════════════════════════════════════╣
║  Web interface starting at: http://{host}:{port}          ║
║                                                           ║
║  Endpoints:                                               ║
║    /           - Dashboard                                ║
║    /evaluate   - Run batch evaluations                    ║
║    /compare    - Compare any two models                  ║
║    /results    - Browse saved results                     ║
║    /prompts    - View prompt templates                    ║
║                                                           ║
║  Press Ctrl+C to stop                                     ║
╚═══════════════════════════════════════════════════════════╝
""")
    app.run(host=host, port=port, debug=debug)


def run_cli_evaluation(
    model: str,
    evaluation_type: str,
    config_path: str = "config/settings.yaml",
    save_results: bool = True
):
    """Run evaluation from command line"""
    print(f"Running {evaluation_type} evaluation on {model}...")
    
    try:
        client = create_client_from_config(config_path)
        evaluator = ModelEvaluator(client)
        
        if evaluation_type == "classification":
            result = evaluator.evaluate_classification(model)
        elif evaluation_type == "dialog":
            result = evaluator.evaluate_dialog(model)
        elif evaluation_type == "general":
            result = evaluator.evaluate_general(model)
        else:
            print(f"Unknown evaluation type: {evaluation_type}")
            return
            
        # Print summary
        print("\n" + "=" * 50)
        print("EVALUATION RESULTS")
        print("=" * 50)
        print(f"Model: {result.model_name}")
        print(f"Type: {result.evaluation_type}")
        print(f"Scenarios: {result.scenarios_tested}")
        print(f"Errors: {len(result.errors)}")
        
        if result.classification_metrics:
            cm = result.classification_metrics
            print(f"\nClassification Metrics:")
            print(f"  Accuracy:  {cm.accuracy:.2%}")
            print(f"  F1 Score:  {cm.f1_score:.2%}")
            print(f"  Precision: {cm.precision:.2%}")
            print(f"  Recall:    {cm.recall:.2%}")
            
        if result.latency_metrics:
            lm = result.latency_metrics
            print(f"\nLatency Metrics:")
            print(f"  Mean:   {lm.mean_latency:.3f}s")
            print(f"  Median: {lm.median_latency:.3f}s")
            print(f"  P95:    {lm.p95_latency:.3f}s")
            
        if save_results:
            result.save()
            print(f"\nResults saved to data/results/")
            
    except Exception as e:
        print(f"Error during evaluation: {e}")
        raise


def run_cli_comparison(
    model_a: str = "gpt4",
    model_b: str = "gpt5",
    evaluation_type: str = "classification",
    config_path: str = "config/settings.yaml"
):
    """Run model comparison from command line"""
    try:
        client = create_client_from_config(config_path)
        comparator = ModelComparator(client)
        
        eval_types = ["classification", "dialog", "general"] if evaluation_type == "all" else [evaluation_type]
        
        for eval_type in eval_types:
            print(f"\nComparing {model_a} vs {model_b} on {eval_type}...")
            report = comparator.compare_models(
                model_a=model_a,
                model_b=model_b,
                evaluation_type=eval_type
            )
            
            # Print report
            print("\n" + report.to_markdown())
            
            # Save report
            report.save()
            print(f"Comparison saved to data/results/")
        
    except Exception as e:
        print(f"Error during comparison: {e}")
        raise


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Azure OpenAI Model Migration Evaluation Framework"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Web server command
    web_parser = subparsers.add_parser("web", help="Start the web interface")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    web_parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    web_parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    
    # Evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Run evaluation")
    eval_parser.add_argument("--model", required=True, help="Model name as defined in settings.yaml (e.g., gpt4, gpt5, gpt4o)")
    eval_parser.add_argument("--type", required=True, choices=["classification", "dialog", "general"], help="Evaluation type")
    eval_parser.add_argument("--config", default="config/settings.yaml", help="Config file path")
    eval_parser.add_argument("--no-save", action="store_true", help="Don't save results")
    
    # Compare command
    compare_parser = subparsers.add_parser("compare", help="Compare two models")
    compare_parser.add_argument("--model-a", default="gpt4", help="First model")
    compare_parser.add_argument("--model-b", default="gpt5", help="Second model")
    compare_parser.add_argument("--type", default="classification", choices=["classification", "dialog", "general", "all"], help="Evaluation type (use 'all' to run all types)")
    compare_parser.add_argument("--config", default="config/settings.yaml", help="Config file path")
    
    args = parser.parse_args()
    
    setup_logging()
    
    if args.command == "web" or args.command is None:
        # Default to web server if no command specified
        host = getattr(args, 'host', '127.0.0.1')
        port = getattr(args, 'port', 5000)
        debug = getattr(args, 'debug', False)
        run_web_server(host=host, port=port, debug=debug)
        
    elif args.command == "evaluate":
        run_cli_evaluation(
            model=args.model,
            evaluation_type=args.type,
            config_path=args.config,
            save_results=not args.no_save
        )
        
    elif args.command == "compare":
        run_cli_comparison(
            model_a=args.model_a,
            model_b=args.model_b,
            evaluation_type=args.type,
            config_path=args.config
        )


if __name__ == "__main__":
    main()
