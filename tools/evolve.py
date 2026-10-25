#!/usr/bin/env python3
"""evolve.py - the bookkeeping half of a self-evolving agent that learns through skill files only.

The model does the thinking (what went wrong, what the lesson is, how the rule should read).
This script does the counting, filing, versioning and rollback, so those never depend on memory.

Agent folder layout (created by `init`):
  <agent>/SKILL.md            the agent's instructions; ends with a "## Learned rules" section
  <agent>/loop/lessons.md     one line per lesson, with a repeat count and a status
  <agent>/loop/run-log.md     one entry per run
  <agent>/loop/changelog.md   one entry per promotion or rollback
  <agent>/loop/archive.md     lessons pruned or retired
  <agent>/loop/versions/      SKILL.md copies taken before every promotion
  <agent>/loop/state.json     version number and next lesson id

Commands:
  init       <agent>
  log        <agent> --task T --outcome pass|fail|corrected [--note N]
  lesson     <agent> "lesson text" [--same L007] [--new] [--source user|self|check]
  candidates <agent> [--min 3]
  promote    <agent> L007 --rule "rule text"
  rollback   <agent>
  prune      <agent> [--days 30]
  status     <agent>
Every command accepts --date YYYY-MM-DD (used for testing; defaults to today).
"""
import argparse, datetime as dt, difflib, json, re, shutil, sys
from pathlib import Path

LINE = re.compile(r"^- (L\d{3}) \| count (\d+) \| (\S+) > (\S+) \| (\w+) \| (\w+) \| (.*)$")
RULES_HEAD = "## Learned rules"
SIZE_WARN = 500


def norm(t):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t.lower())).strip()


class Agent:
    def __init__(self, root, today):
        self.root = Path(root)
        self.loop = self.root / "loop"
        self.skill = self.root / "SKILL.md"
        self.today = today

    # files ---------------------------------------------------------------
    def state(self):
        p = self.loop / "state.json"
        return json.loads(p.read_text()) if p.exists() else {"version": 1, "next_lesson": 1}

    def save_state(self, s):
        (self.loop / "state.json").write_text(json.dumps(s, indent=2))

    def lessons(self):
        out = []
        p = self.loop / "lessons.md"
        if not p.exists():
            return out
        for ln in p.read_text(encoding="utf-8").splitlines():
            m = LINE.match(ln)
            if m:
                out.append(dict(id=m[1], count=int(m[2]), first=m[3], last=m[4],
                                status=m[5], source=m[6], text=m[7]))
        return out

    def save_lessons(self, ls):
        head = ("# Lessons\n\nOne line per lesson. Format: id | count | first seen > last seen | "
                "status | source | text\nStatus: open, promoted, retired.\n\n")
        body = "\n".join(f"- {l['id']} | count {l['count']} | {l['first']} > {l['last']} | "
                         f"{l['status']} | {l['source']} | {l['text']}" for l in ls)
        (self.loop / "lessons.md").write_text(head + body + ("\n" if body else ""), encoding="utf-8")

    def append(self, name, text):
        with open(self.loop / name, "a", encoding="utf-8") as f:
            f.write(text)

    # commands --------------------------------------------------------------
    def init(self):
        (self.loop / "versions").mkdir(parents=True, exist_ok=True)
        if not self.skill.exists():
            n = self.root.name
            self.skill.write_text(f"---\nname: {n}\ndescription: TODO describe when to use {n}.\n---\n\n"
                                  f"# {n}\n\nTODO core instructions.\n\n{RULES_HEAD}\n\n", encoding="utf-8")
        elif RULES_HEAD not in self.skill.read_text(encoding="utf-8"):
            with open(self.skill, "a", encoding="utf-8") as f:
                f.write(f"\n\n{RULES_HEAD}\n\nRules this agent learned from its own runs. "
                        "Each one names the lesson it came from.\n\n")
        for name, head in (("run-log.md", "# Run log\n\n"), ("changelog.md", "# Changelog\n\n"),
                           ("archive.md", "# Archive\n\n")):
            if not (self.loop / name).exists():
                (self.loop / name).write_text(head, encoding="utf-8")
        if not (self.loop / "lessons.md").exists():
            self.save_lessons([])
        if not (self.loop / "state.json").exists():
            self.save_state({"version": 1, "next_lesson": 1})
        print(f"Loop ready in {self.loop}")

    def log(self, task, outcome, note):
        self.append("run-log.md", f"## {self.today} | {outcome}\n- Task: {task}\n- Note: {note or 'none'}\n\n")
        print(f"Run logged: {outcome}")

    def lesson(self, text, same, force_new, source):
        ls, s = self.lessons(), self.state()
        target = None
        if same:
            target = next((l for l in ls if l["id"] == same), None)
            if not target:
                sys.exit(f"No lesson {same}")
        else:
            key = norm(text)
            target = next((l for l in ls if norm(l["text"]) == key and l["status"] == "open"), None)
            if not target and not force_new:
                scored = [(difflib.SequenceMatcher(None, key, norm(l["text"])).ratio(), l)
                          for l in ls if l["status"] == "open"]
                scored = [x for x in scored if x[0] >= 0.8]
                if scored:
                    best = max(scored, key=lambda x: x[0])[1]
                    print(f"Similar to {best['id']}: {best['text']}\n"
                          f"Rerun with --same {best['id']} to count it there, or --new to file it separately.")
                    sys.exit(3)
        if target:
            target["count"] += 1
            target["last"] = self.today
            self.save_lessons(ls)
            print(f"{target['id']} now seen {target['count']} times")
        else:
            lid = f"L{s['next_lesson']:03d}"
            ls.append(dict(id=lid, count=1, first=self.today, last=self.today, status="open",
                           source=source, text=text.replace("\n", " ").strip()))
            s["next_lesson"] += 1
            self.save_lessons(ls)
            self.save_state(s)
            print(f"{lid} filed")

    def candidates(self, minimum):
        found = [l for l in self.lessons() if l["status"] == "open"
                 and (l["count"] >= minimum or l["source"] == "user")]
        if not found:
            print("No lessons ready to promote.")
        for l in found:
            why = "stated by the user" if l["source"] == "user" and l["count"] < minimum else f"seen {l['count']} times"
            print(f"{l['id']} ({why}): {l['text']}")

    def promote(self, lid, rule):
        ls, s = self.lessons(), self.state()
        l = next((x for x in ls if x["id"] == lid), None)
        if not l or l["status"] != "open":
            sys.exit(f"{lid} is not an open lesson")
        text = self.skill.read_text(encoding="utf-8")
        if RULES_HEAD not in text:
            sys.exit("SKILL.md has no Learned rules section. Run init first.")
        shutil.copy(self.skill, self.loop / "versions" / f"SKILL.v{s['version']}.md")
        head, tail = text.split(RULES_HEAD, 1)
        n = len(re.findall(r"^\d+\. ", tail, flags=re.M)) + 1
        sep = "\n\n" if n == 1 else "\n"
        tail = tail.rstrip("\n") + f"{sep}{n}. {rule.strip()} (from {lid}, {self.today})\n"
        self.skill.write_text(head + RULES_HEAD + tail, encoding="utf-8")
        l["status"] = "promoted"
        self.save_lessons(ls)
        s["version"] += 1
        s.setdefault("promoted", []).append(lid)
        self.save_state(s)
        self.append("changelog.md", f"## v{s['version']} | {self.today}\n- Promoted {lid} to Learned rule {n}: {rule.strip()}\n\n")
        lines = len(self.skill.read_text(encoding="utf-8").splitlines())
        print(f"Promoted {lid} as rule {n}. SKILL.md is now v{s['version']} ({lines} lines).")
        if lines > SIZE_WARN:
            print(f"Warning: SKILL.md is over {SIZE_WARN} lines. Merge or retire rules before adding more.")

    def rollback(self):
        s = self.state()
        prev = self.loop / "versions" / f"SKILL.v{s['version'] - 1}.md"
        if s["version"] <= 1 or not prev.exists():
            sys.exit("Nothing to roll back to.")
        shutil.copy(self.skill, self.loop / "versions" / f"SKILL.v{s['version']}.rolledback.md")
        shutil.copy(prev, self.skill)
        s["version"] -= 1
        lid = (s.get("promoted") or [None]).pop() if s.get("promoted") else None
        if lid:
            ls = self.lessons()
            for l in ls:
                if l["id"] == lid:
                    l["status"] = "open"
            self.save_lessons(ls)
        self.save_state(s)
        self.append("changelog.md", f"## v{s['version']} | {self.today}\n- Rolled back to v{s['version']}; {lid or 'no lesson'} reopened\n\n")
        print(f"Rolled back to v{s['version']}. {lid or 'No lesson'} is open again.")

    def prune(self, days):
        today = dt.date.fromisoformat(self.today)
        keep, gone = [], []
        for l in self.lessons():
            stale = (l["status"] == "open" and l["count"] == 1 and l["source"] != "user"
                     and (today - dt.date.fromisoformat(l["last"])).days > days)
            (gone if stale else keep).append(l)
        if gone:
            self.save_lessons(keep)
            self.append("archive.md", "".join(f"- {self.today} pruned {l['id']}: {l['text']}\n" for l in gone))
        print(f"Pruned {len(gone)} stale lessons.")

    def status(self):
        ls, s = self.lessons(), self.state()
        lines = len(self.skill.read_text(encoding="utf-8").splitlines()) if self.skill.exists() else 0
        opn = [l for l in ls if l["status"] == "open"]
        print(f"Version v{s['version']} | SKILL.md {lines} lines | lessons: {len(opn)} open, "
              f"{sum(l['status'] == 'promoted' for l in ls)} promoted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["init", "log", "lesson", "candidates", "promote", "rollback", "prune", "status"])
    ap.add_argument("agent")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--task"); ap.add_argument("--outcome", choices=["pass", "fail", "corrected"])
    ap.add_argument("--note"); ap.add_argument("--same"); ap.add_argument("--new", action="store_true")
    ap.add_argument("--source", default="self", choices=["user", "self", "check"])
    ap.add_argument("--min", type=int, default=3); ap.add_argument("--rule")
    ap.add_argument("--days", type=int, default=30); ap.add_argument("--date")
    a = ap.parse_args()
    ag = Agent(a.agent, a.date or dt.date.today().isoformat())
    if a.command != "init" and not ag.loop.exists():
        sys.exit("No loop folder. Run: evolve.py init <agent>")
    if a.command == "init": ag.init()
    elif a.command == "log":
        if not a.task or not a.outcome: sys.exit("log needs --task and --outcome")
        ag.log(a.task, a.outcome, a.note)
    elif a.command == "lesson":
        if not a.arg: sys.exit("lesson needs the lesson text")
        ag.lesson(a.arg, a.same, a.new, a.source)
    elif a.command == "candidates": ag.candidates(a.min)
    elif a.command == "promote":
        if not a.arg or not a.rule: sys.exit("promote needs a lesson id and --rule")
        ag.promote(a.arg, a.rule)
    elif a.command == "rollback": ag.rollback()
    elif a.command == "prune": ag.prune(a.days)
    elif a.command == "status": ag.status()


if __name__ == "__main__":
    main()
