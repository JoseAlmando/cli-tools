import argparse
import getpass
import json
import sys

from cli_tools.core import api, context, output
from cli_tools.core.errors import ToolError
from cli_tools.tools.jenkins.client import PROG, WHO_AM_I, Jenkins, job_path

BUILD_NAMES = {"lastBuild", "lastCompletedBuild", "lastFailedBuild", "lastStableBuild", "lastSuccessfulBuild",
               "lastUnstableBuild", "lastUnsuccessfulBuild"}


def cmd_login(jenkins, args):
    if args.token_stdin:
        token = sys.stdin.readline().strip()
    else:
        context.require_terminal(f"{PROG} login")
        token = getpass.getpass("Jenkins API token: ").strip()
    name = jenkins.login(args.user, token)
    output.info(f"Logged in to Jenkins as {name}.")


def cmd_logout(jenkins, args):
    jenkins.credentials_file.unlink(missing_ok=True)
    output.info("Logged out.")


def cmd_me(jenkins, args):
    identity = jenkins.get_object(WHO_AM_I)
    name = identity.get("name")
    authenticated = identity.get("authenticated") is True
    if args.ctx.json_mode:
        print(json.dumps({"username": name, "authenticated": authenticated, "url": jenkins.url}, ensure_ascii=False))
    else:
        print(f"{name or 'anonymous'} on {jenkins.url} ({'authenticated' if authenticated else 'anonymous access'})")


def list_objects(jenkins, path, key, tree):
    data = jenkins.get_object(api.set_query(path, {"tree": tree}))
    rows = data.get(key)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ToolError(f"Expected a list of {key} from {path}")
    return rows


def cmd_jobs(jenkins, args):
    path = f"{job_path(args.folder)}/api/json" if args.folder else "api/json"
    jobs = list_objects(jenkins, path, "jobs", "jobs[name,url,color]")
    columns = ("name", "url", "color")
    output.print_rows(api.pick_fields(jobs, columns), args.table, args.ctx.json_mode, columns)


def cmd_queue(jenkins, args):
    items = list_objects(jenkins, "queue/api/json", "items", "items[id,why,task[name,url]]")
    rows = []
    for item in api.pick_fields(items, ("id", "task.name", "why", "task.url")):
        rows.append({"id": item["id"], "name": item["task.name"], "why": item["why"], "url": item["task.url"]})
    output.print_rows(rows, args.table, args.ctx.json_mode, ("id", "name", "why", "url"))


def build_path(args):
    return f"{job_path(args.job)}/{args.build}"


def cmd_build(jenkins, args):
    data = jenkins.get_object(f"{build_path(args)}/api/json")
    output.print_response(json.dumps(data, ensure_ascii=False), args.ctx.json_mode)


def cmd_log(jenkins, args):
    _, raw, _ = jenkins.call("GET", f"{build_path(args)}/consoleText", None)
    sys.stdout.write(raw)


def build_selector(value):
    if value not in BUILD_NAMES and not (value.isascii() and value.isdecimal()):
        raise argparse.ArgumentTypeError("expected a build number or a permalink such as lastSuccessfulBuild")
    return value


def cmd_api(jenkins, args):
    return api.run_api(args, jenkins.call, args.ctx, next_page=jenkins.next_page)


def build_parser(prog):
    epilog = f"""\
examples:
  {prog} login --url https://jenkins.example.com --user <username>
  {prog} --json --read-only jobs "Folder/Subfolder"
  {prog} --json --read-only build "Folder/Job" lastSuccessfulBuild
  {prog} --json --read-only log "Folder/Job" 42
  {prog} --json --read-only queue --table
  {prog} --json --read-only GET "api/json?tree=jobs[name,color]"

environment variables:
  JENKINS_URL          controller URL, including any context path; a trailing /api/ is accepted
  JENKINS_USER, JENKINS_TOKEN
                       use these with JENKINS_URL instead of the saved session
  JENKINS_CONF_DIR     credentials folder (default ~/.config/jenkins)
  JENKINS_CACERT       extra CA (default ca.pem in the credentials folder)
  JENKINS_ALLOW_HTTP   set to 1 to allow HTTP; any credentials are sent without TLS

Without credentials, requests use Jenkins' anonymous permissions. API tokens do not renew automatically.
--json enables JSON errors and structured output; log always writes plain text.
--read-only sends only GET requests and blocks login and logout. Both flags work anywhere.
"""
    parser = argparse.ArgumentParser(prog=prog, description="Client for the Jenkins Remote Access API.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    p = sub.add_parser("login", aliases=context.command_aliases("login"), help="check and save a Jenkins API token")
    p.add_argument("--url", help="Jenkins controller URL (defaults to JENKINS_URL or the saved URL)")
    p.add_argument("--user", required=True, help="Jenkins username")
    p.add_argument("--token-stdin", action="store_true", help="read the API token from stdin")
    p.set_defaults(func=cmd_login)
    sub.add_parser("logout", aliases=context.command_aliases("logout"),
                   help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the Jenkins user or anonymous identity").set_defaults(func=cmd_me)
    p = sub.add_parser("jobs", help="list jobs and folders")
    p.add_argument("folder", nargs="?", help="full folder name, with folders separated by /")
    p.add_argument("--table", action="store_true", help="show a table even with --json")
    p.set_defaults(func=cmd_jobs)
    p = sub.add_parser("queue", help="list queued builds")
    p.add_argument("--table", action="store_true", help="show a table even with --json")
    p.set_defaults(func=cmd_queue)
    for name, func, help_text in (("build", cmd_build, "show build metadata"), ("log", cmd_log, "read the build console")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("job", help="full job name, e.g. Folder/Job")
        p.add_argument("build", nargs="?", default="lastBuild", type=build_selector,
                       help="build number or permalink (default lastBuild)")
        p.set_defaults(func=func)
    api.add_api_parser(sub, prog, "job/Example/api/json", cmd_api, api_prefix="/",
                       description="Call a path relative to the Jenkins controller. JSON API paths end in /api/json.")
    return parser


def main(argv, ctx=None, prog=PROG):
    ctx = ctx or context.CLIContext.from_env()
    parser = build_parser(prog)
    args = parser.parse_args(api.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    args.command = context.canonical_command(args.command)
    ctx.check_session_command(args.command)
    args.ctx = ctx
    return args.func(Jenkins(getattr(args, "url", None)), args)
