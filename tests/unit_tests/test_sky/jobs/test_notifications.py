"""Unit tests for managed job notifications."""
import json
from unittest import mock

import pytest

from sky import Task
from sky.jobs import notifications


class TestNotificationHandlers:
    """Test notification handler implementations."""

    def setup_method(self):
        """Set up test fixtures."""
        self.job_context = {
            'job_id': 123,
            'task_id': 1,
            'task_name': 'test-task',
            'cluster_name': 'test-cluster',
            'status': 'SUCCEEDED',
            'skypilot_task_id': 'sky-test-id',
            'skypilot_task_ids': 'sky-test-ids',
        }

    def test_slack_handler_message_building(self):
        """Test Slack message payload construction."""
        handler = notifications.SlackNotificationHandler()
        config = {
            'webhook_url': 'https://hooks.slack.com/test',
            'username': 'TestBot',
            'channel': '#test-channel',
        }

        message = handler._build_message('SUCCEEDED', self.job_context, config)

        assert message['username'] == 'TestBot'
        assert message['channel'] == '#test-channel'
        assert len(message['attachments']) == 1

        attachment = message['attachments'][0]
        assert attachment['color'] == '#36a64f'  # Green for SUCCEEDED
        assert len(attachment['blocks']) == 2

        # Check header block
        header = attachment['blocks'][0]
        assert header['type'] == 'header'
        assert '✅' in header['text']['text']  # Success emoji
        assert 'test-task' in header['text']['text']

        # Check fields block
        fields_block = attachment['blocks'][1]
        assert fields_block['type'] == 'section'
        assert len(fields_block['fields']) == 4

        # Verify field contents
        field_texts = [field['text'] for field in fields_block['fields']]
        assert any('SUCCEEDED' in text for text in field_texts)
        assert any('123' in text for text in field_texts)
        assert any('test-task' in text for text in field_texts)
        assert any('test-cluster' in text for text in field_texts)

    def test_discord_handler_message_building(self):
        """Test Discord message payload construction."""
        handler = notifications.DiscordNotificationHandler()
        config = {
            'webhook_url': 'https://discord.com/api/webhooks/test',
            'username': 'TestBot',
        }

        message = handler._build_message('FAILED', self.job_context, config)

        assert message['username'] == 'TestBot'
        assert len(message['embeds']) == 1

        embed = message['embeds'][0]
        assert '❌' in embed['title']  # Failed emoji
        assert embed['color'] == 0xff0000  # Red for FAILED
        assert len(embed['fields']) == 4

        # Verify field contents
        fields = embed['fields']
        status_field = next(f for f in fields if f['name'] == 'Status')
        assert 'FAILED' in status_field['value']

        job_id_field = next(f for f in fields if f['name'] == 'Job ID')
        assert '123' in job_id_field['value']

    def test_status_emoji_and_colors(self):
        """Test that status emojis and colors are correctly mapped."""
        test_cases = [
            ('STARTING', '🚀', '#36a64f'),
            ('RUNNING', '⚡', '#ffaa00'),
            ('SUCCEEDED', '✅', '#36a64f'),
            ('FAILED', '❌', '#ff0000'),
            ('RECOVERING', '🔄', '#ffaa00'),
            ('CANCELLED', '⛔', '#808080'),
            ('UNKNOWN', 'ℹ️', '#0099cc'),  # Default values
        ]

        slack_handler = notifications.SlackNotificationHandler()
        discord_handler = notifications.DiscordNotificationHandler()

        for status, expected_emoji, expected_color in test_cases:
            config = {'webhook_url': 'https://test.com'}

            # Test Slack
            slack_msg = slack_handler._build_message(status, self.job_context,
                                                     config)
            assert expected_emoji in slack_msg['attachments'][0]['blocks'][0][
                'text']['text']
            assert slack_msg['attachments'][0]['color'] == expected_color

            # Test Discord
            discord_msg = discord_handler._build_message(
                status, self.job_context, config)
            assert expected_emoji in discord_msg['embeds'][0]['title']
            expected_color_int = int(expected_color.replace('#', ''), 16)
            assert discord_msg['embeds'][0]['color'] == expected_color_int

    @mock.patch('sky.jobs.notifications.log_lib.run_bash_command_with_log')
    def test_successful_webhook_sending(self, mock_run_command):
        """Test successful webhook sending."""
        mock_run_command.return_value = 0  # Success

        handler = notifications.SlackNotificationHandler()
        config = {'webhook_url': 'https://hooks.slack.com/test'}

        result = handler.send_notification('SUCCEEDED', self.job_context,
                                           config)

        assert result is True
        mock_run_command.assert_called_once()

        # Verify curl command construction
        call_args = mock_run_command.call_args
        bash_command = call_args[1]['bash_command']
        assert 'curl -X POST' in bash_command
        assert 'https://hooks.slack.com/test' in bash_command
        assert '--silent --fail' in bash_command

    @mock.patch('sky.jobs.notifications.log_lib.run_bash_command_with_log')
    def test_failed_webhook_sending(self, mock_run_command):
        """Test failed webhook sending."""
        mock_run_command.return_value = 1  # Failure

        handler = notifications.SlackNotificationHandler()
        config = {'webhook_url': 'https://hooks.slack.com/test'}

        result = handler.send_notification('FAILED', self.job_context, config)

        assert result is False
        mock_run_command.assert_called_once()

    @mock.patch('sky.jobs.notifications.log_lib.run_bash_command_with_log')
    def test_webhook_exception_handling(self, mock_run_command):
        """Test webhook exception handling."""
        mock_run_command.side_effect = Exception('Network error')

        handler = notifications.DiscordNotificationHandler()
        config = {'webhook_url': 'https://discord.com/api/webhooks/test'}

        result = handler.send_notification('RUNNING', self.job_context, config)

        assert result is False
        mock_run_command.assert_called_once()

    def test_missing_webhook_url(self):
        """Test handling of missing webhook URL."""
        handler = notifications.SlackNotificationHandler()
        config = {}  # No webhook_url

        result = handler.send_notification('SUCCEEDED', self.job_context,
                                           config)

        assert result is False

    def test_notify_on_filtering(self):
        """Test status filtering with notify_on parameter."""
        handler = notifications.SlackNotificationHandler()
        config = {
            'webhook_url': 'https://hooks.slack.com/test',
            'notify_on': ['FAILED', 'SUCCEEDED']
        }

        # Should skip RUNNING status
        result = handler.send_notification('RUNNING', self.job_context, config)
        assert result is True

        # Should process FAILED status
        with mock.patch(
                'sky.jobs.notifications.log_lib.run_bash_command_with_log'
        ) as mock_run:
            mock_run.return_value = 0
            result = handler.send_notification('FAILED', self.job_context,
                                               config)
            assert result is True
            mock_run.assert_called_once()

    def test_notify_on_empty_list(self):
        """Test that empty notify_on list processes all statuses."""
        handler = notifications.SlackNotificationHandler()
        config = {
            'webhook_url': 'https://hooks.slack.com/test',
            'notify_on': []
        }

        with mock.patch(
                'sky.jobs.notifications.log_lib.run_bash_command_with_log'
        ) as mock_run:
            mock_run.return_value = 0
            result = handler.send_notification('RUNNING', self.job_context,
                                               config)
            assert result is True
            mock_run.assert_called_once()


class TestCustomMessages:
    """Test custom message template functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.job_context = {
            'job_id': 999,
            'task_id': 5,
            'task_name': 'custom-test',
            'cluster_name': 'test-cluster',
            'status': 'SUCCEEDED',
            'skypilot_task_id': 'sky-custom-id',
            'skypilot_task_ids': 'sky-custom-ids',
        }

    def test_custom_message_template_formatting(self):
        """Test custom message template variable substitution."""
        handler = notifications.SlackNotificationHandler()

        # Test both {VAR} and $VAR syntax
        template1 = "{EMOJI} Job {TASK_NAME} is {JOB_STATUS} on {CLUSTER_NAME} (ID: {JOB_ID})"
        template2 = "$EMOJI Job $TASK_NAME is $JOB_STATUS on $CLUSTER_NAME (ID: $JOB_ID)"

        result1 = handler._format_custom_text(template1, 'SUCCEEDED',
                                              self.job_context)
        result2 = handler._format_custom_text(template2, 'SUCCEEDED',
                                              self.job_context)

        expected = "✅ Job custom-test is SUCCEEDED on test-cluster (ID: 999)"
        assert result1 == expected
        assert result2 == expected

    def test_slack_custom_message_format(self):
        """Test Slack handler with custom message."""
        handler = notifications.SlackNotificationHandler()
        config = {
            'webhook_url': 'https://hooks.slack.com/test',
            'username': 'TestBot',
            'message': '{EMOJI} Custom notification: {TASK_NAME} → {JOB_STATUS}'
        }

        message = handler._build_message('FAILED', self.job_context, config)

        assert 'text' in message
        assert message['text'] == '❌ Custom notification: custom-test → FAILED'
        assert 'attachments' not in message  # Simple text format
        assert message['username'] == 'TestBot'

    def test_discord_custom_message_format(self):
        """Test Discord handler with custom message."""
        handler = notifications.DiscordNotificationHandler()
        config = {
            'webhook_url': 'https://discord.com/api/webhooks/test',
            'message': '{EMOJI} {TASK_NAME} status: {JOB_STATUS}'
        }

        message = handler._build_message('SUCCEEDED', self.job_context, config)

        assert 'content' in message
        assert message['content'] == '✅ custom-test status: SUCCEEDED'
        assert 'embeds' not in message  # Simple text format

    def test_variable_edge_cases(self):
        """Test edge cases in variable substitution."""
        handler = notifications.SlackNotificationHandler()

        # Test with missing variables (should remain unchanged)
        template = "Status: {JOB_STATUS}, Unknown: {UNKNOWN_VAR}"
        result = handler._format_custom_text(template, 'RUNNING',
                                             self.job_context)
        assert result == "Status: RUNNING, Unknown: {UNKNOWN_VAR}"

        # Test with empty template
        result = handler._format_custom_text("", 'FAILED', self.job_context)
        assert result == ""

        # Test with no variables
        template = "Static message with no variables"
        result = handler._format_custom_text(template, 'SUCCEEDED',
                                             self.job_context)
        assert result == "Static message with no variables"

    def test_all_available_variables(self):
        """Test that all documented variables are available."""
        handler = notifications.SlackNotificationHandler()

        template = "Status:{JOB_STATUS} ID:{JOB_ID} TaskID:{TASK_ID} Name:{TASK_NAME} Cluster:{CLUSTER_NAME} Emoji:{EMOJI}"
        result = handler._format_custom_text(template, 'RECOVERING',
                                             self.job_context)

        expected = "Status:RECOVERING ID:999 TaskID:5 Name:custom-test Cluster:test-cluster Emoji:🔄"
        assert result == expected


class TestNotificationSending:
    """Test notification sending orchestration."""

    def setup_method(self):
        """Set up test fixtures."""
        self.job_context = {
            'job_id': 456,
            'task_id': 2,
            'task_name': 'orchestration-test',
            'cluster_name': 'test-cluster-2',
            'status': 'RUNNING',
            'skypilot_task_id': 'sky-test-id-2',
            'skypilot_task_ids': 'sky-test-ids-2',
        }

    def test_send_single_notification_success(self):
        """Test sending single notification successfully."""
        mock_handler = mock.Mock()
        mock_handler.send_notification.return_value = True

        with mock.patch.dict('sky.jobs.notifications.NOTIFICATION_HANDLERS',
                             {'slack': mock_handler}):
            config = {'slack': {'webhook_url': 'https://test.com'}}

            # Should not raise exception
            notifications._send_single_notification(config, 'RUNNING',
                                                    self.job_context)

            mock_handler.send_notification.assert_called_once_with(
                'RUNNING', self.job_context,
                {'webhook_url': 'https://test.com'})

    def test_send_single_notification_failure(self):
        """Test sending single notification with failure."""
        mock_handler = mock.Mock()
        mock_handler.send_notification.return_value = False

        with mock.patch.dict('sky.jobs.notifications.NOTIFICATION_HANDLERS',
                             {'discord': mock_handler}):
            config = {'discord': {'webhook_url': 'https://discord.test'}}

            # Should not raise exception even on failure
            notifications._send_single_notification(config, 'FAILED',
                                                    self.job_context)

            mock_handler.send_notification.assert_called_once()

    def test_send_single_notification_exception(self):
        """Test sending single notification with exception."""
        mock_handler = mock.Mock()
        mock_handler.send_notification.side_effect = Exception('Handler error')

        with mock.patch.dict('sky.jobs.notifications.NOTIFICATION_HANDLERS',
                             {'slack': mock_handler}):
            config = {'slack': {'webhook_url': 'https://test.com'}}

            # Should not raise exception even on handler exception
            notifications._send_single_notification(config, 'SUCCEEDED',
                                                    self.job_context)

            mock_handler.send_notification.assert_called_once()

    @mock.patch('sky.jobs.notifications._send_single_notification')
    def test_send_multiple_notifications(self, mock_send_single):
        """Test sending multiple notifications."""
        notifications_config = [
            {
                'slack': {
                    'webhook_url': 'https://slack.test'
                }
            },
            {
                'discord': {
                    'webhook_url': 'https://discord.test'
                }
            },
        ]

        notifications.send_notifications(notifications_config, 'SUCCEEDED',
                                         self.job_context)

        assert mock_send_single.call_count == 2
        mock_send_single.assert_any_call(
            {'slack': {
                'webhook_url': 'https://slack.test'
            }}, 'SUCCEEDED', self.job_context)
        mock_send_single.assert_any_call(
            {'discord': {
                'webhook_url': 'https://discord.test'
            }}, 'SUCCEEDED', self.job_context)

    def test_unknown_notification_handler(self):
        """Test handling of unknown notification handler."""
        config = {'unknown_service': {'webhook_url': 'https://unknown.test'}}

        # Should not raise exception
        notifications._send_single_notification(config, 'RUNNING',
                                                self.job_context)


class TestEventCallbackIntegration:
    """Test integration with event callback system."""

    def setup_method(self):
        """Set up test fixtures."""
        self.task = Task(name='test-task',
                         run='echo test',
                         event_callback=[{
                             'slack': {
                                 'webhook_url': 'https://hooks.slack.com/test',
                                 'notify_on': ['FAILED', 'SUCCEEDED']
                             }
                         }])
        self.job_context = {
            'job_id': 789,
            'task_id': 3,
            'task_name': 'integration-test',
            'cluster_name': 'test-cluster-3',
            'status': 'SUCCEEDED',
            'skypilot_task_id': 'sky-integration-id',
            'skypilot_task_ids': 'sky-integration-ids',
        }

    @mock.patch('sky.jobs.notifications.send_notifications')
    @mock.patch('sky.jobs.utils.managed_job_state')
    def test_event_callback_with_list_config(self, mock_job_state,
                                             mock_send_notifications):
        """Test event callback function with list configuration."""
        from sky.jobs.utils import event_callback_func

        # Mock job state functions
        mock_job_state.get_pool_from_job_id.return_value = None

        # Create callback function
        callback = event_callback_func(789, 3, self.task)

        # Execute callback with a valid notification status
        callback('SUCCEEDED')

        # Verify notifications were sent
        mock_send_notifications.assert_called_once()
        call_args = mock_send_notifications.call_args

        # Check arguments
        notifications_config, status, job_context = call_args[0]
        assert notifications_config == self.task.event_callback
        assert status == 'SUCCEEDED'
        assert job_context['job_id'] == 789
        assert job_context['task_id'] == 3

    @mock.patch('sky.jobs.notifications.send_notifications')
    @mock.patch('sky.jobs.utils.managed_job_state')
    def test_event_callback_skips_non_notification_statuses(
            self, mock_job_state, mock_send_notifications):
        """Test that event callback skips non-notification statuses."""
        from sky.jobs.utils import event_callback_func

        # Mock job state functions
        mock_job_state.get_pool_from_job_id.return_value = None

        # Create callback function
        callback = event_callback_func(789, 3, self.task)

        # Execute callback with statuses that should NOT trigger notifications
        for status in ['PENDING', 'SUBMITTED', 'SETTING_UP', 'UNKNOWN']:
            mock_send_notifications.reset_mock()
            callback(status)
            # Verify notifications were NOT sent
            mock_send_notifications.assert_not_called()

        # Execute callback with a status that SHOULD trigger notifications
        callback('FAILED')
        # Verify notifications WERE sent
        mock_send_notifications.assert_called_once()

    @mock.patch('sky.skylet.log_lib.run_bash_command_with_log')
    @mock.patch('sky.jobs.utils.managed_job_state')
    def test_event_callback_with_bash_script(self, mock_job_state,
                                             mock_run_bash):
        """Test event callback function with bash script (backward compatibility)."""
        from sky.jobs.utils import event_callback_func

        # Create task with bash script callback
        bash_task = Task(name='bash-test',
                         run='echo test',
                         event_callback='echo "Job status: $JOB_STATUS"')

        # Mock job state functions
        mock_job_state.get_pool_from_job_id.return_value = None
        mock_run_bash.return_value = 0

        # Create and execute callback
        callback = event_callback_func(789, 3, bash_task)
        callback('RUNNING')

        # Verify bash command was executed
        mock_run_bash.assert_called_once()
        call_args = mock_run_bash.call_args

        # Check bash command and environment variables
        assert call_args[1]['bash_command'] == 'echo "Job status: $JOB_STATUS"'
        env_vars = call_args[1]['env_vars']
        assert env_vars['JOB_STATUS'] == 'RUNNING'
        assert env_vars['JOB_ID'] == '789'
        assert env_vars['TASK_ID'] == '3'

    @mock.patch('sky.jobs.utils.managed_job_state')
    def test_event_callback_no_callback_configured(self, mock_job_state):
        """Test event callback function with no callback configured."""
        from sky.jobs.utils import event_callback_func

        # Create task without event callback
        no_callback_task = Task(name='no-callback', run='echo test')

        # Create and execute callback
        callback = event_callback_func(789, 3, no_callback_task)

        # Should not raise exception
        callback('SUCCEEDED')

    @mock.patch('sky.jobs.utils.managed_job_state')
    def test_event_callback_none_task(self, mock_job_state):
        """Test event callback function with None task."""
        from sky.jobs.utils import event_callback_func

        # Create callback with None task
        callback = event_callback_func(789, 3, None)

        # Should not raise exception
        callback('FAILED')
