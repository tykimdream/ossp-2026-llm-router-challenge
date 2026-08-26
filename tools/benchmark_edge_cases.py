# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Benchmark two guarded submission routers on generated edge cases."""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
MODEL_IDS = ("ax31-light", "ax31", "axk1-think")
TIERS = ("fast", "balanced", "premium")
OUTPUT_LIMIT_BYTES = 4 * 1024 * 1024
ROUTERS = {
    "v4": ROOT / "src/ossp_router/resources/risk-router.v4.json",
    "v5": ROOT / "src/ossp_router/resources/aggressive-router.v5.json",
    "v6": None,
    "v7": None,
    "v9": None,
}


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(_json_text(value), encoding="utf-8")
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    return int(value if sys.platform == "darwin" else value * 1024)


def _worker(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from ossp_router import submission
    from ossp_router.heuristic import write_submission_atomic
    from ossp_router.protocol import load_bundled_policy, load_input

    started = time.perf_counter()
    inputs = load_input(args.worker_input)
    policy = load_bundled_policy()
    if args.worker_router == "v6":
        from ossp_router.aggressive_v6 import load_artifact

        artifact = load_artifact()
    elif args.worker_router == "v7":
        from ossp_router.aggressive_v7 import load_artifact

        artifact = load_artifact()
    elif args.worker_router == "v9":
        from ossp_router.aggressive_v9 import load_artifact

        artifact = load_artifact()
    else:
        artifact = submission.load_submission_artifact(ROUTERS[args.worker_router])
    routed = submission.make_submission(inputs, policy, artifact, args.worker_tier)
    write_submission_atomic(args.worker_output, routed)
    metrics = {
        "worker_seconds": time.perf_counter() - started,
        "peak_rss_bytes": _peak_rss_bytes(),
    }
    _write_json(args.worker_metrics, metrics)
    return 0


def _record_key(record: Mapping[str, Any]) -> Tuple[str, str, str]:
    return str(record["case"]), str(record["router"]), str(record["tier"])


def _record_ok(record: Mapping[str, Any]) -> bool:
    return bool(
        not record.get("timed_out")
        and record.get("return_code") == 0
        and record.get("submission_valid")
        and record.get("output_limit_passed")
    )


def _read_output(path: Path, expected_ids: Sequence[str]) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        decisions = value["decisions"]
        ids = [item["episode_id"] for item in decisions]
        models = [item["model_id"] for item in decisions]
        valid = (
            len(ids) == len(expected_ids)
            and len(set(ids)) == len(ids)
            and set(ids) == set(expected_ids)
            and all(model in MODEL_IDS for model in models)
        )
        counts = Counter(models)
        return {
            "valid": valid,
            "model_counts": {model: counts.get(model, 0) for model in MODEL_IDS},
            "decisions": {item["episode_id"]: item["model_id"] for item in decisions},
        }
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        return {
            "valid": False,
            "model_counts": {model: 0 for model in MODEL_IDS},
            "decisions": {},
        }


def _run_one(
    *,
    case: Mapping[str, Any],
    cases_dir: Path,
    work_dir: Path,
    router: str,
    tier: str,
    timeout_seconds: float,
) -> Dict[str, Any]:
    input_path = cases_dir / str(case["file"])
    input_value = json.loads(input_path.read_text(encoding="utf-8"))
    expected_ids = [item["episode_id"] for item in input_value["episodes"]]
    stem = f"{case['name']}.{router}.{tier}"
    output_path = work_dir / f"{stem}.submission.json"
    metrics_path = work_dir / f"{stem}.metrics.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--worker-input",
        str(input_path),
        "--worker-router",
        router,
        "--worker-tier",
        tier,
        "--worker-output",
        str(output_path),
        "--worker-metrics",
        str(metrics_path),
    ]
    started = time.perf_counter()
    timed_out = False
    return_code: Optional[int] = None
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    wall_seconds = time.perf_counter() - started
    output_bytes = output_path.stat().st_size if output_path.is_file() else 0
    parsed = _read_output(output_path, expected_ids)
    metrics: Mapping[str, Any] = {}
    if metrics_path.is_file():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    return {
        "case": case["name"],
        "router": router,
        "tier": tier,
        "episodes": case["episodes"],
        "prompt_characters": case["prompt_characters"],
        "expected_router_path": case["expected_router_path"],
        "wall_seconds": wall_seconds,
        "worker_seconds": metrics.get("worker_seconds"),
        "peak_rss_bytes": metrics.get("peak_rss_bytes"),
        "timed_out": timed_out,
        "return_code": return_code,
        "output_bytes": output_bytes,
        "output_limit_passed": output_bytes <= OUTPUT_LIMIT_BYTES,
        "submission_valid": parsed["valid"],
        "model_counts": parsed["model_counts"],
        "decision_map": parsed["decisions"],
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "stderr_tail": stderr[-500:],
    }


def _compact_counts(counts: Mapping[str, Any]) -> str:
    return f"{counts['ax31-light']}/{counts['ax31']}/{counts['axk1-think']}"


def _fmt_seconds(value: Any) -> str:
    return "TIMEOUT" if value is None else f"{float(value):.3f}"


def _saved_decisions(
    record: Mapping[str, Any], work_dir: Path
) -> Mapping[str, str]:
    path = work_dir / (
        f"{record['case']}.{record['router']}.{record['tier']}.submission.json"
    )
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {
            item["episode_id"]: item["model_id"] for item in value["decisions"]
        }
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        return {}


def _comparison_rows(
    records: Sequence[Mapping[str, Any]],
    work_dir: Path,
    baseline_router: str,
    candidate_router: str,
) -> List[Dict[str, Any]]:
    by_key = {_record_key(record): record for record in records}
    rows = []
    cases = sorted({str(record["case"]) for record in records})
    for case in cases:
        for tier in TIERS:
            baseline = by_key.get((case, baseline_router, tier))
            candidate = by_key.get((case, candidate_router, tier))
            if baseline is None or candidate is None:
                continue
            left = _saved_decisions(baseline, work_dir)
            right = _saved_decisions(candidate, work_dir)
            mismatches = None
            if baseline["submission_valid"] and candidate["submission_valid"]:
                mismatches = sum(left[item] != right[item] for item in left)
            rows.append(
                {
                    "case": case,
                    "tier": tier,
                    "episodes": baseline["episodes"],
                    "path": baseline["expected_router_path"],
                    "baseline_seconds": (
                        baseline["wall_seconds"] if not baseline["timed_out"] else None
                    ),
                    "candidate_seconds": (
                        candidate["wall_seconds"] if not candidate["timed_out"] else None
                    ),
                    "baseline_peak_rss_bytes": baseline["peak_rss_bytes"],
                    "candidate_peak_rss_bytes": candidate["peak_rss_bytes"],
                    "baseline_counts": baseline["model_counts"],
                    "candidate_counts": candidate["model_counts"],
                    "mismatches": mismatches,
                    "baseline_output_bytes": baseline["output_bytes"],
                    "candidate_output_bytes": candidate["output_bytes"],
                    "baseline_ok": (
                        not baseline["timed_out"]
                        and baseline["return_code"] == 0
                        and baseline["submission_valid"]
                        and baseline["output_limit_passed"]
                    ),
                    "candidate_ok": (
                        not candidate["timed_out"]
                        and candidate["return_code"] == 0
                        and candidate["submission_valid"]
                        and candidate["output_limit_passed"]
                    ),
                }
            )
    return rows


def _markdown(report: Mapping[str, Any]) -> str:
    baseline = str(report["baseline_router"]).upper()
    candidate = str(report["candidate_router"]).upper()
    lines = [
        "<!--",
        "SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.",
        "SPDX-License-Identifier: Apache-2.0",
        "-->",
        "",
        f"# {baseline}/{candidate} 엣지케이스 벤치마크",
        "",
        f"- 플랫폼: `{report['platform']}` / Python `{report['python']}`",
        f"- timeout: 실행당 `{report['timeout_seconds']}`초",
        "- 범위: 별도 Python 프로세스 시작, 입력 파싱, 라우팅, JSON 쓰기 포함",
        "- 모델 수 표기: `Light/AX31/K1`",
        "- 시간은 로컬 참고값이며 공식 ARM64 컨테이너 측정값이 아님",
        f"- 경로 열은 manifest의 guard 분류이며 {candidate} guard fallback은 Always-Light",
        "",
        "## 요약",
        "",
        "| Router | 성공/실행 | Timeout | 잘못된 출력 | 4 MiB 초과 | 합계 초 | 최대 RSS MiB |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for router in (report["baseline_router"], report["candidate_router"]):
        item = report["summary"][router]
        lines.append(
            "| {router} | {success}/{runs} | {timeouts} | {invalid} | {over} | {seconds:.3f} | {rss:.1f} |".format(
                router=str(router).upper(),
                success=item["successes"],
                runs=item["runs"],
                timeouts=item["timeouts"],
                invalid=item["invalid_submissions"],
                over=item["output_limit_failures"],
                seconds=item["total_wall_seconds"],
                rss=item["max_peak_rss_bytes"] / (1024 * 1024),
            )
        )
    lines.extend(
        [
            "",
            "## 전체 측정",
            "",
        f"| 케이스 | Tier | 경로 | {baseline} 초 | {candidate} 초 | {baseline} L/A/K | {candidate} L/A/K | 불일치 | {baseline}/{candidate} 판정 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in report["comparisons"]:
        status = (
            f"{'OK' if row['baseline_ok'] else 'FAIL'}/"
            f"{'OK' if row['candidate_ok'] else 'FAIL'}"
        )
        mismatch = "-" if row["mismatches"] is None else str(row["mismatches"])
        lines.append(
            "| {case} | {tier} | {path} | {left} | {right} | {leftc} | {rightc} | {mismatch} | {status} |".format(
                case=row["case"],
                tier=row["tier"],
                path=row["path"],
                left=_fmt_seconds(row["baseline_seconds"]),
                right=_fmt_seconds(row["candidate_seconds"]),
                leftc=_compact_counts(row["baseline_counts"]),
                rightc=_compact_counts(row["candidate_counts"]),
                mismatch=mismatch,
                status=status,
            )
        )
    lines.extend(
        [
            "",
            "`FAIL`은 timeout, 비정상 종료, 잘못된 submission 또는 공식 4 MiB 출력 한도 초과 중 하나입니다.",
            "합성 입력에는 실제 outcome이 없으므로 이 보고서의 모델 수는 실제 비용 통과를 보장하지 않습니다.",
            "",
        ]
    )
    return "\n".join(lines)


def _summary(
    records: Sequence[Mapping[str, Any]], routers: Sequence[str]
) -> Mapping[str, Mapping[str, Any]]:
    result = {}
    for router in routers:
        selected = [record for record in records if record["router"] == router]
        result[router] = {
            "runs": len(selected),
            "successes": sum(_record_ok(record) for record in selected),
            "timeouts": sum(bool(record["timed_out"]) for record in selected),
            "invalid_submissions": sum(
                not bool(record["submission_valid"]) for record in selected
            ),
            "output_limit_failures": sum(
                not bool(record["output_limit_passed"]) for record in selected
            ),
            "total_wall_seconds": sum(
                float(record["wall_seconds"]) for record in selected
            ),
            "max_peak_rss_bytes": max(
                (int(record["peak_rss_bytes"] or 0) for record in selected),
                default=0,
            ),
        }
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=ROOT / "build/edge-cases")
    parser.add_argument(
        "--report-json",
        type=Path,
        default=ROOT / "experiments/results/edge-cases-v4-v5.json",
    )
    parser.add_argument(
        "--report-markdown",
        type=Path,
        default=ROOT / "docs/EDGE_CASE_RESULTS.md",
    )
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument(
        "--routers",
        nargs=2,
        choices=tuple(ROUTERS),
        default=("v4", "v5"),
        metavar=("BASELINE", "CANDIDATE"),
    )
    parser.add_argument("--case", action="append", default=[], dest="cases")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--rerun-router",
        action="append",
        default=[],
        choices=tuple(ROUTERS),
        help="with --resume, discard and rerun this router's saved records",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-input", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-router", choices=tuple(ROUTERS), help=argparse.SUPPRESS)
    parser.add_argument("--worker-tier", choices=TIERS, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-metrics", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.worker:
        return _worker(args)
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    baseline_router, candidate_router = args.routers
    if baseline_router == candidate_router:
        raise SystemExit("--routers must name two different versions")
    manifest_path = args.cases_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    if args.cases:
        wanted = set(args.cases)
        known = {item["name"] for item in cases}
        unknown = sorted(wanted - known)
        if unknown:
            raise SystemExit(f"unknown cases: {', '.join(unknown)}")
        cases = [item for item in cases if item["name"] in wanted]

    records: List[Dict[str, Any]] = []
    if args.resume and args.report_json.is_file():
        old = json.loads(args.report_json.read_text(encoding="utf-8"))
        selected = {baseline_router, candidate_router}
        rerun = set(args.rerun_router)
        records = [
            record
            for record in old.get("records", [])
            if (
                record.get("router") in selected
                and record.get("router") not in rerun
                and _record_ok(record)
            )
        ]
    completed = {_record_key(record) for record in records}
    work_dir = args.cases_dir / f"benchmark-{baseline_router}-{candidate_router}"

    def save() -> None:
        public_records = [
            {key: value for key, value in record.items() if key != "decision_map"}
            for record in records
        ]
        report = {
            "report_type": "ossp-edge-case-router-comparison-v2",
            "baseline_router": baseline_router,
            "candidate_router": candidate_router,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "timeout_seconds": args.timeout_seconds,
            "output_limit_bytes": OUTPUT_LIMIT_BYTES,
            "manifest": _display_path(manifest_path),
            "records": public_records,
            "summary": _summary(records, (baseline_router, candidate_router)),
            "comparisons": _comparison_rows(
                records, work_dir, baseline_router, candidate_router
            ),
        }
        _write_json(args.report_json, report)
        args.report_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.report_markdown.write_text(_markdown(report), encoding="utf-8")

    for case in cases:
        for router in (baseline_router, candidate_router):
            for tier in TIERS:
                key = (str(case["name"]), router, tier)
                if key in completed:
                    continue
                print(f"RUN {case['name']} {router} {tier}", flush=True)
                record = _run_one(
                    case=case,
                    cases_dir=args.cases_dir,
                    work_dir=work_dir,
                    router=router,
                    tier=tier,
                    timeout_seconds=args.timeout_seconds,
                )
                records.append(record)
                completed.add(key)
                save()
    save()
    print(f"OK: {args.report_json} / {args.report_markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
