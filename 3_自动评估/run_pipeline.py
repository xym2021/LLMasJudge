"""End-to-end pipeline: run the tested models on the case set, judge each case as soon as it
finishes, then aggregate scores and rebuild the run viewer.

Usage:
    python run_pipeline.py                 # run / resume the default run
    python run_pipeline.py --cases 6 --run-name pipeline_0925
    python run_pipeline.py --report-only   # only re-aggregate and rebuild the viewer

Per (model, case) task:
    1. Trip+ run.py: inference -> conversion -> rule evaluation, only this case
    2. Judge B on every turn of the case
    3. Judge A (Trip+ user simulation) on the last plan of the case
Every stage is retried up to MAX_ATTEMPTS times with exponential backoff, then skipped and logged.
State is saved to <run_root>/pipeline_state.json after every event, so a rerun resumes where it stopped;
the viewer and leaderboard are rebuilt after every SAVE_EVERY finished tasks.
"""

import argparse
import ctypes
import json
import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TRIP = ROOT / "trip-plus"
QUERY = ROOT / "2_评测集_cases" / "query_10.json"
DB_DIR = TRIP / "database" / "sample" / "en"
PY = sys.executable

MODELS = [
    {"label": "deepseek-flash", "config": "deepseek-v4-flash", "stop_on_quota": False,
     "note": "DeepSeek 官方 key 鉴权失败，改走阿里云百炼的 deepseek-v4-flash"},
    {"label": "kimi-k3", "config": "kimi-k3", "stop_on_quota": True, "note": "阿里云百炼"},
    {"label": "qwen3.8-flash", "config": "qwen3.8-flash", "stop_on_quota": True, "note": "阿里云百炼"},
    {"label": "dots3-note-prev", "config": "dots3-note-prev", "stop_on_quota": False, "note": "XHS 接口"},
]
JUDGE_MODEL = "deepseek-v4-flash"
MAX_ATTEMPTS = 5
SAVE_EVERY = 3
MAX_WORKERS = 8
PER_MODEL_WORKERS = 2
TASK_START_QPS = 0.5
RUN_TIMEOUT_S = 60 * 60
JUDGE_TIMEOUT_S = 40 * 60
QUOTA_MARKERS = ("arrearage", "quota", "insufficient_balance", "insufficient balance", "allocationquota",
                 "freetieronly", "overdue", "欠费", "余额", "额度", "billing")


class RateLimiter:
    def __init__(self, qps: float):
        self.interval = 1.0 / qps
        self.lock = threading.Lock()
        self.next_time = 0.0

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            wait = self.next_time - now
            self.next_time = max(now, self.next_time) + self.interval
        if wait > 0:
            time.sleep(wait)


def keep_awake():
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except json.JSONDecodeError:
        return None


class Pipeline:
    def __init__(self, run_root: Path, case_ids: list[str]):
        self.root = run_root
        self.case_ids = case_ids
        self.cases = {c["id"]: c for c in json.loads(QUERY.read_text(encoding="utf-8"))}
        self.lock = threading.Lock()
        self.state_file = run_root / "pipeline_state.json"
        self.state = load_json(self.state_file) or {"created": now(), "events": [], "tasks": {}, "models": {}}
        self.state["judge_model"] = JUDGE_MODEL
        self.state["case_ids"] = case_ids
        self.state["max_attempts"] = MAX_ATTEMPTS
        for m in MODELS:
            self.state["models"].setdefault(m["label"], {"exhausted": False})
            self.state["models"][m["label"]].update({"config": m["config"], "note": m["note"], "dir": f"{m['config']}_en"})
        self.model_sems = {m["label"]: threading.Semaphore(PER_MODEL_WORKERS) for m in MODELS}
        self.limiter = RateLimiter(TASK_START_QPS)
        self.finished_since_build = 0
        (run_root / "_cases").mkdir(parents=True, exist_ok=True)
        (run_root / "logs").mkdir(exist_ok=True)
        for cid in case_ids:
            (run_root / "_cases" / f"{cid}.json").write_text(json.dumps([self.cases[cid]], ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- state ----------
    def save(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_file)

    def event(self, model, cid, stage, level, msg):
        with self.lock:
            self.state["events"].append({"time": now(), "model": model, "case": cid, "stage": stage, "level": level, "msg": msg})
            self.save()
        print(f"[{now()}] {level.upper():5} {model:16} {cid or '-':16} {stage:10} {msg}", flush=True)

    def task(self, model, cid):
        key = f"{model}|{cid}"
        with self.lock:
            return self.state["tasks"].setdefault(key, {"model": model, "case": cid, "status": "pending", "stages": {}})

    def set_stage(self, model, cid, stage, **fields):
        with self.lock:
            t = self.state["tasks"][f"{model}|{cid}"]
            t["stages"].setdefault(stage, {}).update(fields)
            self.save()

    def set_task(self, model, cid, **fields):
        with self.lock:
            self.state["tasks"][f"{model}|{cid}"].update(fields)
            self.save()

    # ---------- helpers ----------
    def model_dir(self, m):
        return self.root / f"{m['config']}_en"

    def run_cmd(self, cmd, log_path: Path, timeout, cwd):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            log.write(f"$ {' '.join(map(str, cmd))}\n\n")
            log.flush()
            try:
                proc = subprocess.run(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, env=env)
                return proc.returncode
            except subprocess.TimeoutExpired:
                log.write(f"\n[pipeline] timeout after {timeout}s\n")
                return -9

    def quota_hit(self, text: str) -> bool:
        low = text.lower()
        return any(k in low for k in QUOTA_MARKERS)

    def backoff(self, attempt):
        time.sleep(min(300, 10 * 2 ** (attempt - 1)) + random.uniform(0, 3))

    # ---------- stage 1: Trip+ run ----------
    def check_run(self, m, cid):
        d = self.model_dir(m)
        errors = []
        for t in self.cases[cid]["turns"]:
            name = f"{cid}_turn_{t['turn_id']}"
            traj = load_json(d / "trajectories" / f"{name}.json")
            if not traj:
                errors.append(f"T{t['turn_id']} 无轨迹")
                continue
            if traj.get("inference_failed"):
                errors.append(f"T{t['turn_id']} 推理失败：{str(traj.get('error'))[:300]}")
                continue
            if not (d / "evaluation" / f"{name}_score.json").exists():
                errors.append(f"T{t['turn_id']} 无规则评分")
        return errors

    def stage_run(self, m, cid):
        label = m["label"]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.state["models"][label]["exhausted"]:
                return False
            self.set_stage(label, cid, "run", status="running", attempts=attempt, started=now())
            self.event(label, cid, "run", "info", f"第 {attempt} 次尝试：推理 + 转换 + 规则评估")
            log = self.root / "logs" / label / f"{cid}_run_attempt{attempt}.log"
            cmd = [PY, "run.py", "--model", m["config"], "--test-data", str(self.root / "_cases" / f"{cid}.json"),
                   "--output-dir", str(self.root), "--database-dir", str(DB_DIR), "--workers", "1", "--rerun-ids", cid]
            t0 = time.time()
            code = self.run_cmd(cmd, log, RUN_TIMEOUT_S, TRIP)
            errors = self.check_run(m, cid)
            if code == 0 and not errors:
                self.set_stage(label, cid, "run", status="ok", finished=now(), elapsed_s=round(time.time() - t0, 1))
                self.event(label, cid, "run", "ok", f"完成，用时 {time.time() - t0:.0f} 秒")
                return True
            detail = "；".join(errors) or f"进程退出码 {code}"
            log_text = log.read_text(encoding="utf-8", errors="replace")[-20000:]
            self.set_stage(label, cid, "run", status="retrying", last_error=detail[:1000], log=str(log.relative_to(self.root)))
            self.event(label, cid, "run", "warn", f"第 {attempt} 次失败：{detail[:300]}")
            if m["stop_on_quota"] and self.quota_hit(detail + log_text):
                with self.lock:
                    self.state["models"][label]["exhausted"] = True
                    self.state["models"][label]["exhausted_at"] = now()
                    self.state["models"][label]["exhausted_reason"] = detail[:500]
                self.event(label, cid, "run", "error", "疑似额度不足，停止该模型的后续 case")
                return False
            if attempt < MAX_ATTEMPTS:
                self.backoff(attempt)
        self.set_stage(label, cid, "run", status="failed", finished=now())
        self.event(label, cid, "run", "error", f"重试 {MAX_ATTEMPTS} 次仍失败，跳过该 case")
        return False

    # ---------- stage 2: Judge B ----------
    def stage_judge_b(self, m, cid):
        label, d = m["label"], self.model_dir(m)
        turns = [t["turn_id"] for t in self.cases[cid]["turns"]]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.set_stage(label, cid, "judge_b", status="running", attempts=attempt, started=now())
            self.event(label, cid, "judge_b", "info", f"第 {attempt} 次尝试：Judge B 评 {len(turns)} 轮")
            log = self.root / "logs" / label / f"{cid}_judge_b_attempt{attempt}.log"
            self.run_cmd([PY, str(HERE / "run_judge_b.py"), str(d), cid, "--model", JUDGE_MODEL, "--workers", "4"], log, JUDGE_TIMEOUT_S, HERE)
            missing = [n for n in turns if not load_json(d / "judge_b" / f"{cid}_turn_{n}.json")]
            if not missing:
                self.set_stage(label, cid, "judge_b", status="ok", finished=now())
                self.event(label, cid, "judge_b", "ok", "完成")
                return True
            self.set_stage(label, cid, "judge_b", status="retrying", last_error=f"缺少轮次 {missing}", log=str(log.relative_to(self.root)))
            self.event(label, cid, "judge_b", "warn", f"第 {attempt} 次后仍缺少轮次 {missing}")
            if attempt < MAX_ATTEMPTS:
                self.backoff(attempt)
        self.set_stage(label, cid, "judge_b", status="failed", finished=now())
        self.event(label, cid, "judge_b", "error", "重试后仍有轮次缺少 Judge B，已跳过")
        return False

    # ---------- stage 3: Judge A ----------
    def judge_a_file(self, m, cid):
        hits = sorted((self.model_dir(m) / "judge_a").glob(f"*/user_simulations/id_{cid}_turn_*_user_simulation.json"))
        return hits[-1] if hits else None

    def stage_judge_a(self, m, cid):
        label, d = m["label"], self.model_dir(m)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.set_stage(label, cid, "judge_a", status="running", attempts=attempt, started=now())
            self.event(label, cid, "judge_a", "info", f"第 {attempt} 次尝试：Judge A 模拟旅行者体验")
            log = self.root / "logs" / label / f"{cid}_judge_a_attempt{attempt}.log"
            cmd = [PY, "-m", "simulation.run_user_simulation", "--simulator-model", JUDGE_MODEL, "--result-dir", str(d),
                   "--query-file", str(QUERY), "--database-dir", str(DB_DIR), "--target-id", cid,
                   "--output-root", str(d / "judge_a"), "--workers", "1"]
            self.run_cmd(cmd, log, JUDGE_TIMEOUT_S, TRIP)
            if self.judge_a_file(m, cid):
                self.set_stage(label, cid, "judge_a", status="ok", finished=now())
                self.event(label, cid, "judge_a", "ok", "完成")
                return True
            tail = log.read_text(encoding="utf-8", errors="replace")[-400:]
            if "eligible final-turn plans: 0" in log.read_text(encoding="utf-8", errors="replace"):
                self.set_stage(label, cid, "judge_a", status="not_applicable", finished=now())
                self.event(label, cid, "judge_a", "info", "最后一轮没有可评的方案，跳过")
                return True
            self.set_stage(label, cid, "judge_a", status="retrying", last_error=tail, log=str(log.relative_to(self.root)))
            self.event(label, cid, "judge_a", "warn", f"第 {attempt} 次失败")
            if attempt < MAX_ATTEMPTS:
                self.backoff(attempt)
        self.set_stage(label, cid, "judge_a", status="failed", finished=now())
        self.event(label, cid, "judge_a", "error", "重试后仍失败，已跳过")
        return False

    # ---------- task ----------
    def run_task(self, m, cid):
        label = m["label"]
        t = self.task(label, cid)
        if t["status"] in ("done", "failed"):
            return
        with self.model_sems[label]:
            if self.state["models"][label]["exhausted"]:
                self.set_task(label, cid, status="skipped", reason="模型额度不足，已停止")
                self.event(label, cid, "task", "warn", "模型已停止，跳过")
                return
            self.limiter.acquire()
            self.set_task(label, cid, status="running", started=now())
            ok = self.stage_run(m, cid)
            if not ok:
                status = "skipped" if self.state["models"][label]["exhausted"] else "failed"
                self.set_task(label, cid, status=status, finished=now())
            else:
                jb = self.stage_judge_b(m, cid)
                ja = self.stage_judge_a(m, cid)
                self.set_task(label, cid, status="done" if (jb and ja) else "done_partial", finished=now())
        with self.lock:
            self.finished_since_build += 1
            rebuild = self.finished_since_build >= SAVE_EVERY
            if rebuild:
                self.finished_since_build = 0
        if rebuild:
            self.build_outputs("阶段性保存")

    def build_outputs(self, why):
        try:
            code = subprocess.run([PY, str(HERE / "run_viewer" / "build_run_viewer.py"), "--pipeline-root", str(self.root)],
                                  cwd=HERE / "run_viewer", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            msg = (code.stdout + code.stderr).strip().splitlines()[-1:] or [""]
            self.event("-", None, "report", "ok" if code.returncode == 0 else "warn", f"{why}：重建面板与排行榜 {msg[0][:200]}")
        except Exception as error:  # noqa: BLE001
            self.event("-", None, "report", "warn", f"{why}：重建失败 {error}")

    def run(self):
        keep_awake()
        self.state["started"] = self.state.get("started") or now()
        self.state["status"] = "running"
        self.save()
        self.event("-", None, "pipeline", "info", f"开始：{len(MODELS)} 个模型 × {len(self.case_ids)} 个 case，裁判 {JUDGE_MODEL}")
        jobs = [(m, cid) for cid in self.case_ids for m in MODELS]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(self.run_task, m, cid) for m, cid in jobs]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as error:  # noqa: BLE001
                    self.event("-", None, "pipeline", "error", f"任务异常：{error!r}")
        self.state["status"] = "finished"
        self.state["finished"] = now()
        self.save()
        self.event("-", None, "pipeline", "ok", "全部任务结束，开始聚合")
        self.build_outputs("最终聚合")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=6, help="use the first N cases of query_10.json")
    ap.add_argument("--run-name", default="pipeline_0925")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    case_ids = [c["id"] for c in json.loads(QUERY.read_text(encoding="utf-8"))][: args.cases]
    run_root = TRIP / "result" / args.run_name
    run_root.mkdir(parents=True, exist_ok=True)
    p = Pipeline(run_root, case_ids)
    if args.report_only:
        p.build_outputs("手动重建")
    else:
        p.run()


if __name__ == "__main__":
    main()
