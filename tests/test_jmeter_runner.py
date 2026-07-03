from unittest.mock import patch

from agent.jmeter_runner import JMeterRunner


class FakeProcess:
    returncode = 0

    def poll(self):
        return 0


def _run_with_patches(runner, script_path, result_dir, args):
    with patch.object(runner, "_inject_thread_config", return_value=str(script_path)) as inject, \
         patch.object(runner, "_parse_final_result", return_value={"total_samples": 0}) as parse, \
         patch.object(runner, "_generate_report") as report, \
         patch("agent.jmeter_runner.subprocess.Popen", return_value=FakeProcess()) as popen:
        runner.execute(
            script_path=str(script_path),
            result_dir=str(result_dir),
            jmeter_args=args,
            timeout=10,
        )
    return inject, parse, report, popen


def test_jmeter_heap_uses_heap_env_and_skips_internal_args(tmp_path):
    script_path = tmp_path / "test.jmx"
    script_path.write_text("<jmeterTestPlan></jmeterTestPlan>", encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")

    _, _, _, popen = _run_with_patches(
        runner,
        script_path,
        tmp_path / "result",
        {
            "threads": "1",
            "duration": "1",
            "jvm_heap_mb": "2048",
            "capture_error_log": "false",
        },
    )

    cmd = popen.call_args.args[0]
    env = popen.call_args.kwargs["env"]
    assert env["HEAP"] == "-Xms2048m -Xmx2048m"
    assert all("jvm_heap_mb" not in part for part in cmd)
    assert all("capture_error_log" not in part for part in cmd)


def test_capture_error_log_controls_error_capture(tmp_path):
    script_path = tmp_path / "test.jmx"
    script_path.write_text("<jmeterTestPlan></jmeterTestPlan>", encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")

    inject, _, _, popen = _run_with_patches(
        runner,
        script_path,
        tmp_path / "result",
        {"threads": "1", "duration": "1", "capture_error_log": "false"},
    )
    cmd = popen.call_args.args[0]
    assert inject.call_args.kwargs["error_data_path"] is None
    assert "-Jjmeter.save.saveservice.samplerData=true" not in cmd
    assert "-Jjmeter.save.saveservice.requestHeaders=true" not in cmd
    assert "-Jjmeter.save.saveservice.responseHeaders=true" not in cmd

    inject, _, _, popen = _run_with_patches(
        runner,
        script_path,
        tmp_path / "result2",
        {"threads": "1", "duration": "1", "capture_error_log": "true"},
    )
    cmd = popen.call_args.args[0]
    assert inject.call_args.kwargs["error_data_path"].endswith("error_responses.jsonl")
    assert "-Jjmeter.save.saveservice.samplerData=true" in cmd


def test_report_generation_uses_reportgenerator_properties(tmp_path):
    runner = JMeterRunner("/opt/jmeter")
    jtl_path = tmp_path / "result.xml"
    report_path = tmp_path / "html-report"

    with patch("agent.jmeter_runner.subprocess.run") as run:
        run.return_value.returncode = 0
        runner._generate_report(
            str(jtl_path),
            str(report_path),
            {
                "jmeter.reportgenerator.overall_granularity": "30000",
                "httpclient4.retrycount": "0",
            },
        )

    cmd = run.call_args.args[0]
    assert "-Jjmeter.reportgenerator.overall_granularity=30000" in cmd
    assert all("httpclient4.retrycount" not in part for part in cmd)
