"""Search driver: compile variants in shadow roots, score them in-process,
record every trial in SQLite, and climb.

Phases per function, all inside one time budget:
  1. singles: every honest move on the base text;
  2. beam: combinations of the useful singles on the base text;
  3. hill-climbing with random restarts: from the best states, regenerate the
     moves on the current text (so dependent levers chain) and take the best
     neighbour of each batch, allowing sideways moves across plateaus.
"""

import concurrent.futures as cf
import difflib
import hashlib
import json
import os
import queue
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import moves as M
from .elfscore import Score, Target, Elf, regressions

ROOT = M.ROOT
LS = M.LS
HELPER_RX = re.compile(r"^static inline void \w+Part\d+\(", re.M)


def demangle(sym: str) -> str:
    try:
        r = subprocess.run([os.path.join(ROOT, "build", "tools", "dtk"), "demangle", sym],
                           capture_output=True, text=True, timeout=30)
        out = r.stdout.strip()
        return out or sym
    except (OSError, subprocess.SubprocessError):
        return sym


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()


# --------------------------------------------------------------------------
# Trial database
# --------------------------------------------------------------------------

class TrialDB:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS trials (
        fn TEXT, unit TEXT, hash TEXT, score TEXT, sig TEXT, moves TEXT, secs REAL, at REAL,
        PRIMARY KEY (fn, hash));
    CREATE TABLE IF NOT EXISTS runs (
        fn TEXT, unit TEXT, status TEXT, base TEXT, best TEXT, builds INTEGER, cached INTEGER,
        secs REAL, moves TEXT, patch TEXT, at REAL);
    """

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=60)
        self.db.executescript(self.SCHEMA)
        self.lock = threading.Lock()

    def get(self, fn: str, h: str) -> Optional[Tuple[str, str, str]]:
        with self.lock:
            r = self.db.execute("SELECT score, sig, moves FROM trials WHERE fn=? AND hash=?", (fn, h)).fetchone()
        return r

    def put(self, fn, unit, h, score: Score, moves: str, secs: float):
        row = score.to_row() if score.ok else "FAIL " + score.err[:200]
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO trials VALUES (?,?,?,?,?,?,?,?)",
                            (fn, unit, h, row, score.sig, moves, secs, time.time()))
            self.db.commit()

    def count(self, fn: str) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM trials WHERE fn=?", (fn,)).fetchone()[0]

    def run_done(self, fn: str) -> bool:
        with self.lock:
            r = self.db.execute("SELECT COUNT(*) FROM runs WHERE fn=?", (fn,)).fetchone()
        return bool(r[0])

    def put_run(self, fn, unit, status, base, best, builds, cached, secs, moves, patch):
        with self.lock:
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (fn, unit, status, base, best, builds, cached, secs, moves, patch, time.time()))
            self.db.commit()


# --------------------------------------------------------------------------
# Units and the evaluator
# --------------------------------------------------------------------------

@dataclass
class Unit:
    name: str
    rel_src: str
    target: str
    obj: str


def find_unit(unit: str) -> Unit:
    ud = json.load(open(os.path.join(ROOT, "objdiff.json")))
    us = [u for u in ud["units"] if u["name"] == unit or u["name"].endswith("/" + unit)]
    if not us:
        raise SystemExit("unit not found: " + unit)
    u = us[0]
    return Unit(u["name"], u["metadata"]["source_path"], os.path.join(ROOT, u["target_path"]), u["base_path"])


_NINJA = {}


def ninja_db():
    if "db" not in _NINJA:
        _NINJA["db"] = LS.NinjaDB(os.path.join(ROOT, "build.ninja"))
    return _NINJA["db"]


class Evaluator:
    def __init__(self, unit: Unit, fn: str, jobs: int, work: str, db: TrialDB):
        self.unit, self.fn, self.jobs, self.db = unit, fn, jobs, db
        self.target = Target(unit.target)
        self.work = tempfile.mkdtemp(prefix="hs-", dir=work)
        self.shadows: "queue.Queue" = queue.Queue()
        for i in range(jobs):
            self.shadows.put(LS.Shadow(self.work, unit.rel_src, i))
        self.cmd = {}
        self.mem: Dict[str, Score] = {}
        self.builds = 0
        self.cached = 0
        self.build_secs = 0.0
        self.lock = threading.Lock()

    def close(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _compile(self, text: str, keep: Optional[str] = None) -> Tuple[Optional[str], str]:
        sh = self.shadows.get()
        try:
            sh.write(text)
            out = os.path.join(sh.out, os.path.splitext(os.path.basename(self.unit.rel_src))[0] + ".o")
            if os.path.exists(out):
                os.remove(out)
            cmd = ninja_db().compile_command(self.unit.obj, self.unit.rel_src, sh.out)
            t0 = time.time()
            r = subprocess.run(cmd, shell=True, cwd=sh.dir, capture_output=True, text=True, errors="replace")
            dt = time.time() - t0
            with self.lock:
                self.builds += 1
                self.build_secs += dt
            if r.returncode or not os.path.exists(out):
                lines = (r.stdout + r.stderr).splitlines()
                err = [i for i, l in enumerate(lines) if "Error:" in l]
                if err:
                    i = err[0]
                    return None, " | ".join(x.strip("# ") for x in lines[max(0, i - 1):i + 2])
                return None, " ".join(lines[-3:]) if lines else "compile failed"
            if keep:
                shutil.copy(out, keep)
                return keep, ""
            elf_copy = out + ".score"
            shutil.copy(out, elf_copy)
            return elf_copy, ""
        finally:
            self.shadows.put(sh)

    def score(self, text: str, moves: str = "") -> Score:
        h = text_hash(text)
        with self.lock:
            if h in self.mem:
                return self.mem[h]
        row = self.db.get(self.fn, h)
        if row is not None:
            sc = Score.from_row(row[0], row[1])
            with self.lock:
                self.mem[h] = sc
                self.cached += 1
            return sc
        t0 = time.time()
        path, err = self._compile(text)
        if path is None:
            sc = Score(False, err)
        else:
            try:
                sc = self.target.score(path, self.fn)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        self.db.put(self.fn, self.unit.name, h, sc, moves, time.time() - t0)
        with self.lock:
            self.mem[h] = sc
        return sc

    def profile(self, text: str) -> Optional[Dict[str, tuple]]:
        path = os.path.join(self.work, "profile-%s.o" % text_hash(text)[:12])
        p, err = self._compile(text, keep=path)
        if p is None:
            return None
        try:
            return self.target.profile(p)
        finally:
            os.remove(p)

    def map(self, items: List[Tuple[str, str]], deadline: float) -> List[Tuple[str, str, Score]]:
        """Score (text, moves) pairs in parallel; stops submitting at the deadline."""
        out = []
        with cf.ThreadPoolExecutor(self.jobs) as ex:
            futs = {}
            it = iter(items)
            done = False
            while True:
                while not done and len(futs) < self.jobs * 2:
                    if time.time() > deadline:
                        done = True
                        break
                    try:
                        text, mv = next(it)
                    except StopIteration:
                        done = True
                        break
                    futs[ex.submit(self.score, text, mv)] = (text, mv)
                if not futs:
                    break
                fin, _ = cf.wait(futs, return_when=cf.FIRST_COMPLETED)
                for f in fin:
                    text, mv = futs.pop(f)
                    out.append((text, mv, f.result()))
                    if out[-1][2].exact:
                        done = True
        return out


# --------------------------------------------------------------------------
# The search
# --------------------------------------------------------------------------

@dataclass
class State:
    text: str
    score: Score
    moves: List[str] = field(default_factory=list)


class Search:
    def __init__(self, unit: str, fn: str, jobs: int = 3, work: Optional[str] = None,
                 db: Optional[TrialDB] = None, log=print, seed: int = 0):
        self.u = find_unit(unit)
        self.fn = fn
        self.log = log
        self.db = db
        self.ev = Evaluator(self.u, fn, jobs, work or tempfile.gettempdir(), db)
        with open(os.path.join(ROOT, self.u.rel_src), encoding="utf-8", newline="") as f:
            self.base_text = f.read()
        self.dm = demangle(fn)
        self.info = LS.parse_demangled(self.dm)
        self.rng = random.Random(seed or hash(fn) & 0xFFFF)
        self.idx = None
        self.gen_cache: Dict[str, List[M.Move]] = {}
        self.kind_gain: Dict[str, List[int]] = {}
        self.max_moves = 6  # a longer chain of rewrites stops reading like one person's source
        self.fails: Dict[tuple, int] = {}  # a move that fails to compile twice is dropped
        # one generated inline level per variant: a stack of machine-cut levels
        # (a helper calling a helper) no longer reads like the original source
        self.max_helpers = 1
        self.base_helpers = len(HELPER_RX.findall(self.base_text))

    def too_many(self, text: Optional[str]) -> bool:
        return text is None or len(HELPER_RX.findall(text)) - self.base_helpers > self.max_helpers

    def close(self):
        self.ev.close()

    def moves_of(self, text: str) -> List[M.Move]:
        h = text_hash(text)
        if h not in self.gen_cache:
            if len(self.gen_cache) > 64:
                self.gen_cache.clear()
            if self.idx is None:
                self.idx = M.merged_index(self.base_text)
            mv = M.generate(text, self.info, self.idx)
            for m in mv:
                m.stable = (m.kind, tuple(re.sub(r"Part\d+", "Part", text[a:b]) for a, b, _ in m.edits),
                            tuple(re.sub(r"Part\d+", "Part", r) for _, _, r in m.edits))
            self.gen_cache[h] = [m for m in mv if self.fails.get(m.stable, 0) < 2]
        return [m for m in self.gen_cache[h] if self.fails.get(m.stable, 0) < 2]

    def honest(self, text: str) -> List[str]:
        return M.lint(self.base_text, text)

    def unit_check(self, text: str) -> List[str]:
        if self.base_profile is None:
            self.base_profile = self.ev.profile(self.base_text)
        vp = self.ev.profile(text)
        if self.base_profile is None or vp is None:
            return ["profile compile failed"]
        return regressions(self.base_profile, vp, self.fn)

    def run(self, budget: float) -> dict:
        t_start = time.time()
        deadline = t_start + budget
        self.base_profile = None
        self.best_at = (0.0, 0)
        base = self.ev.score(self.base_text, "base")
        res = {"unit": self.u.name, "fn": self.fn, "dm": self.dm, "base": base, "best": base,
               "moves": [], "status": "no-gain", "text": None, "regress": []}
        self.log("%s  [%s]  base: %s" % (self.dm, self.u.name, base.short()))
        if not base.ok:
            res["status"] = "base-fail"
            return res
        if base.exact:
            res["status"] = "already-exact"
            return res
        best = State(self.base_text, base, [])
        states: Dict[str, State] = {base.sig: best}
        rejected_exact = []

        def consider(text, mv, sc) -> Optional[State]:
            nonlocal best
            if not sc.ok:
                return None
            st = State(text, sc, mv)
            if sc.sig not in states or sc.key < states[sc.sig].score.key or \
                    (sc.key == states[sc.sig].score.key and len(mv) < len(states[sc.sig].moves)):
                states[sc.sig] = st
            if sc.exact:
                bad = self.honest(text) + self.unit_check(text)
                if bad:
                    self.log("  exact but rejected (%s): %s" % ("; ".join(bad[:3]), " + ".join(mv)))
                    rejected_exact.append((mv, bad))
                    return None
                best = st
                self.best_at = (time.time() - t_start, self.ev.builds)
                return st
            if sc.key < best.score.key:
                self.best_at = (time.time() - t_start, self.ev.builds)
            if sc.key < best.score.key or (sc.key == best.score.key and len(mv) < len(best.moves)):
                best = st
            return None

        # ---- 1. singles
        mvs = self.moves_of(self.base_text)
        self.log("  %d moves (%s)" % (len(mvs), ", ".join("%s %d" % kv for kv in sorted(_count(m.kind for m in mvs).items()))))
        items = []
        for m in mvs:
            t2 = M.apply(self.base_text, [m])
            if t2 is not None and t2 != self.base_text:
                items.append((t2, m.desc))
        singles: Dict[int, Score] = {}
        move_of = {}
        for i, m in enumerate(mvs):
            t2 = M.apply(self.base_text, [m])
            if t2 is not None:
                move_of.setdefault(t2, i)
        res1 = self.ev.map(items, t_start + budget * 0.6)
        for text, mv, sc in res1:
            singles[move_of.get(text, -1)] = sc
            if not sc.ok and move_of.get(text) is not None:
                self.fails[mvs[move_of[text]].stable] = 2
            kind = mv.split(" ")[0]
            g = self.kind_gain.setdefault(kind, [0, 0])
            g[1] += 1
            if sc.ok and sc.key < base.key:
                g[0] += 1
            if consider(text, [mv], sc):
                return self._finish(res, best, "exact", t_start, rejected_exact)
        nfail = sum(1 for _, _, s in res1 if not s.ok)
        self.log("  singles: %d scored (%d failed to compile), best %s, %.0fs" % (
            len(res1), nfail, best.score.short(), time.time() - t_start))

        # ---- 2. beam over useful singles on the base text
        useful = [i for i, s in singles.items() if i >= 0 and s.ok and
                  (s.key < base.key or (s.sig != base.sig and s.key[:2] <= base.key[:2]))]
        useful.sort(key=lambda i: singles[i].key)
        pool, sigs = [], set()
        for i in useful:
            if singles[i].sig not in sigs:
                sigs.add(singles[i].sig)
                pool.append(i)
        pool = pool[:14]
        beam_states = [((i,), singles[i]) for i in pool[:6]]
        seen = set((i,) for i in pool)
        depth = 1
        beam_deadline = t_start + budget * 0.8
        while beam_states and time.time() < beam_deadline and depth < 4:
            depth += 1
            combos = []
            for combo, _ in beam_states:
                for i in pool:
                    nc = tuple(sorted(set(combo + (i,))))
                    if len(nc) == len(combo) + 1 and nc not in seen:
                        seen.add(nc)
                        combos.append(nc)
            items, combo_of = [], {}
            for c in combos:
                t2 = M.apply(self.base_text, [mvs[i] for i in c])
                if not self.too_many(t2) and t2 not in combo_of:
                    combo_of[t2] = c
                    items.append((t2, " + ".join(mvs[i].desc for i in c)))
            if not items:
                break
            out = self.ev.map(items, beam_deadline)
            nxt, sg = [], set()
            for text, mv, sc in sorted(out, key=lambda r: r[2].key):
                if consider(text, mv.split(" + "), sc):
                    return self._finish(res, best, "exact", t_start, rejected_exact)
                if sc.ok and sc.sig not in sg:
                    sg.add(sc.sig)
                    nxt.append((combo_of.get(text, ()), sc))
            beam_states = [x for x in nxt if x[0]][:6]
            self.log("  beam depth %d: %d combos, best %s" % (depth, len(out), best.score.short()))

        # ---- 3. hill-climbing with random restarts and kicks
        restarts = kicks = dry = 0
        exhausted = set()
        stable_of = {}

        def rank(pool):
            return sorted(pool, key=lambda s: (s.score.key, len(s.moves)))

        while time.time() < deadline and dry < 40:
            open_states = [s for s in rank(states.values()) if text_hash(s.text) not in exhausted
                           and len(s.moves) < self.max_moves]
            r = self.rng.random()
            builds0 = self.ev.builds
            if not open_states or r < 0.25:
                # kick: two or three random moves at once from a good state
                srcs = [s for s in rank(states.values()) if len(s.moves) <= self.max_moves - 2][:8]
                if not srcs:
                    dry += 1
                    continue
                src = self.rng.choice(srcs)
                nb = self.moves_of(src.text)
                if len(nb) < 2:
                    dry += 1
                    continue
                ms = self.rng.sample(nb, min(len(nb), self.rng.choice((2, 2, 3)), self.max_moves - len(src.moves)))
                t2 = M.apply(src.text, ms)
                if self.too_many(t2) or text_hash(t2) in self.ev.mem:
                    dry += 1
                    continue
                kicks += 1
                out = self.ev.map([(t2, " + ".join(src.moves + [m.desc for m in ms]))], deadline)
                if not out:
                    break
                text, mv, sc = out[0]
                if consider(text, mv.split(" + "), sc):
                    return self._finish(res, best, "exact", t_start, rejected_exact)
                if not sc.ok:
                    dry = 0
                    continue
                cur = State(text, sc, mv.split(" + "))
            else:
                cur = open_states[0] if r < 0.6 else self.rng.choice(open_states[:8])
            restarts += 1
            stall = 0
            while stall < 4 and time.time() < deadline and len(cur.moves) < self.max_moves:
                nb = self.moves_of(cur.text)
                w = []
                for m in nb:
                    g = self.kind_gain.get(m.kind, [0, 0])
                    w.append((1 + g[0]) / (2 + g[1]) + 0.1 / m.prio + self.rng.random() * 0.3)
                order = [m for _, m in sorted(zip(w, nb), key=lambda x: -x[0])]
                batch = []
                for m in order:
                    t2 = M.apply(cur.text, [m])
                    if self.too_many(t2) or t2 == cur.text:
                        continue
                    if text_hash(t2) in self.ev.mem:
                        continue
                    batch.append((t2, " + ".join(cur.moves + [m.desc])))
                    stable_of[t2] = m.stable
                    if len(batch) >= self.ev.jobs * 3:
                        break
                if not batch:
                    exhausted.add(text_hash(cur.text))
                    break
                out = self.ev.map(batch, deadline)
                for text, _, sc in out:
                    if not sc.ok and text in stable_of:
                        self.fails[stable_of[text]] = self.fails.get(stable_of[text], 0) + 1
                stable_of.clear()
                step = None
                for text, mv, sc in sorted(out, key=lambda r: r[2].key):
                    if consider(text, mv.split(" + "), sc):
                        return self._finish(res, best, "exact", t_start, rejected_exact)
                    if step is None and sc.ok:
                        step = (text, mv, sc)
                if step and step[2].key < cur.score.key:
                    cur = State(step[0], step[2], step[1].split(" + "))
                    stall = 0
                elif step and step[2].key == cur.score.key and step[2].sig != cur.score.sig \
                        and self.rng.random() < 0.4:
                    cur = State(step[0], step[2], step[1].split(" + "))
                    stall += 1
                else:
                    stall += 1
            dry = 0 if self.ev.builds > builds0 else dry + 1
        if dry >= 40:
            self.log("  climb: neighbourhood exhausted")
        self.log("  climb: %d restarts, %d kicks" % (restarts, kicks))
        status = "improved" if best.score.key < base.key else "no-gain"
        return self._finish(res, best, status, t_start, rejected_exact)

    def _finish(self, res, best: State, status, t_start, rejected):
        res["status"] = status
        res["best"] = best.score
        res["moves"] = best.moves
        res["rejected"] = rejected
        if best.text != self.base_text:
            if status == "improved":
                bad = self.honest(best.text) + self.unit_check(best.text)
                res["regress"] = bad
                if bad:
                    res["status"] = "improved-regresses"
            res["text"] = best.text
        res["secs"] = time.time() - t_start
        res["best_at"] = self.best_at
        res["builds"] = self.ev.builds
        res["cached"] = self.ev.cached
        self.log("  result: %s  %s  (%d builds, %d cached, %.0fs, %.1f variants/min)" % (
            res["status"], best.score.short(), self.ev.builds, self.ev.cached, res["secs"],
            60.0 * self.ev.builds / max(res["secs"], 1)))
        if best.moves:
            self.log("  best found at %.0fs, build %d" % self.best_at)
            self.log("  moves: " + " + ".join(best.moves))
        return res

    def patch(self, text: str) -> str:
        a = self.base_text.splitlines(keepends=True)
        b = text.splitlines(keepends=True)
        return "".join(difflib.unified_diff(a, b, "a/" + self.u.rel_src, "b/" + self.u.rel_src))


def _count(it):
    d = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return d
