"""Temporary localhost Control Plane access for the operator release command.

No dotenv, token, remote command, or configurable forwarding destination.
Only the foreground SSH process created here belongs to this context.
"""

from contextlib import contextmanager
import http.client
import subprocess
import time
from urllib.parse import urlsplit

from ai_task_deploy_config import DeploymentError, exception_detail
from ai_task_process import managed_process_options


def endpoint_ready(base_url, *, timeout):
    """Probe HTTP, without credentials, proxies, redirects or response logging."""
    parsed = urlsplit(base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    try:
        connection.request("GET", parsed.path.rstrip("/") + "/openapi.json")
        response = connection.getresponse()
        return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def tunnel_argv(config, port):
    config.validate()
    return [str(config.ssh_path), "-F", "none", "-N", "-T",
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ExitOnForwardFailure=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", 'UserKnownHostsFile="' + config.known_hosts_path.as_posix() + '"',
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3", "-o", "ForwardAgent=no",
            "-o", "PermitLocalCommand=no", "-o", "ControlMaster=no",
            "-o", "ControlPath=none", "-o", "ForkAfterAuthentication=no",
            "-i", str(config.ssh_key_path),
            "-L", f"127.0.0.1:{port}:127.0.0.1:8000",
            config.ssh_user + "@" + config.ssh_host]


def stop_tunnel(process):
    # Never search by PID/port or use a global SSH/process-tree kill.
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@contextmanager
def ensure_control_plane_access(base_url, config, *, requester=None, popen=None,
                                clock=None, sleep=None):
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        yield
        return
    # Only explicitly configured localhost ports opt into automation.
    port = parsed.port
    if port is None:
        yield
        return
    if not 1 <= port <= 65535 or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DeploymentError("Control Plane localhost URL rejected")
    requester = requester or endpoint_ready
    popen = popen or subprocess.Popen
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    if requester(base_url, timeout=2):
        yield
        return
    process = None
    try:
        try:
            process = popen(tunnel_argv(config, port), shell=False,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **managed_process_options())
        except Exception as exc:
            raise DeploymentError("Control Plane SSH tunnel could not start: " + exception_detail(exc)) from None
        deadline = clock() + 30
        while True:
            status = process.poll()
            if status is not None:
                raise DeploymentError(f"Control Plane SSH tunnel exited before readiness (exit={status}); "
                                      "check SSH credentials, known hosts and local port availability")
            remaining = deadline - clock()
            if remaining <= 0:
                raise DeploymentError("Control Plane SSH tunnel readiness timed out (30s); "
                                      "GET /openapi.json did not return HTTP 200")
            if requester(base_url, timeout=min(2, remaining)):
                if process.poll() is not None:
                    raise DeploymentError("Control Plane SSH tunnel exited during readiness check")
                break
            sleep(min(0.25, max(0, deadline - clock())))
        yield
    finally:
        if process is not None:
            stop_tunnel(process)
