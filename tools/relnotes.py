#!/usr/bin/env python3
"""Release notes from git history, and the commit-message convention check (CLAUDE.md "Commit messages").

  tools/relnotes.py                  notes for <previous v* tag>..HEAD
  tools/relnotes.py v0.5.0           notes for <tag before v0.5.0>..v0.5.0
  tools/relnotes.py v0.3.0..v0.4.0   notes for an explicit range
  tools/relnotes.py --check A..B     check the commit messages in A..B (CI); exit 1 on errors, warnings only print

Notes come from the first-parent history of the range (main's merges and direct commits). Each commit's
`Release-note:` trailers decide where it goes:
  Release-note: new | change | fix | docs | dev | none   (subject line is the note)
  Release-note: fix: <text>                               (text is the note; repeat the trailer for several notes)
A merge without the trailer uses the trailers of the commits it brings in; with none anywhere (older history) the
subject is used and the category guessed from the changed files and the wording. Release bumps are skipped.
Stdlib only; needs git >= 2.25 and full history (actions/checkout fetch-depth: 0).
"""
import argparse
import re
import subprocess
import sys

CATEGORIES = {  # trailer value -> section heading, in output order
    "new": "New",
    "change": "Changes",
    "fix": "Fixes",
    "docs": "Documentation",
    "dev": "Developer",
}
ALIASES = {"feature": "new", "feat": "new", "changed": "change", "fixes": "fix", "doc": "docs", "test": "dev",
           "tests": "dev", "internal": "dev"}
SKIP = "none"

RELEASE_BUMP = re.compile(r"^Release v?\d+\.\d+\.\d+\b")
# paths that never reach a player (anything else under native/ or the shipped docs is player-facing)
DEV_PATH = re.compile(r"^(CLAUDE\.md|docs/(NOTES\.md|investigations/|logs/)|tools/|launch/(?!package\.sh$)|\.github/"
                      r"|native/src/testing\.c$|native/build\.sh$|native/proxy/|\.gitignore$|VERSION$)")
DOCS_PATH = re.compile(r"^(README\.md|docs/COMMANDS\.md|docs/commands-[^/]*\.md|LICENSE)$")
FIX_WORDS = re.compile(r"\b(fix(es|ed)?|crash(es)?|stop(s)?|no longer|don'?t|doesn'?t|prevent)\b", re.I)

SEP, END = "\x1f", "\x1e"


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def resolve_range(spec):
    if spec and ".." in spec:
        return spec
    end = spec or "HEAD"
    try:
        start = git("describe", "--tags", "--abbrev=0", "--match", "v*", f"{end}^").strip()
    except subprocess.CalledProcessError:
        return end  # no earlier release: whole history
    return f"{start}..{end}"


def commits(rng, first_parent):
    fmt = SEP.join(["%H", "%P", "%s", "%B",
                    "%(trailers:key=Release-note,valueonly,unfold,separator=%x1d)"]) + END
    args = ["log", "--reverse", f"--format={fmt}"]
    if first_parent:
        args.append("--first-parent")
    out = git(*args, rng)
    for rec in out.split(END):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, subject, body, notes = rec.split(SEP)
        yield {"sha": sha, "parents": parents.split(), "subject": subject.strip(), "body": body,
               "notes": [n.strip() for n in notes.split("\x1d") if n.strip()]}


def parse_note(value):
    """'fix' -> ('fix', None); 'new: Text' -> ('new', 'Text'); unknown -> (None, value)."""
    head, _, text = value.partition(":")
    cat = head.strip().lower()
    cat = ALIASES.get(cat, cat)
    if cat in CATEGORIES or cat == SKIP:
        return cat, (text.strip() or None)
    return None, value


def clean_subject(s):
    s = re.sub(r"^Merge( branch '[^']*'( into \S+)?)?:?\s*", "", s) if s.startswith("Merge") else s
    return s[:1].upper() + s[1:] if re.match(r"[a-z]+ ", s) else s  # not 'e2e', 'lane-restore:', paths


def guess(c):
    files = [f for f in git("diff", "--name-only", c["parents"][0], c["sha"]).split("\n") if f] \
        if c["parents"] else []
    player = [f for f in files if not DEV_PATH.match(f)]
    if not player:
        return "dev"
    if all(DOCS_PATH.match(f) for f in player):
        return "docs"
    return "fix" if FIX_WORDS.search(c["subject"]) else "change"


def entries(c):
    """[(category, text)] for one first-parent commit."""
    tagged = [(n, c["subject"]) for n in c["notes"]]
    if not tagged and len(c["parents"]) > 1:  # merge without trailers: use its branch commits' trailers
        for sub in commits(f"{c['parents'][0]}..{c['sha']}^2", first_parent=False):
            tagged += [(n, sub["subject"]) for n in sub["notes"]]
    if tagged:
        out = []
        for n, subject in tagged:
            cat, text = parse_note(n)
            if cat is None:  # no valid category (--check flags it): free text, or the subject for a bare word
                cat, text = "change", (n if " " in n else None)
            if cat != SKIP:
                out.append((cat, text or clean_subject(subject)))
        return out
    if RELEASE_BUMP.match(c["subject"]):
        return []
    cat, text = guess(c), clean_subject(c["subject"])
    if not text and len(c["parents"]) > 1:  # default "Merge branch 'x'": list what it brought
        return [(cat, clean_subject(sub["subject"])) for sub in
                commits(f"{c['parents'][0]}..{c['sha']}^2", first_parent=False) if len(sub["parents"]) == 1]
    return [(cat, text or c["subject"])]


def notes_markdown(rng):
    groups = {k: [] for k in CATEGORIES}
    for c in commits(rng, first_parent=True):
        for cat, text in entries(c):
            if text not in groups[cat]:
                groups[cat].append(text)
    parts = []
    for cat, heading in CATEGORIES.items():
        if groups[cat]:
            parts.append(f"### {heading}\n" + "".join(f"- {t}\n" for t in groups[cat]))
    return "\n".join(parts) if parts else "_No user-facing changes._\n"


# --- convention check -------------------------------------------------------------------------------------------
ERRORS = [
    (re.compile(r"^(fixup!|squash!|amend!)"), "fixup/squash commit: squash it before merging"),
    (re.compile(r"\bWIP\b|^wip\b", re.I), "WIP subject"),
    (re.compile(r"^Merge (branch|remote-tracking branch) '"), "default merge subject: say what the merge brings"),
]
WARNINGS = [
    (re.compile(r"\b0x[0-9a-fA-F]{6,}\b"), "raw address in the subject (put it in the body)"),
    (re.compile(r"\blanes?\b|\bB4B_LANE\b|\bagent[- ]?(name|[0-9])\b|\bsubagent\b|\bcoordinator\b", re.I),
     "internal test-infra / agent jargon in the subject"),
    (re.compile(r"^[\w./-]+\.(c|h|cpp|py|sh|ya?ml):"), "file-name prefix: describe the change in plain words"),
]


def check(rng):
    errors = warnings = 0
    for c in commits(rng, first_parent=False):
        s, short = c["subject"], c["sha"][:9]
        problems = [("error", msg) for rx, msg in ERRORS if rx.search(s)]
        problems += [("warning", msg) for rx, msg in WARNINGS if rx.search(s)]
        if len(s) > 100:
            problems.append(("warning", f"subject is {len(s)} chars (keep it under ~100)"))
        for n in c["notes"]:
            if parse_note(n)[0] is None:
                problems.append(("error", f"bad Release-note trailer '{n}' (want one of: "
                                          f"{', '.join([*CATEGORIES, SKIP])}, optionally ': text')"))
        for level, msg in problems:
            print(f"::{level}::{short} {msg}: {s}")
            errors += level == "error"
            warnings += level == "warning"
    print(f"commit check {rng}: {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("range", nargs="?", help="tag (notes since the tag before it) or A..B; default HEAD")
    ap.add_argument("--check", action="store_true", help="check commit messages in the range instead")
    a = ap.parse_args()
    if a.check:
        if not a.range:
            ap.error("--check needs a range")
        return check(a.range)
    sys.stdout.write(notes_markdown(resolve_range(a.range)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
