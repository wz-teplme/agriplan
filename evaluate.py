"""Run isolated synthetic evaluations or bind human review to a saved report."""
import argparse
import json
import sys
from pathlib import Path

from app.evaluation import preflight, run_suite
from app.eval_report import finalize_report, save_report, validate_reviews


def main(argv=None):
    parser = argparse.ArgumentParser(description='AgriPlan 隔离合成评测；不验证生产农艺效果')
    parser.add_argument('--mode',choices=('demo','live'),default='demo')
    parser.add_argument('--case',action='append',dest='case_ids')
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--allow-network',action='store_true')
    parser.add_argument('--max-calls',type=int)
    parser.add_argument('--max-model-seconds',type=float)
    parser.add_argument('--output',default=str(Path(__file__).resolve().parent/'data'/'evaluations'))
    parser.add_argument('--review',metavar='REPORT_JSON')
    parser.add_argument('--review-file',metavar='FILE')
    args = parser.parse_args(argv)
    from run import load_env
    load_env()  # Configuration only; evaluation Store paths never use AGRIPLAN_DB.
    try:
        if args.preflight:
            print(json.dumps(preflight(),ensure_ascii=False))
            return 0
        if bool(args.review) != bool(args.review_file):
            raise ValueError('--review 和 --review-file 必须一并提供')
        if args.review:
            original = json.loads(Path(args.review).read_text(encoding='utf-8-sig'))
            payload = json.loads(Path(args.review_file).read_text(encoding='utf-8-sig'))
            reviews = validate_reviews(original,payload)
            report = finalize_report(original | {'manual_reviews':reviews})
        else:
            report = finalize_report(run_suite(mode=args.mode,case_ids=args.case_ids,
                max_calls=args.max_calls,max_seconds=args.max_model_seconds,allow_network=args.allow_network))
        print(json.dumps(save_report(report,args.output),ensure_ascii=False))
        return 1 if report['summary']['failed_cases'] else 0
    except (ValueError,TypeError,KeyError,OSError) as exc:
        # Avoid arbitrary provider/file payloads in terminal errors as well as reports.
        print('评测配置或复核格式错误（'+type(exc).__name__+'）；请检查参数、配置和报告绑定。',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
