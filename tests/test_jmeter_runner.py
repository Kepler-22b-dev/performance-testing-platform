import json
import os
from unittest.mock import patch
import xml.etree.ElementTree as ET

from agent.jmeter_runner import JMeterRunner


class FakeProcess:
    returncode = 0

    def poll(self):
        return 0


class FailedProcess:
    returncode = 1

    def poll(self):
        return 1


class RunningThenCompletedProcess:
    returncode = 0

    def __init__(self):
        self.poll_count = 0

    def poll(self):
        self.poll_count += 1
        return None if self.poll_count == 1 else 0


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
    assert popen.call_args.kwargs["start_new_session"] is True
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
    assert inject.call_args.kwargs["error_sample_limit"] == 100
    assert inject.call_args.kwargs["error_max_body_chars"] == 8192
    assert "-Jjmeter.save.saveservice.samplerData=true" not in cmd


def test_jmeter_defaults_to_csv_jtl_result(tmp_path):
    script_path = tmp_path / "test.jmx"
    script_path.write_text("<jmeterTestPlan></jmeterTestPlan>", encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")

    _, _, report, popen = _run_with_patches(
        runner,
        script_path,
        tmp_path / "result",
        {"threads": "1", "duration": "1", "capture_error_log": "false"},
    )

    cmd = popen.call_args.args[0]
    assert "-Jjmeter.save.saveservice.output_format=csv" in cmd
    assert str(tmp_path / "result" / "result.jtl") in cmd
    assert report.call_args.args[0].endswith("result.jtl")


def test_jmeter_xml_result_format_is_debug_option(tmp_path):
    script_path = tmp_path / "test.jmx"
    script_path.write_text("<jmeterTestPlan></jmeterTestPlan>", encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")

    _, _, report, popen = _run_with_patches(
        runner,
        script_path,
        tmp_path / "result",
        {"threads": "1", "duration": "1", "result_format": "xml", "capture_error_log": "false"},
    )

    cmd = popen.call_args.args[0]
    assert "-Jjmeter.save.saveservice.output_format=xml" in cmd
    assert str(tmp_path / "result" / "result.xml") in cmd
    assert report.call_args.args[0].endswith("result.xml")


def test_distributed_run_skips_unavailable_remote_hosts(tmp_path):
    script_path = tmp_path / "test.jmx"
    script_path.write_text("<jmeterTestPlan></jmeterTestPlan>", encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")

    with patch.object(runner, "_is_remote_host_available", side_effect=lambda host: host.endswith(":1101")), \
         patch.object(runner, "_parse_final_result", return_value={"total_samples": 0}), \
         patch.object(runner, "_generate_report"), \
         patch("agent.jmeter_runner.subprocess.Popen", return_value=FakeProcess()) as popen:
        result = runner.execute(
            script_path=str(script_path),
            result_dir=str(tmp_path / "result"),
            jmeter_args={"capture_error_log": "false"},
            distributed=True,
            remote_hosts="192.168.31.178:1101,192.168.31.178:1100",
            timeout=10,
        )

    cmd = popen.call_args.args[0]
    assert result["status"] == "completed"
    assert cmd[cmd.index("-R") + 1] == "192.168.31.178:1101"
    assert all("192.168.31.178:1100" not in part for part in cmd)
    assert "192.168.31.178:1100" in result["warnings"][0]
    assert result["summary"]["execution_warnings"] == result["warnings"]


def test_distributed_run_falls_back_to_local_when_all_remote_hosts_unavailable(tmp_path):
    script_path = tmp_path / "test.jmx"
    script_path.write_text("<jmeterTestPlan></jmeterTestPlan>", encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")

    with patch.object(runner, "_is_remote_host_available", return_value=False), \
         patch.object(runner, "_parse_final_result", return_value={"total_samples": 0}), \
         patch.object(runner, "_generate_report"), \
         patch("agent.jmeter_runner.subprocess.Popen", return_value=FakeProcess()) as popen:
        result = runner.execute(
            script_path=str(script_path),
            result_dir=str(tmp_path / "result"),
            jmeter_args={"capture_error_log": "false"},
            distributed=True,
            remote_hosts="192.168.31.178:1101,192.168.31.178:1100",
            timeout=10,
        )

    cmd = popen.call_args.args[0]
    assert result["status"] == "completed"
    assert "-R" not in cmd
    assert "-r" not in cmd
    assert any("全部不可用" in warning for warning in result["warnings"])
    assert result["summary"]["execution_warnings"] == result["warnings"]


def test_nonzero_jmeter_exit_with_samples_is_failed(tmp_path):
    script_path = tmp_path / "test.jmx"
    script_path.write_text("<jmeterTestPlan></jmeterTestPlan>", encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")
    summary = {"total_samples": 12, "error_count": 0}

    with patch.object(runner, "_inject_thread_config", return_value=str(script_path)), \
         patch.object(runner, "_parse_final_result", return_value=summary), \
         patch.object(runner, "_generate_report") as report, \
         patch("agent.jmeter_runner.subprocess.Popen", return_value=FailedProcess()), \
         patch("agent.jmeter_runner.time.sleep"):
        result = runner.execute(
            script_path=str(script_path),
            result_dir=str(tmp_path / "result"),
            jmeter_args={"threads": "1", "duration": "1"},
            timeout=10,
        )

    assert result["status"] == "failed"
    assert result["error"] == "JMeter exit code 1"
    assert result["summary"] == summary
    report.assert_not_called()


def test_progress_callback_failure_does_not_fail_jmeter_run(tmp_path):
    script_path = tmp_path / "test.jmx"
    script_path.write_text("<jmeterTestPlan></jmeterTestPlan>", encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")
    summary = {"total_samples": 12, "error_count": 0}
    clock = {"now": 1000.0}

    def fake_time():
        clock["now"] += 1.1
        return clock["now"]

    def on_progress(_progress):
        raise RuntimeError("redis publish failed")

    with patch.object(runner, "_inject_thread_config", return_value=str(script_path)), \
         patch.object(runner, "_parse_progress", return_value={"total_samples": 1}), \
         patch.object(runner, "_parse_final_result", return_value=summary), \
         patch.object(runner, "_generate_report") as report, \
         patch("agent.jmeter_runner.subprocess.Popen", return_value=RunningThenCompletedProcess()), \
         patch("agent.jmeter_runner.time.time", side_effect=fake_time), \
         patch("agent.jmeter_runner.time.sleep"):
        result = runner.execute(
            script_path=str(script_path),
            result_dir=str(tmp_path / "result"),
            jmeter_args={"threads": "1", "duration": "1"},
            on_progress=on_progress,
            timeout=10,
        )

    assert result["status"] == "completed"
    assert result["summary"] == summary
    assert runner._progress_callback_error_count == 1
    report.assert_called_once()


def test_parse_csv_jtl_handles_quoted_commas(tmp_path):
    jtl_path = tmp_path / "result.jtl"
    jtl_path.write_text(
        "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,bytes,sentBytes,URL,Latency,Connect\n"
        "1700000000000,100,\"api, list\",200,\"OK, cached\",tg,true,120,30,http://example.test,80,5\n"
        "1700000001000,200,\"api, list\",500,\"ERR, backend\",tg,false,180,40,http://example.test,150,8\n",
        encoding="utf-8",
    )
    runner = JMeterRunner("/opt/jmeter")

    summary = runner._parse_final_result(str(jtl_path))

    assert summary["total_samples"] == 2
    assert summary["error_count"] == 1
    assert summary["total_bytes_received"] == 300
    assert summary["total_bytes_sent"] == 70
    assert summary["response_code_dist"] == {"200": 1, "500": 1}


def test_final_result_parser_caps_percentile_samples(tmp_path):
    jtl_path = tmp_path / "large.jtl"
    rows = [
        "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,bytes,sentBytes,URL,Latency,Connect\n"
    ]
    elapsed_sum = 0
    for index in range(10005):
        elapsed = index % 2000 + 1
        elapsed_sum += elapsed
        rows.append(
            f"{1700000000000 + index},{elapsed},api,200,OK,tg,true,10,2,http://example.test,{elapsed},1\n"
        )
    jtl_path.write_text("".join(rows), encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")

    with patch.dict(os.environ, {"JMETER_RESULT_SAMPLE_LIMIT": "10000"}):
        summary = runner._parse_final_result(str(jtl_path))

    assert summary["total_samples"] == 10005
    assert summary["avg_response_time"] == round(elapsed_sum / 10005, 2)
    assert summary["percentiles_approximate"] is True
    assert summary["percentile_sample_size"] == 10000


def test_parse_progress_caps_recent_elapsed_times(tmp_path):
    jtl_path = tmp_path / "result.jtl"
    rows = [
        "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,bytes,sentBytes,URL,Latency,Connect\n"
    ]
    for index in range(1000):
        rows.append(
            f"{1700000000000 + index},{index + 1},api,200,OK,tg,true,10,2,http://example.test,1,1\n"
        )
    jtl_path.write_text("".join(rows), encoding="utf-8")
    runner = JMeterRunner("/opt/jmeter")

    progress = runner._parse_progress(str(jtl_path))

    assert progress["total_samples"] == 1000
    assert len(progress["elapsed_times"]) == 500
    assert progress["elapsed_times"][0] == 501
    assert progress["elapsed_times"][-1] == 1000


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
                "jvm_heap_mb": "2048",
            },
        )

    cmd = run.call_args.args[0]
    assert "-Jjmeter.reportgenerator.overall_granularity=30000" in cmd
    assert all("httpclient4.retrycount" not in part for part in cmd)
    assert run.call_args.kwargs["env"]["HEAP"] == "-Xms2048m -Xmx2048m"
    assert "capture_output" not in run.call_args.kwargs


def test_image_resource_loader_is_injected_for_image_scenario(tmp_path):
    script_path = tmp_path / "image-test.jmx"
    script_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Test Plan">
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments"/>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Thread Group">
        <intProp name="ThreadGroup.num_threads">1</intProp>
        <intProp name="ThreadGroup.ramp_time">1</intProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController">
          <intProp name="LoopController.loops">1</intProp>
        </elementProp>
      </ThreadGroup>
      <hashTree>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="API">
          <stringProp name="HTTPSampler.domain">example.com</stringProp>
          <stringProp name="HTTPSampler.path">/api</stringProp>
        </HTTPSamplerProxy>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
""",
        encoding="utf-8",
    )
    runner = JMeterRunner("/opt/jmeter")

    modified = runner._inject_thread_config(
        str(script_path),
        threads=5,
        ramp_time=2,
        duration=30,
        scenario={
            "type": "image-load",
            "resource_load": {
                "enabled": True,
                "max_images_per_response": 3,
                "timeout_ms": 3000,
            },
        },
    )

    ET.parse(modified)
    text = open(modified, encoding="utf-8").read()
    assert "Image Resource Loader" in text
    assert "final int maxImages = 3" in text
    assert "prev.addSubResult(sample)" in text


def test_error_response_capture_uses_sample_label_api(tmp_path):
    script_path = tmp_path / "error-capture-test.jmx"
    script_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Test Plan">
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments"/>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Thread Group">
        <intProp name="ThreadGroup.num_threads">1</intProp>
        <intProp name="ThreadGroup.ramp_time">1</intProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController">
          <intProp name="LoopController.loops">1</intProp>
        </elementProp>
      </ThreadGroup>
      <hashTree>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="API">
          <stringProp name="HTTPSampler.domain">example.com</stringProp>
          <stringProp name="HTTPSampler.path">/api</stringProp>
        </HTTPSamplerProxy>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
""",
        encoding="utf-8",
    )
    runner = JMeterRunner("/opt/jmeter")

    modified = runner._inject_thread_config(
        str(script_path),
        threads=5,
        ramp_time=2,
        duration=30,
        error_data_path=str(tmp_path / "error_responses.jsonl"),
    )

    ET.parse(modified)
    text = open(modified, encoding="utf-8").read()
    assert "Error Response Capture" in text
    assert "prev.getSampleLabel()" in text
    assert "prev.getLabel()" not in text


def test_thread_config_injection_updates_string_props(tmp_path):
    script_path = tmp_path / "string-props.jmx"
    script_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Test Plan"/>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Thread Group">
        <stringProp name="ThreadGroup.num_threads">5</stringProp>
        <stringProp name="ThreadGroup.ramp_time">1</stringProp>
        <boolProp name="ThreadGroup.scheduler">false</boolProp>
        <stringProp name="ThreadGroup.duration">15</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController">
          <intProp name="LoopController.loops">1</intProp>
        </elementProp>
      </ThreadGroup>
      <hashTree/>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
""",
        encoding="utf-8",
    )
    runner = JMeterRunner("/opt/jmeter")

    modified = runner._inject_thread_config(
        str(script_path),
        threads=1500,
        ramp_time=5,
        duration=60,
    )

    root = ET.parse(modified).getroot()
    assert root.find(".//stringProp[@name='ThreadGroup.num_threads']").text == "1500"
    assert root.find(".//stringProp[@name='ThreadGroup.ramp_time']").text == "5"
    assert root.find(".//stringProp[@name='ThreadGroup.duration']").text == "60"
    assert root.find(".//boolProp[@name='ThreadGroup.scheduler']").text == "true"
    assert root.find(".//*[@name='LoopController.loops']").text == "-1"


def test_image_resource_loader_enables_subresults_output(tmp_path):
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
            "scenario": json.dumps({
                "type": "image-load",
                "resource_load": {"enabled": True},
            }),
        },
    )

    cmd = popen.call_args.args[0]
    assert "-Jjmeter.save.saveservice.subresults=true" in cmd
