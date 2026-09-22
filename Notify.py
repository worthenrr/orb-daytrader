"""Push alerts to your phone through ntfy.sh (free, no account needed).

Setup: install the "ntfy" app, tap +, subscribe to a topic name only you
know (e.g. mike-orb-8f3k2q), then set NTFY_TOPIC to that same name.
"""
import logging

import requests

log = logging.getLogger("orb")


def send(cfg, title, message, priority="default"):
    log.info("%s | %s", title, message)
    if not cfg.ntfy_topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{cfg.ntfy_topic}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority},
            timeout=10,
        )
    except Exception as exc:  # alerts must never crash the bot
        log.warning("alert failed: %s", exc)
