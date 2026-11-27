from langboard_shared import FastAPIRunner
from langboard_shared.Env import Env
from .Constants import APP_CONFIG_FILE, BASE_DIR


def run():
    from langboard_shared.ai import BotScheduleHelper

    BotScheduleHelper.utils.reload_cron()
    FastAPIRunner.run(f"{Env.PROJECT_NAME}.AppInstance:app", APP_CONFIG_FILE, BASE_DIR)
