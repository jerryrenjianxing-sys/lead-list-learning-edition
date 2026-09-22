"""Run the distributed Skill from a fresh directory against a real local host."""

import argparse
import importlib.util
import json
import os
import subprocess
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--instance", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "skill_client", root / "skills/media-workbench/scripts/workbench.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
args.instance = str(Path(args.instance).resolve())
client = module.Client(args.instance)
out = Path(args.output).resolve()
out.mkdir(parents=True, exist_ok=True)
archive = out / "Skill.zip"
archive.write_bytes(client.send("GET", "/api/v1/skill"))
with zipfile.ZipFile(archive) as z:
    z.extractall(out / "fresh-agent")
script = out / "fresh-agent/media-workbench/scripts/workbench.py"
python = client.instance["python"]


def run(*arguments):
    result = subprocess.run(
        [python, str(script), "--instance", args.instance, *arguments],
        cwd=out,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        encoding="utf-8",
        capture_output=True,
        timeout=45,
    )
    if result.returncode:
        raise AssertionError(result.stdout + "\n" + result.stderr)
    return json.loads(result.stdout)


def call(method, path, body=None, key=None):
    arguments = ["api", method, "/api/v1" + path]
    if body is not None:
        (out / "request.json").write_text(
            json.dumps(body, ensure_ascii=False), encoding="utf-8"
        )
        arguments += ["--json-file", str(out / "request.json")]
    if key:
        arguments += ["--request-id", key]
    return run(*arguments)


health = run("check")
assert health["product"] == "MediaWorkbench"
dataset = call("POST", "/demo", {})
examples = [
    ("需求归纳", {"自由字段": "希望显示无障碍车辆信息", "建议": "增加车辆服务信息"}),
    ("证据分类", {"自定类别": "亲身体验", "另一个字段": ["通勤", "拥挤"]}),
    (
        "任意报告",
        {"结论正文": "这是合成示例；所有输入均已检查。", "自定义指标": {"数量": 6}},
    ),
]
report = []
for i, (name, payload) in enumerate(examples):
    a = call(
        "POST",
        "/analyses",
        {
            "dataset_id": dataset["id"],
            "name": name,
            "goal": "客户端与数据完整性验收：" + name,
        },
    )
    inputs = call("GET", f"/analyses/{a['id']}/inputs")["items"]
    batch = {
        "batch_id": "first",
        "processed_ids": [r["id"] for r in inputs[:3]],
        "results": [{"payload": payload, "evidence_ids": [inputs[i]["id"]]}],
    }
    key = "smoke-" + a["id"]
    first = call("POST", f"/analyses/{a['id']}/batches", batch, key)
    assert call("POST", f"/analyses/{a['id']}/batches", batch, key) == first
    # Each invocation is a new process with no prior in-memory client state.
    pending = call("GET", f"/analyses/{a['id']}/inputs?state=pending")["items"]
    assert len(pending) == 3
    call(
        "POST",
        f"/analyses/{a['id']}/batches",
        {"batch_id": "resumed", "processed_ids": [r["id"] for r in pending]},
    )
    assert call("POST", f"/analyses/{a['id']}/finish", {})["state"] == "completed"
    for format in ["json", "xlsx", "docx", "csv", "md"]:
        artifact = call("POST", f"/analyses/{a['id']}/export", {"format": format})
        run("download", artifact["download_url"], str(out / f"{i}.{format}"))
    assert len(call("GET", f"/analyses/{a['id']}/results")["items"]) == 1
    report.append({"analysis_id": a["id"], "name": name, "processed": 6, "formats": 5})
(out / "client-results.json").write_text(
    json.dumps(
        {
            "ok": True,
            "product": health["product"],
            "python": python,
            "scenarios": report,
            "note": "Deterministic client verification; not an independent model reasoning evaluation.",
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print(
    json.dumps(
        {"ok": True, "scenarios": len(report), "exports": 15, "output": str(out)},
        ensure_ascii=False,
    )
)
