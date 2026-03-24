"""Launcher that suppresses SIGINT during heavy module imports."""
import signal
import sys
import os
import traceback

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Log everything to a file since VS Code terminals can lose output
log_path = os.path.join('logs', 'launcher.log')
os.makedirs('logs', exist_ok=True)
log_f = open(log_path, 'w', encoding='utf-8')

def _log(msg):
    print(msg, flush=True)
    log_f.write(msg + '\n')
    log_f.flush()

# Ignore SIGINT while importing heavy pydantic/openai modules
original_handler = signal.getsignal(signal.SIGINT)
signal.signal(signal.SIGINT, signal.SIG_IGN)

try:
    _log("Importing heavy modules...")
    import openai
    import pydantic
    import flask
    _log(f"Done: openai={openai.__version__}, pydantic={pydantic.__version__}, flask={flask.__version__}")
except Exception:
    _log("IMPORT ERROR:")
    traceback.print_exc(file=log_f)
    traceback.print_exc()
    sys.exit(1)

# Restore SIGINT handler
signal.signal(signal.SIGINT, original_handler)

try:
    _log("Starting Flask app...")
    sys.argv = ['app.py']  # reset argv for argparse
    from app import main
    main()
except KeyboardInterrupt:
    _log("Interrupted by user")
except Exception:
    _log("APP ERROR:")
    traceback.print_exc(file=log_f)
    traceback.print_exc()
    sys.exit(1)
finally:
    log_f.close()
