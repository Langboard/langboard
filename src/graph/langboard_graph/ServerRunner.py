from langboard_shared import FastAPIRunner
from langboard_shared.Env import Env
from .Constants import APP_CONFIG_FILE, BASE_DIR
from .core.graph.GraphRunner import GraphRunner


def run():
    GraphRunner.clear_bot_status_cache()
    FastAPIRunner.run(f"{Env.PROJECT_NAME}_graph.AppInstance:app", APP_CONFIG_FILE, BASE_DIR)
