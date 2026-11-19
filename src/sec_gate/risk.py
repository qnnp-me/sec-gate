"""风险评分：对将要执行的命令做静态体检，分数决定页面要几次确认。"""
import os
import re

NET_TOOLS = {"curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp", "rsync",
             "telnet", "socat", "dig", "nslookup", "ftp", "httpie", "aria2c"}
ENCODE_TOOLS = {"base64", "xxd", "od", "openssl", "iconv", "gpg", "uudecode", "uuencode"}
KNOWN_BINS = {"git", "docker", "docker-compose", "kubectl", "helm", "psql", "mysql", "redis-cli",
              "sh", "bash", "dash", "zsh", "echo", "printf", "cat", "ls", "cp", "mv", "rm", "mkdir",
              "jq", "yq", "sed", "awk", "grep", "egrep", "find", "head", "tail", "cut", "sort", "uniq",
              "python3", "python", "node", "npm", "pnpm", "yarn", "go", "make", "cmake", "brew", "tar",
              "gzip", "zip", "unzip", "date", "sleep", "env", "which", "test", "true", "false", "xargs",
              "sha256sum", "md5sum", "wc", "tr", "chmod", "chown", "ln", "readlink", "stat", "du", "df",
              "perl", "php", "ruby", "java", "mvn", "gradle", "npx", "tsc", "eslint",
              "prettier", "vitest", "jest", "pytest", "ruff", "black", "mypy", "cargo", "rustc",
              "curl", "wget", "nc", "ssh", "scp", "rsync", "base64", "xxd", "openssl"}
SENSITIVE_PREFIXES = ("~/.ssh", "/etc/", "~/.config", "~/.local/state", "/root", "~/.gnupg", "/proc/")
SHELLS = {"sh", "bash", "dash", "zsh"}
HOST_RE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?", re.IGNORECASE)


def risk_profile(argv):
    """对命令做静态体检：分数决定页面要几次确认。"""
    prog = os.path.basename(argv[0]) if argv else ""
    joined = " ".join(argv)
    score = 0
    signals, hosts = [], []
    script = ""
    tokens = {os.path.basename(a) for a in argv}
    if prog in SHELLS and "-c" in argv:
        i = argv.index("-c")
        script = argv[i + 1] if i + 1 < len(argv) else ""
        if any(ch in script for ch in (";", "|", "$(", "&&", "||", ">")):
            score += 25; signals.append("shell 拼接")
        tokens |= {os.path.basename(t) for t in script.split()}
    net_hit = sorted(tokens & NET_TOOLS)
    enc_hit = sorted(tokens & ENCODE_TOOLS)
    if net_hit:
        score += 40; signals.append("网络出口：" + "、".join(net_hit))
    if enc_hit:
        score += 30; signals.append("编码/变换：" + "、".join(enc_hit))
    if prog and prog not in KNOWN_BINS:
        score += 15; signals.append(f"非惯用程序 {prog}")
    for a in argv:
        if any(prefix in a for prefix in SENSITIVE_PREFIXES):
            score += 20; signals.append("涉及敏感路径"); break
    if len(argv) + len(script.split()) > 40:
        score += 10; signals.append("命令过长")
    for match in HOST_RE.finditer(joined):
        host = match.group(0)
        if host.replace(".", "").isdigit():
            continue
        hosts.append(host)
    level = "high" if score >= 60 else ("medium" if score >= 30 else "low")
    return {"score": score, "level": level, "signals": signals, "hosts": sorted(set(hosts))[:6]}
