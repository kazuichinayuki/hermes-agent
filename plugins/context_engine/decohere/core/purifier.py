"""Data purification and noise reduction for Decohere context compression."""
import re
import json
from html.parser import HTMLParser
from typing import Any

# Patterns for privacy and noise
_EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
# Basic regex for IPv4 (avoiding internal IPs like 127.0.0.1 or 192.168.x.x for safety, but let's just redact generally if needed, actually 127.0.0.1 is fine to keep, let's redact public looking ones. For simplicity, just redact all).
_IP_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
# Basic regex for tokens/keys
_KEY_RE = re.compile(r'(?i)(?:api_key|secret|token|password|bearer)["\'\s:=]+([a-zA-Z0-9\-_\.]{20,})')
# Very long URLs (over 150 chars)
_LONG_URL_RE = re.compile(r'https?://[^\s<"]{150,}')



def purify_json(data: Any) -> Any:
    """Recursively strip nulls, empty lists, and empty dicts from JSON-like objects."""
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            val = purify_json(v)
            if val is not None and val != "" and val != [] and val != {}:
                cleaned[k] = val
        return cleaned
    elif isinstance(data, list):
        cleaned = []
        for item in data:
            val = purify_json(item)
            if val is not None and val != "" and val != [] and val != {}:
                cleaned.append(val)
        return cleaned
    return data


def purify_text(text: str) -> str:
    """Apply privacy scrubbing, URL shortening, and deduplication to raw text."""
    if not isinstance(text, str):
        return text

    # 1. JSON minification
    text_stripped = text.strip()
    if (text_stripped.startswith('{') and text_stripped.endswith('}')) or \
       (text_stripped.startswith('[') and text_stripped.endswith(']')):
        try:
            parsed = json.loads(text_stripped)
            cleaned = purify_json(parsed)
            if not cleaned:
                text = "{}" if text_stripped.startswith('{') else "[]"
            else:
                text = json.dumps(cleaned, ensure_ascii=False, separators=(',', ':'))
        except json.JSONDecodeError:
            pass

    # 2. Document/HTML purification via markitdown
    # If the text looks like HTML or we want to leverage markitdown
    if "<html" in text.lower() or "<body" in text.lower() or "</a>" in text.lower() or "</div>" in text.lower():
        try:
            from markitdown import MarkItDown
            # markitdown.convert_stream expects a file-like object, but maybe it has a string converter?
            # Actually, we can write the string to a temporary file, or see if it exposes a direct string API
            import tempfile
            import os
            
            md = MarkItDown()
            # MarkItDown currently works best with files, we write the HTML string to a tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as tf:
                tf.write(text)
                tf_path = tf.name
                
            try:
                result = md.convert(tf_path)
                if result and result.text_content:
                    text = result.text_content
            finally:
                os.unlink(tf_path)
                
        except Exception as e:
            pass # Fallback to native text logic if markitdown fails

    # 3. Privacy Scrubbing
    text = _EMAIL_RE.sub('[EMAIL_REDACTED]', text)
    # text = _IP_RE.sub('[IP_REDACTED]', text)  # IP redaction can break legitimate curl commands/API responses
    # Redact sensitive keys
    def redact_key(match):
        return match.group(0).replace(match.group(1), "[KEY_REDACTED]")
    text = _KEY_RE.sub(redact_key, text)

    # 4. URL Shortener
    text = _LONG_URL_RE.sub('[LONG_URL_TRUNCATED]', text)

    # 5. Log Deduplication (e.g., repeated lines in terminal output)
    lines = text.splitlines()
    if len(lines) > 10:
        deduped = []
        repeat_count = 0
        last_line = None
        for line in lines:
            if line == last_line and line.strip():
                repeat_count += 1
            else:
                if repeat_count > 5:
                    deduped.append(f"... [Previous line repeated {repeat_count} times] ...")
                elif repeat_count > 0:
                    deduped.extend([last_line] * repeat_count)
                
                deduped.append(line)
                repeat_count = 0
                last_line = line
                
        if repeat_count > 5:
            deduped.append(f"... [Previous line repeated {repeat_count} times] ...")
        elif repeat_count > 0:
            deduped.extend([last_line] * repeat_count)
            
        text = "\n".join(deduped)

    return text
