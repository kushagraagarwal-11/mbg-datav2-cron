# -*- coding: utf-8 -*-
"""One-off: remove the Device Recovery Funnel posts from a Slack channel.

Only the bot that posted a message can delete it, and its token lives solely as
a GitHub secret, so this runs in Actions rather than locally.
"""
import os, json, requests

TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL = os.environ["SLACK_CHANNEL_ID"]
NEEDLE = "Device Recovery Funnel"
H = {"Authorization": "Bearer %s" % TOKEN}

hist = requests.get("https://slack.com/api/conversations.history",
                    headers=H, params={"channel": CHANNEL, "limit": 100}).json()
if not hist.get("ok"):
    raise SystemExit("conversations.history failed: %s" % hist.get("error"))

targets = [m for m in hist.get("messages", [])
           if NEEDLE in (m.get("text") or "") and m.get("bot_id")]
print("found %d matching bot messages" % len(targets), flush=True)

for m in targets:
    ts = m["ts"]
    # remove the attached images first; a deleted message can leave files behind
    for f in m.get("files", []) or []:
        r = requests.post("https://slack.com/api/files.delete",
                          headers=H, data={"file": f["id"]}).json()
        print("  file %s -> ok=%s %s" % (f["id"], r.get("ok"), r.get("error") or ""), flush=True)
    r = requests.post("https://slack.com/api/chat.delete",
                      headers=H, data={"channel": CHANNEL, "ts": ts}).json()
    print("message ts=%s -> ok=%s %s" % (ts, r.get("ok"), r.get("error") or ""), flush=True)

print("done", flush=True)
