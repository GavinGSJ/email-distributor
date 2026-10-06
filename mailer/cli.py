import argparse
import getpass
import re
import sys
from dataclasses import replace
from pathlib import Path

from .config import load_job, load_smtp
from .errors import MailerError, PrepareError
from .sender import SmtpSender
from .sendlog import SendLog
from .service import prepare, send_all

DEFAULT_SMTP = "config/smtp.toml"
_STATUS = {"sent": "已发送", "skipped": "跳过", "failed": "失败"}


def _summary(job, mails, smtp):
    first = mails[0].message
    print(f"发件人 : {first['From']}")
    print(f"抄送   : {', '.join(job.cc) or '(无)'}")
    if job.bcc:
        print(f"密送   : {', '.join(job.bcc)}")
    names = [p.get_filename() for p in first.iter_attachments()]
    print(f"附件   : {', '.join(names) or '(无)'}")
    print(f"签名   : {job.signature.name if job.signature else '(无)'}")
    print(f"共 {len(mails)} 封独立邮件:")
    for m in mails:
        print(f"  - {m.to}  |  {m.subject}")


def _prepare(args, smtp):
    job = load_job(args.job)
    if args.signature is not None:
        job = replace(job, signature=Path(args.signature).resolve() if args.signature else None)
    mails = prepare(job, smtp.from_addr, smtp.from_name, test_to=args.test_to, only=args.only)
    return job, mails


def cmd_preview(args):
    smtp = load_smtp(args.smtp)
    job, mails = _prepare(args, smtp)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(mails, start=1):
        safe = re.sub(r"[^\w.@-]+", "_", m.to)
        (out / f"{i:02d}_{safe}.eml").write_bytes(bytes(m.message))
    _summary(job, mails, smtp)
    print(f"\n已生成 {len(mails)} 个 .eml 文件到 {out}（双击可用邮件客户端查看，未发送任何邮件）")
    print("\n—— 第 1 封纯文本内容 ——")
    print(mails[0].message.get_body(("plain",)).get_content())
    return 0


def cmd_send(args):
    smtp = load_smtp(args.smtp)
    if not smtp.password and smtp.username:
        smtp = replace(smtp, password=getpass.getpass(f"{smtp.username} 的 SMTP 密码/授权码: "))
    job, mails = _prepare(args, smtp)
    _summary(job, mails, smtp)
    if args.test_to:
        print(f"\n【测试模式】所有邮件都只发给 {args.test_to}，不抄送，不写发送记录")

    if not args.yes:
        if input("\n确认发送？输入 yes 继续: ").strip().lower() != "yes":
            print("已取消")
            return 1

    log = None if args.test_to else SendLog(job.path.with_suffix(".sendlog.jsonl"))

    def progress(i, total, r):
        extra = f"  ({r.error})" if r.error else ""
        print(f"[{i}/{total}] {_STATUS[r.status]}  {r.to}{extra}")

    with SmtpSender(smtp) as sender:
        results = send_all(
            mails,
            sender,
            log=log,
            interval=job.interval_seconds,
            skip_sent=not args.resend,
            on_progress=progress,
        )
    failed = [r for r in results if r.status == "failed"]
    print(f"\n完成：成功 {sum(r.status == 'sent' for r in results)}，"
          f"跳过 {sum(r.status == 'skipped' for r in results)}，失败 {len(failed)}")
    if failed:
        print("有失败的邮件；修正后重新运行同一命令，已成功的会自动跳过。")
    return 2 if failed else 0


def cmd_check_smtp(args):
    smtp = load_smtp(args.smtp)
    if not smtp.password and smtp.username:
        smtp = replace(smtp, password=getpass.getpass(f"{smtp.username} 的 SMTP 密码/授权码: "))
    with SmtpSender(smtp):
        pass
    print(f"SMTP 连接并登录成功: {smtp.host}:{smtp.port} ({smtp.security})")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="mailer", description="批量独立发送邮件（同文、同抄送、各发各的）")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, job=True):
        if job:
            sp.add_argument("job", help="任务文件 job.toml")
        sp.add_argument("--smtp", default=DEFAULT_SMTP, help=f"SMTP 配置文件（默认 {DEFAULT_SMTP}）")

    def job_opts(sp):
        sp.add_argument("--only", action="append", metavar="EMAIL", help="只处理名单中的这个收件人，可重复")
        sp.add_argument("--test-to", metavar="EMAIL", help="测试：所有邮件都改发到这个地址，不抄送")
        sp.add_argument("--signature", metavar="FILE", help="临时换一个签名文件，空字符串表示不带签名")

    sp = sub.add_parser("preview", help="生成 .eml 预览文件，不发送")
    common(sp)
    job_opts(sp)
    sp.add_argument("--out", default="output/preview", help="预览输出目录")
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("send", help="发送邮件")
    common(sp)
    job_opts(sp)
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认提示")
    sp.add_argument("--resend", action="store_true", help="忽略发送记录，重发已成功的")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("check-smtp", help="只测试 SMTP 连接和登录")
    common(sp, job=False)
    sp.set_defaults(func=cmd_check_smtp)
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PrepareError as e:
        print("无法生成邮件，未发送任何内容:\n  " + "\n  ".join(e.problems), file=sys.stderr)
        return 1
    except MailerError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
