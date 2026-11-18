import argparse
import json
from pathlib import Path

from .proofreading import ProofreadingError, ProofreadingWorkflow
from .store import StateStore


def build_parser():
    parser = argparse.ArgumentParser(description="课件校对建议台")
    parser.add_argument("--state", default="var/proofreading.json", help="状态文件路径")
    commands = parser.add_subparsers(dest="action", required=True)

    ingest = commands.add_parser("ingest", help="导入课件记录")
    ingest.add_argument("path")

    listing = commands.add_parser("list", help="查看课件记录")
    listing.add_argument("--source")

    wordlist = commands.add_parser("wordlist", help="注册新版词表并设为当前版本")
    wordlist.add_argument("path")

    commands.add_parser("wordlists", help="查看词表版本列表")

    scan = commands.add_parser("scan", help="列出仍待处理的建议")
    scan.add_argument("--record", help="只看指定课件")

    adopt = commands.add_parser("adopt", help="采纳建议（按建议编号）")
    adopt.add_argument("ids", nargs="+")

    reject = commands.add_parser("reject", help="按语境拒绝建议（按建议编号）")
    reject.add_argument("ids", nargs="+")

    edit = commands.add_parser("edit", help="更新课件正文（修订号加一）")
    edit.add_argument("record_id")
    source = edit.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file")

    commands.add_parser("adopted", help="查看已采纳变化及其来源词表版本")
    commands.add_parser("rejected", help="查看生效中的拒绝决定")
    commands.add_parser("stale", help="查看因课件编辑而失效的拒绝决定")
    commands.add_parser("status", help="查看总览")
    return parser


def main():
    args = build_parser().parse_args()
    workflow = ProofreadingWorkflow(StateStore(args.state))
    try:
        if args.action == "ingest":
            result = workflow.import_records(json.loads(Path(args.path).read_text(encoding="utf-8")))
        elif args.action == "list":
            result = workflow.list_records(args.source)
        elif args.action == "wordlist":
            result = workflow.register_wordlist(json.loads(Path(args.path).read_text(encoding="utf-8")))
        elif args.action == "wordlists":
            result = workflow.wordlists()
        elif args.action == "scan":
            result = workflow.scan(args.record)
        elif args.action == "adopt":
            result = [workflow.adopt(suggestion_id) for suggestion_id in args.ids]
        elif args.action == "reject":
            result = [workflow.reject(suggestion_id) for suggestion_id in args.ids]
        elif args.action == "edit":
            text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
            result = workflow.edit_text(args.record_id, text)
        elif args.action == "adopted":
            result = workflow.adopted()
        elif args.action == "rejected":
            result = workflow.rejected()
        elif args.action == "stale":
            result = workflow.stale()
        else:
            result = workflow.status()
    except (ProofreadingError, ValueError) as error:
        raise SystemExit(f"错误：{error}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
