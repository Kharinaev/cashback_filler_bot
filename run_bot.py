import logging
import sys

import fire
import yaml
from src.bot import run_bot


def init_logger():
    logger = logging.getLogger("logger")
    logger.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    return logger


def load_config(config_path):
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)
    return config


def main(config_path="configs/config.yaml"):
    init_logger()
    cfg = load_config(config_path)
    run_bot(cfg)


if __name__ == "__main__":
    fire.Fire(main)
