"""Deterministic lease-deadline and self-deployment restart regressions."""

import io
import json
import logging
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from uuid import uuid4

import check_ai_task_local_runner as fixtures
from ai_task_api_client import ClaimedTask, RunnerAPIClient, RunnerAPIError
from ai_task_runner import LeaseHeartbeat, RunOutcome


class Clock:
    def __init__(self):
        self.seconds = 0
        self.origin = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def now(self):
        return self.origin + timedelta(seconds=self.seconds)

    def timestamp(self, seconds):
        return (self.origin + timedelta(seconds=seconds)).isoformat()


class HeartbeatTests(unittest.TestCase):
    def heartbeat(self, *, expires=100, interval=10, iterations=10):
        clock = Clock()
        task_id = uuid4()
        task = ClaimedTask(task_id, "test lease", f"ai/task/{task_id}", f"ai-task-{task_id}",
                           uuid4(), clock.timestamp(expires))
        client = Mock()
        on_lost = Mock()
        waits = []

        def wait(delay):
            if len(waits) == iterations:
                return True
            waits.append(delay)
            clock.seconds += delay
            return False

        heartbeat = LeaseHeartbeat(client, task, interval=interval, now=clock.now,
                                   wait=wait, on_lost=on_lost)
        return heartbeat, client, clock, waits, on_lost

    def test_more_than_three_transport_failures_recover_before_actual_expiry(self):
        heartbeat, client, clock, waits, lost = self.heartbeat(iterations=6)
        client.heartbeat.side_effect = [RunnerAPIError("connection refused")] * 5 + [
            {"task_id": str(heartbeat.task.task_id), "status": "running",
             "lease_expires_at": clock.timestamp(200)},
        ]
        heartbeat._run()
        self.assertEqual(client.heartbeat.call_count, 6)
        self.assertEqual(clock.seconds, 60)
        self.assertEqual(heartbeat.task.lease_expires_at, clock.timestamp(200))
        self.assertFalse(heartbeat.lost.is_set())
        lost.assert_not_called()

    def test_real_expiry_stops_at_deadline_not_after_three_failures(self):
        heartbeat, client, clock, waits, lost = self.heartbeat(expires=25)
        client.heartbeat.side_effect = RunnerAPIError("HTTP 503 restart", status_code=503)
        heartbeat._run()
        self.assertEqual(waits, [10, 10, 5])
        self.assertEqual(client.heartbeat.call_count, 2)
        self.assertEqual(clock.seconds, 25)
        self.assertTrue(heartbeat.lost.is_set())
        self.assertEqual(heartbeat.task.lease_expires_at, clock.timestamp(25))
        lost.assert_called_once()

    def test_explicit_identity_rejection_loses_even_unexpired_lease(self):
        for status in (401, 403, 409):
            with self.subTest(status=status):
                heartbeat, client, clock, waits, lost = self.heartbeat()
                client.heartbeat.side_effect = RunnerAPIError("claim rejected", status_code=status)
                heartbeat._run()
                self.assertEqual(clock.seconds, 10)
                self.assertTrue(heartbeat.lost.is_set())
                client.heartbeat.assert_called_once()
                lost.assert_called_once()

    def test_heartbeat_refresh_extends_deadline_from_root_response(self):
        heartbeat, client, clock, waits, lost = self.heartbeat(expires=25)
        client.heartbeat.side_effect = [
            {"lease_expires_at": clock.timestamp(55)},
            RunnerAPIError("temporary restart"), RunnerAPIError("temporary restart"),
            RunnerAPIError("temporary restart"), RunnerAPIError("temporary restart"),
        ]
        heartbeat._run()
        self.assertEqual(clock.seconds, 55)
        self.assertEqual(client.heartbeat.call_count, 5)
        self.assertEqual(heartbeat.task.lease_expires_at, clock.timestamp(55))
        lost.assert_called_once()

    def test_stale_inflight_heartbeat_does_not_shorten_deployment_reservation(self):
        heartbeat, client, clock, waits, lost = self.heartbeat(expires=60, interval=30, iterations=5)

        def older_response_arrives_after_reservation(*args):
            heartbeat.update_lease(clock.timestamp(900))
            return {"lease_expires_at": clock.timestamp(120)}

        client.heartbeat.side_effect = [
            {"lease_expires_at": clock.timestamp(120)},
            RunnerAPIError("admin restarting"), RunnerAPIError("admin restarting"),
            RunnerAPIError("admin restarting"), RunnerAPIError("admin restarting"),
        ]
        # Install the reservation while the earlier heartbeat request is in flight.
        first = True
        later = client.heartbeat.side_effect
        def respond(*args):
            nonlocal first
            if first:
                first = False
                return older_response_arrives_after_reservation(*args)
            next(later) if client.heartbeat.call_count == 2 else None
            raise RunnerAPIError("admin restarting")
        client.heartbeat.side_effect = respond
        heartbeat._run()
        self.assertEqual(clock.seconds, 150)
        self.assertEqual(heartbeat.task.lease_expires_at, clock.timestamp(900))
        self.assertFalse(heartbeat.lost.is_set())
        lost.assert_not_called()

    def test_missing_or_invalid_timestamp_does_not_invent_an_extension(self):
        for response in ({"status": "running"}, {"lease_expires_at": "not-a-date"},
                         {"lease_expires_at": "2030-01-01T00:10:00"}):
            with self.subTest(response=response):
                heartbeat, client, clock, waits, lost = self.heartbeat(expires=25)
                client.heartbeat.return_value = response
                heartbeat._run()
                self.assertEqual(clock.seconds, 25)
                self.assertEqual(heartbeat.task.lease_expires_at, clock.timestamp(25))
                lost.assert_called_once()

    def test_stopping_heartbeat_does_not_revoke_valid_lease(self):
        heartbeat, client, clock, waits, lost = self.heartbeat()
        heartbeat.stop_event.set()
        heartbeat._run()
        client.heartbeat.assert_not_called()
        lost.assert_not_called()
        self.assertFalse(heartbeat.lost.is_set())


class LeaseClientTests(unittest.TestCase):
    def test_http_status_and_redacted_diagnostics_survive_client_wrapping(self):
        for status in (401, 403, 409, 500, 503):
            with self.subTest(status=status):
                body = io.BytesIO(b'{"detail":"backend unavailable test-api-secret"}')
                failure = HTTPError("https://runner.example.test/heartbeat", status, "failure", {}, body)
                client = RunnerAPIClient("https://runner.example.test", "test-api-secret", "runner-1",
                                         requester=Mock(side_effect=failure))
                with self.assertRaises(RunnerAPIError) as raised:
                    client.heartbeat(uuid4(), uuid4())
                self.assertEqual(raised.exception.status_code, status)
                self.assertIn("backend unavailable", str(raised.exception))
                self.assertNotIn("test-api-secret", str(raised.exception))
                self.assertTrue(body.closed)

    def test_transport_error_has_no_identity_rejection_status(self):
        client = RunnerAPIClient("https://runner.example.test", "test-api-secret", "runner-1",
                                 requester=Mock(side_effect=URLError("connection refused")))
        with self.assertRaises(RunnerAPIError) as raised:
            client.heartbeat(uuid4(), uuid4())
        self.assertIsNone(raised.exception.status_code)
        self.assertIn("connection refused", str(raised.exception))

    def test_heartbeat_returns_root_lease_timestamp(self):
        response = {"task_id": str(uuid4()), "status": "running", "lease_expires_at": Clock().timestamp(180)}
        client = RunnerAPIClient("https://runner.example.test", "test-api-secret", "runner-1",
                                 requester=Mock(return_value=json.dumps(response).encode()))
        self.assertEqual(client.heartbeat(uuid4(), uuid4()), response)


class DeploymentLeaseTests(unittest.TestCase):
    def test_deployment_reservation_is_observed_before_admin_restart_and_success(self):
        fixture = fixtures.RunnerTests(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        clock = Clock()
        fixture.task = replace(fixture.task, lease_expires_at=clock.timestamp(60))
        fixture.client.task = fixture.task
        runner = fixture.make_runner()
        waits = []
        def wait(delay):
            if len(waits) == 5:
                return True
            waits.append(delay)
            clock.seconds += delay
            return False
        lost = Mock()
        heartbeat = LeaseHeartbeat(fixture.client, fixture.task, interval=30,
                                   now=clock.now, wait=wait, on_lost=lost)
        heartbeat.start = Mock()
        heartbeat.stop = Mock()
        runner.heartbeat_factory = Mock(return_value=heartbeat)
        fixture.client.heartbeat = Mock(side_effect=[RunnerAPIError("admin restarting")] * 4 + [
            {"status": "deploying", "lease_expires_at": clock.timestamp(900)},
        ])
        original = fixture.client.mark_deploying
        def reserve(*args, **kwargs):
            original(*args, **kwargs)
            return {"task_id": str(fixture.task.task_id), "status": "deploying",
                    "lease_expires_at": clock.timestamp(900)}
        fixture.client.mark_deploying = reserve
        def deploy(sha, **kwargs):
            fixture.events.append("deploy")
            self.assertEqual(heartbeat.task.lease_expires_at, clock.timestamp(900))
            heartbeat._run()
            self.assertGreater(clock.seconds, 60)
            self.assertFalse(kwargs["stop_event"].is_set())
            return SimpleNamespace(deployed_commit_sha=sha, summary="SUCCESS")
        runner.deployer.deploy = deploy
        self.assertEqual(runner.run_once(), RunOutcome.SUCCESS)
        self.assertEqual(fixture.codex_calls, 1)
        self.assertEqual(fixture.events.count("merge"), 1)
        self.assertEqual(fixture.events.count("deploy"), 1)
        self.assertEqual(fixture.client.calls[-1][0], "completed")
        lost.assert_not_called()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
