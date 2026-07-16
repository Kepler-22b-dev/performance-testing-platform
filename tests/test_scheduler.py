import time
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import redis

from common.protocol import CommandType, ProgressUpdate, TaskCommand, TaskResult, TaskStatus
from manager.core.scheduler import TaskScheduler
from tests.conftest import make_running_task, make_task, make_completed_result


def _build_scheduler():
    with patch("manager.core.scheduler.redis.Redis") as mock_redis_cls:
        mock_redis = MagicMock()
        mock_redis.incr.return_value = 1
        mock_redis_cls.return_value = mock_redis
        s = TaskScheduler()
    return s


class FakeNodeManager:
    def __init__(self, agents=None):
        self.agents = {
            agent.agent_id: agent
            for agent in (agents or [])
        }

    def get_agent(self, agent_id):
        return self.agents.get(agent_id)

    def get_available_agents(self):
        return [agent for agent in self.agents.values() if agent.status == "online"]


class TestCreateTaskValidation:
    def test_generate_task_id_uses_redis_incr(self):
        s = _build_scheduler()
        s.redis.incr.return_value = 42
        with patch("manager.core.scheduler.time.strftime", return_value="20260704"):
            assert s._generate_task_id() == "task-20260704-042"
        s.redis.incr.assert_called_once_with("jmeter:task_seq:20260704")
        s.redis.expire.assert_not_called()

    def test_generate_task_id_sets_expiry_on_first_use(self):
        s = _build_scheduler()
        s.redis.incr.return_value = 1
        with patch("manager.core.scheduler.time.strftime", return_value="20260704"):
            assert s._generate_task_id() == "task-20260704-001"
        s.redis.expire.assert_called_once_with("jmeter:task_seq:20260704", 86400 * 2)

    def test_generate_task_id_fallback_on_redis_error(self, tmp_path):
        s = _build_scheduler()
        s.redis.incr.side_effect = redis.ConnectionError("connection refused")
        existing = [
            make_task(task_id="task-20260704-001"),
            make_task(task_id="task-20260704-003"),
        ]
        with patch("manager.core.scheduler.time.strftime", return_value="20260704"), \
             patch("manager.core.scheduler.REPORTS_DIR", str(tmp_path)), \
             patch("manager.core.scheduler.db_get_all_tasks", return_value=existing):
            assert s._generate_task_id(MagicMock()) == "task-20260704-004"

    def test_empty_agents_raises(self):
        s = _build_scheduler()
        try:
            s.create_task(script_id="x", target_agents=[], jmeter_args={})
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "至少需要" in str(e)

    def test_negative_timeout_raises(self):
        s = _build_scheduler()
        try:
            s.create_task(script_id="x", target_agents=["a-1"], jmeter_args={}, timeout=-1)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "超时时间" in str(e)

    def test_zero_timeout_raises(self):
        s = _build_scheduler()
        try:
            s.create_task(script_id="x", target_agents=["a-1"], jmeter_args={}, timeout=0)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "超时时间" in str(e)

    def test_concurrent_limit_reached(self):
        s = _build_scheduler()
        running_tasks = [make_running_task(task_id=f"task-{i}") for i in range(3)]
        with patch("manager.core.scheduler.db_get_running_tasks", return_value=running_tasks), \
             patch("manager.core.scheduler.get_sync_db"), \
             patch("manager.core.scheduler.os.path.exists", return_value=True):
            try:
                s.create_task(script_id="x", target_agents=["a-1"], jmeter_args={})
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "并发" in str(e)

    def test_target_agent_running_conflict(self):
        s = _build_scheduler()
        running_tasks = [
            make_task(task_id="task-old", status=TaskStatus.RUNNING, target_agents=["a-1"])
        ]
        with patch("manager.core.scheduler.db_get_running_tasks", return_value=running_tasks), \
             patch("manager.core.scheduler.get_sync_db"), \
             patch("manager.core.scheduler.os.path.exists", return_value=True):
            try:
                s.create_task(script_id="x", target_agents=["a-1"], jmeter_args={})
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "压力机已有运行中的任务" in str(e)
                assert "a-1" in str(e)

    def test_remote_slave_running_conflict(self):
        s = _build_scheduler()
        running_tasks = [
            make_task(
                task_id="task-old",
                status=TaskStatus.RUNNING,
                target_agents=["agent-old"],
                remote_hosts="agent-a.example.internal:1100,agent-b.example.internal:1100",
            )
        ]
        with patch("manager.core.scheduler.db_get_running_tasks", return_value=running_tasks), \
             patch("manager.core.scheduler.get_sync_db"), \
             patch("manager.core.scheduler.os.path.exists", return_value=True):
            try:
                s.create_task(
                    script_id="x",
                    target_agents=["agent-new"],
                    jmeter_args={},
                    distributed=True,
                    remote_hosts="agent-a.example.internal:1100",
                )
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "agent-a.example.internal:1100" in str(e)

    def test_single_agent_guard_can_be_disabled(self):
        s = _build_scheduler()
        running_tasks = [
            make_task(task_id="task-old", status=TaskStatus.RUNNING, target_agents=["a-1"])
        ]
        mock_db = MagicMock()
        with patch("manager.core.scheduler.db_get_running_tasks", return_value=running_tasks), \
             patch("manager.core.scheduler.get_vars_dict", return_value={}), \
             patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.os.path.exists", side_effect=lambda path: str(path).endswith("x.jmx")), \
             patch("manager.core.scheduler.db_get_all_tasks", return_value=[]), \
             patch("manager.core.scheduler.db_create_task") as mock_create:
            task_id = s.create_task(
                script_id="x",
                target_agents=["a-1"],
                jmeter_args={},
                enforce_single_agent_task=False,
            )
            assert task_id.startswith("task-")
            mock_create.assert_called_once()

    def test_global_variables_are_snapshotted_and_task_args_override(self):
        s = _build_scheduler()
        mock_db = MagicMock()
        with patch("manager.core.scheduler.get_vars_dict", return_value={
            "base_url": "https://test.example.com",
            "region": "cn-test",
            "threads": "999",
        }), \
             patch("manager.core.scheduler.db_get_running_tasks", return_value=[]), \
             patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.os.path.exists", side_effect=lambda path: str(path).endswith("x.jmx")), \
             patch("manager.core.scheduler.db_get_all_tasks", return_value=[]), \
             patch("manager.core.scheduler.db_create_task") as mock_create:
            s.create_task(
                script_id="x",
                target_agents=["a-1"],
                jmeter_args={"base_url": "https://override.example.com", "threads": "10"},
            )

        args = mock_create.call_args.args[1]["jmeter_args"]
        assert args == {
            "base_url": "https://override.example.com",
            "region": "cn-test",
            "threads": "10",
        }


class TestCleanupStuckTasks:
    def test_no_results_timeout_marks_failed(self):
        s = _build_scheduler()
        task = make_running_task(elapsed=4000, timeout=3600, results={})
        mock_db = MagicMock()

        with patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.db_get_running_tasks", return_value=[task]), \
             patch("manager.core.scheduler.db_update_task") as mock_update:
            s._cleanup_stuck_tasks()
            mock_update.assert_called_once()
            args = mock_update.call_args
            assert args[0][1] == "task-test0001"
            assert args[1]["status"] == "failed"
            assert "未收到任何 Agent 响应" in args[1]["error_message"]

    def test_partial_results_timeout_marks_failed(self):
        s = _build_scheduler()
        results = {
            "agent-001": make_completed_result("agent-001"),
            "agent-002": {"agent_id": "agent-002", "status": "running",
                          "start_time": time.time() - 100, "end_time": None,
                          "report_path": None, "error_message": None, "summary": {}},
        }
        task = make_running_task(elapsed=4000, timeout=3600, results=results)
        mock_db = MagicMock()

        with patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.db_get_running_tasks", return_value=[task]), \
             patch("manager.core.scheduler.db_update_task") as mock_update:
            s._cleanup_stuck_tasks()
            mock_update.assert_called_once()
            args = mock_update.call_args
            assert "1/2" in args[1]["error_message"]

    def test_within_timeout_not_cleaned(self):
        s = _build_scheduler()
        task = make_running_task(elapsed=100, timeout=3600)
        mock_db = MagicMock()

        with patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.db_get_running_tasks", return_value=[task]), \
             patch("manager.core.scheduler.db_update_task") as mock_update:
            s._cleanup_stuck_tasks()
            mock_update.assert_not_called()

    def test_uses_task_timeout_not_default(self):
        s = _build_scheduler()
        task = make_running_task(elapsed=200, timeout=100)
        mock_db = MagicMock()

        with patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.db_get_running_tasks", return_value=[task]), \
             patch("manager.core.scheduler.db_update_task") as mock_update:
            s._cleanup_stuck_tasks()
            mock_update.assert_called_once()

    def test_multiple_tasks_mixed(self):
        s = _build_scheduler()
        stuck = make_running_task(task_id="task-stuck", elapsed=5000, timeout=3600, results={})
        ok = make_running_task(task_id="task-ok", elapsed=100, timeout=3600, results={})
        mock_db = MagicMock()

        with patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.db_get_running_tasks", return_value=[stuck, ok]), \
             patch("manager.core.scheduler.db_update_task") as mock_update:
            s._cleanup_stuck_tasks()
            assert mock_update.call_count == 1
            assert mock_update.call_args[0][1] == "task-stuck"


class TestStartTaskScriptCheck:
    def test_missing_script_marks_failed(self):
        s = _build_scheduler()
        task = make_task(status="pending")
        mock_db = MagicMock()

        with patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.db_get_task", return_value=task), \
             patch("manager.core.scheduler.os.path.exists", return_value=False), \
             patch("manager.core.scheduler.db_update_task") as mock_update:
            result = s.start_task("task-test0001")
            assert result is False
            mock_update.assert_called_once()
            assert "脚本文件不存在" in mock_update.call_args[1]["error_message"]

    def test_unavailable_target_agent_marks_failed(self):
        node_manager = FakeNodeManager([
            SimpleNamespace(agent_id="agent-current", status="online"),
        ])
        with patch("manager.core.scheduler.redis.Redis"):
            s = TaskScheduler(node_manager=node_manager)
        task = make_task(
            status=TaskStatus.PENDING,
            target_agents=["agent-stale"],
        )
        mock_db = MagicMock()

        with patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.db_get_task", return_value=task), \
             patch("manager.core.scheduler.db_update_task") as mock_update:
            result = s.start_task(task["task_id"])

        assert result is False
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs["status"] == TaskStatus.FAILED
        assert "agent-stale 未在线" in mock_update.call_args.kwargs["error_message"]
        s.redis.xadd.assert_not_called()

    def test_rerun_uses_available_agent_when_original_target_is_stale(self):
        node_manager = FakeNodeManager([
            SimpleNamespace(agent_id="agent-current", status="online"),
        ])
        with patch("manager.core.scheduler.redis.Redis"):
            s = TaskScheduler(node_manager=node_manager)
        old_task = make_task(
            status=TaskStatus.COMPLETED,
            target_agents=["agent-stale"],
        )

        with patch.object(s, "get_task", return_value=old_task), \
             patch.object(s, "create_task", return_value="task-new") as mock_create, \
             patch.object(s, "start_task", return_value=True) as mock_start:
            result = s.rerun_task(old_task["task_id"])

        assert result == "task-new"
        assert mock_create.call_args.kwargs["target_agents"] == ["agent-current"]
        mock_start.assert_called_once_with("task-new")


class TestTaskCommandTargeting:
    def _extract_command_payloads(self, scheduler):
        """从 redis.xadd 调用中提取命令 payload。"""
        payloads = []
        for call in scheduler.redis.xadd.call_args_list:
            if call.args and call.args[0] == "jmeter:command":
                fields = call.args[1] if len(call.args) > 1 else call.kwargs.get("field_map", {})
                if "data" in fields:
                    payloads.append(fields["data"])
        return [TaskCommand.from_json(p) for p in payloads]

    def test_start_task_targets_each_agent(self):
        s = _build_scheduler()
        task = make_task(
            status=TaskStatus.PENDING,
            target_agents=["agent-001", "agent-002"],
            csv_file="data.csv",
        )
        mock_db = MagicMock()

        with patch("manager.core.scheduler.get_sync_db", return_value=mock_db), \
             patch("manager.core.scheduler.db_get_task", return_value=task), \
             patch("manager.core.scheduler.db_update_task"):
            result = s.start_task(task["task_id"])

        assert result is True
        commands = self._extract_command_payloads(s)
        assert [c.target_agent_id for c in commands] == ["agent-001", "agent-002"]

    def test_adjust_load_targets_each_running_agent(self):
        s = _build_scheduler()
        task = make_task(
            status=TaskStatus.RUNNING,
            target_agents=["agent-001", "agent-002"],
        )

        with patch("manager.core.scheduler.db_get_task", return_value=task), \
             patch("manager.core.scheduler.get_sync_db"):
            result = s.adjust_load(
                task["task_id"],
                action="increase",
                threads=20,
                ramp_time=5,
                duration=120,
            )

        assert result["segment_id"].startswith("dyn-")
        commands = self._extract_command_payloads(s)
        assert [c.command for c in commands] == [CommandType.ADJUST_LOAD, CommandType.ADJUST_LOAD]
        assert [c.target_agent_id for c in commands] == ["agent-001", "agent-002"]
        assert all(c.jmeter_args["action"] == "increase" for c in commands)
        assert all(c.jmeter_args["threads"] == "20" for c in commands)

    def test_stop_task_targets_each_agent(self):
        s = _build_scheduler()
        task = make_task(
            status=TaskStatus.RUNNING,
            target_agents=["agent-001", "agent-002"],
        )

        with patch("manager.core.scheduler.db_get_task", return_value=task), \
             patch("manager.core.scheduler.get_sync_db"):
            result = s.stop_task(task["task_id"])

        assert result is True
        commands = self._extract_command_payloads(s)
        assert [c.target_agent_id for c in commands] == ["agent-001", "agent-002"]


class TestHandleResultFinalization:
    def test_partial_multi_agent_result_does_not_finish_task(self):
        s = _build_scheduler()
        task_id = "task-multi"
        first_result = make_completed_result("agent-001")
        task_before = make_task(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            target_agents=["agent-001", "agent-002"],
            results={},
        )
        partial_task = make_task(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            target_agents=["agent-001", "agent-002"],
            results={"agent-001": first_result},
        )

        with patch("manager.core.scheduler.get_sync_db", return_value=MagicMock()), \
             patch("manager.core.scheduler.db_get_task", side_effect=[task_before, partial_task]), \
             patch("manager.core.scheduler.db_add_task_result"), \
             patch("manager.core.scheduler.db_update_task") as mock_update:
            s.handle_result(TaskResult(
                task_id=task_id,
                agent_id="agent-001",
                status=TaskStatus.COMPLETED,
                start_time=first_result["start_time"],
                end_time=first_result["end_time"],
                summary=first_result["summary"],
            ))

        mock_update.assert_not_called()

    def test_multi_agent_result_finishes_after_all_targets_report(self):
        s = _build_scheduler()
        task_id = "task-multi"
        first_result = make_completed_result("agent-001")
        second_result = make_completed_result("agent-002")
        task_before = make_task(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            target_agents=["agent-001", "agent-002"],
            results={"agent-001": first_result},
        )
        completed_task = make_task(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            target_agents=["agent-001", "agent-002"],
            results={"agent-001": first_result, "agent-002": second_result},
        )

        with patch("manager.core.scheduler.get_sync_db", return_value=MagicMock()), \
             patch("manager.core.scheduler.db_get_task", side_effect=[task_before, completed_task]), \
             patch("manager.core.scheduler.db_add_task_result"), \
             patch("manager.core.scheduler.db_update_task") as mock_update:
            s.handle_result(TaskResult(
                task_id=task_id,
                agent_id="agent-002",
                status=TaskStatus.COMPLETED,
                start_time=second_result["start_time"],
                end_time=second_result["end_time"],
                summary=second_result["summary"],
            ))

        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs["status"] == TaskStatus.COMPLETED


class TestProgressAggregation:
    def test_handle_progress_aggregates_dynamic_segments(self):
        s = _build_scheduler()

        s.handle_progress(ProgressUpdate(
            task_id="task-agg",
            agent_id="agent-001",
            timestamp=1,
            elapsed=10,
            active_threads=10,
            throughput=100,
            avg_response_time=100,
            error_rate=10,
            success_rate=90,
            total_samples=100,
            bytes_received=1000,
            avg_latency=80,
            avg_connect_time=5,
            segment_id="base",
        ))
        s.handle_progress(ProgressUpdate(
            task_id="task-agg",
            agent_id="agent-001",
            timestamp=2,
            elapsed=5,
            active_threads=5,
            throughput=50,
            avg_response_time=200,
            error_rate=0,
            success_rate=100,
            total_samples=50,
            bytes_received=500,
            avg_latency=160,
            avg_connect_time=10,
            segment_id="dyn-001",
        ))

        progress = s.get_progress("task-agg")
        assert progress.segment_id == "aggregate"
        assert progress.active_threads == 15
        assert progress.throughput == 150
        assert progress.total_samples == 150
        assert progress.bytes_received == 1500
        assert progress.avg_response_time == 133.33
        assert progress.error_rate == 6.67
        assert progress.success_rate == 93.33

        history = s.get_progress_history("task-agg")
        assert history[-1]["segment_id"] == "aggregate"
        assert history[-1]["active_threads"] == 15
