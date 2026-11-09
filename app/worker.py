"""Run as `python -m app.worker`, independently of the API process."""
import os
import time
from pathlib import Path

from .research import work_once
from .storage import Store


def main():
    default = Path(__file__).resolve().parents[1]/'data'/'agriplan.db'
    store = Store(os.getenv('AGRIPLAN_DB',str(default)))
    print('AgriPlan Worker 已启动（单 Worker / SQLite）',flush=True)
    try:
        while True:
            if not work_once(store):
                time.sleep(.5)
    except KeyboardInterrupt:
        pass


if __name__=='__main__':
    main()
