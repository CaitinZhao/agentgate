"""Remote environment probe: check docker/mirrors/py311+openjiuwen(aarch64) before deployment.

Usage: python agentgate/deploy/probe_remote.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy_eval import HOST, MIRRORS, Remote, _shq  # noqa: E402


def probe_image(r: Remote, image: str) -> bool:
    """True when the image exists locally or can be pulled via a mirror."""
    code, _ = r.run("docker image inspect %s >/dev/null 2>&1 && echo ok" % image, show_tail=0)
    if code == 0:
        print("    镜像已存在：%s" % image)
        return True
    for mirror in MIRRORS:
        ref = (mirror + image.split("/")[-1]) if mirror else image
        code, _ = r.run("docker pull %s 2>&1 | tail -1" % ref, timeout=900, show_tail=0)
        if code == 0:
            if mirror:
                r.run("docker tag %s %s" % (ref, image))
            print("    镜像拉取成功：%s" % ref)
            return True
    return False


def main():
    r = Remote()
    print("[0] 连接 %s" % HOST)

    print("[1] 基础环境 ...")
    r.run("uname -m && python3 --version && docker --version")
    r.run("docker info 2>/dev/null | grep -A2 -i mirror || echo 'no mirror config'")

    print("[2] 已有部署状态 ...")
    r.run("ls /opt/agentgate 2>/dev/null | head -8 || echo '/opt/agentgate 不存在'")
    r.run("docker ps -a --format '{{.Names}} {{.Status}} {{.Image}}' | head -5")

    print("[3] 拉取 python:3.11-slim ...")
    if not probe_image(r, "python:3.11-slim"):
        print("!! python:3.11-slim 所有镜像源失败，jiuwen 容器路线受阻")
        return 1

    print("[4] 探测 openjiuwen 在 aarch64/py311 的安装可用性 ...")
    cmd = ("docker run --rm python:3.11-slim sh -c "
           "\"pip download --no-deps --dest /tmp -i https://pypi.tuna.tsinghua.edu.cn/simple "
           "openjiuwen==0.1.18 2>&1 | tail -3\"")
    code, text = r.run(cmd, timeout=600, show_tail=0)
    tail = [l for l in text.strip().splitlines()[-3:]]
    print("    [rc=%d] %s" % (code, " | ".join(tail)[:300]))
    if code != 0:
        print("!! openjiuwen 下载失败（wheel/sdist 均不可得？）")
        return 1
    print("探针通过：python:3.11-slim 可用，openjiuwen 可下载")
    r.cli.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
