import json
from unittest.mock import patch
import xml.etree.ElementTree as ET

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
