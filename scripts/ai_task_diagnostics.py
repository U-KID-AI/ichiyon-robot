"""Preserve operation diagnostics while removing credential values."""

import os
import re


def redact_secrets(value: object, secrets=()) -> str:
    text = str(value or "")
    known = list(secrets)
    known.extend(value for key, value in os.environ.items()
                 if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY", key, re.I))
    for secret in sorted({str(value) for value in known if value and len(str(value)) >= 4},
                         key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                  "[redacted private key]", text, flags=re.S)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[redacted]", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{16,})\b",
                  "[redacted]", text)
    text = re.sub(r"(?i)(?<![A-Z0-9_])((?:[A-Z0-9_]*(?:token|secret|password|api[_-]?key|private[_-]?key))[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
                  r"\1[redacted]", text)
    return re.sub(r"(https?://[^/\s:@]+:)[^@\s/]+@", r"\1[redacted]@", text)
