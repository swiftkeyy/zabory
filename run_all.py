"""Запуск Telegram-бота и VK-бота одновременно в одном процессе Railway."""
import multiprocessing
import subprocess
import sys
import signal
import os


def run_bot(script: str):
    """Запускает указанный скрипт бота."""
    proc = subprocess.Popen([sys.executable, script])
    proc.wait()


def main():
    procs = [
        multiprocessing.Process(target=run_bot, args=("bot.py",), name="tg_bot"),
        multiprocessing.Process(target=run_bot, args=("vk_bot.py",), name="vk_bot"),
    ]

    def shutdown(signum, frame):
        for p in procs:
            if p.is_alive():
                p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    for p in procs:
        p.start()
        print(f"[run_all] Started {p.name} (pid={p.pid})")

    for p in procs:
        p.join()


if __name__ == "__main__":
    main()
