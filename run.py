"""Start API and one durable Worker; --no-worker supports separate processes."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env():
    path = ROOT/'.env'
    if path.exists():
        for raw in path.read_text(encoding='utf-8-sig').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            key = key.strip()
            if sep and key.startswith('AGRIPLAN_') and key.replace('_','').isalnum():
                os.environ.setdefault(key,value.strip().strip('"').strip("'"))
    path = Path(os.getenv('AGRIPLAN_DB',str(ROOT/'data'/'agriplan.db')))
    os.environ['AGRIPLAN_DB'] = str((ROOT/path).resolve() if not path.is_absolute() else path)


def main():
    parser = argparse.ArgumentParser(description='AgriPlan local MVP')
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--no-worker',action='store_true')
    args = parser.parse_args()
    load_env()
    from app.main import create_app
    import uvicorn
    app = create_app()
    worker = None
    print(f'AgriPlan MVP: http://127.0.0.1:{args.port}',flush=True)
    print('Demo accounts: sr_chen / fr_zhou / sr_liu; default password: agriplan-demo',flush=True)
    try:
        if not args.no_worker:
            worker = subprocess.Popen([sys.executable,'-X','utf8','-m','app.worker'],cwd=ROOT)
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='info')
    finally:
        if worker and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()


if __name__=='__main__':
    main()
