"""Built-in notification handlers for managed jobs."""

import abc
import json
import shlex
from typing import Any, Dict, List

from sky import sky_logging
from sky.skylet import log_lib

logger = sky_logging.init_logger(__name__)

# Common status-based styling
_STATUS_EMOJI = {
    'STARTING': '🚀',
    'RUNNING': '⚡',
    'SUCCEEDED': '✅',
    'FAILED': '❌',
    'RECOVERING': '🔄',
    'CANCELLED': '⛔',
}

_STATUS_COLORS = {
    'STARTING': '#36a64f',
    'RUNNING': '#ffaa00',
    'SUCCEEDED': '#36a64f',
    'FAILED': '#ff0000',
    'RECOVERING': '#ffaa00',
    'CANCELLED': '#808080',
}


class NotificationHandler(abc.ABC):
    """Abstract base class for notification handlers."""
    
    def send_notification(self, 
                         status: str,
                         job_context: Dict[str, Any],
                         config: Dict[str, Any]) -> bool:
        """Send a notification.
        
        Args:
            status: Job status (e.g., 'RUNNING', 'SUCCEEDED', 'FAILED')
            job_context: Context information about the job
            config: Handler-specific configuration
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        # Validate required webhook_url
        webhook_url = config.get('webhook_url')
        if not webhook_url:
            logger.error(f"{self.__class__.__name__}: webhook_url not provided")
            return False
        
        # Check notify_on filter
        notify_on = config.get('notify_on', [])
        if notify_on and status not in notify_on:
            logger.debug(f"Skipping {self.__class__.__name__} notification for "
                        f"status {status} (only notifying on: {notify_on})")
            return True
        
        # Build and send message
        message = self._build_message(status, job_context, config)
        return self._send_webhook(webhook_url, message, job_context)
    
    def _format_custom_text(self, template: str, status: str, 
                           job_context: Dict[str, Any]) -> str:
        """Format custom text template with job context variables."""
        # Available template variables
        variables = {
            'JOB_STATUS': status,
            'JOB_ID': str(job_context['job_id']),
            'TASK_ID': str(job_context['task_id']),
            'TASK_NAME': job_context['task_name'],
            'CLUSTER_NAME': job_context['cluster_name'],
            'EMOJI': _STATUS_EMOJI.get(status, 'ℹ️'),
        }
        
        # Simple template substitution
        formatted = template
        for var, value in variables.items():
            formatted = formatted.replace(f'{{{var}}}', value)
            formatted = formatted.replace(f'${var}', value)  # Also support $VAR syntax
        
        return formatted
    
    @abc.abstractmethod
    def _build_message(self, status: str, job_context: Dict[str, Any], 
                      config: Dict[str, Any]) -> Dict[str, Any]:
        """Build the platform-specific message payload."""
        pass
    
    def _send_webhook(self, webhook_url: str, message: Dict[str, Any], 
                     job_context: Dict[str, Any]) -> bool:
        """Send webhook request using curl."""
        try:
            # Build curl command
            webhook_url_escaped = shlex.quote(webhook_url)
            message_json = json.dumps(message).replace('"', '\\"')
            
            curl_cmd = (f'curl -X POST {webhook_url_escaped} '
                       f'-H "Content-type: application/json" '
                       f'--data "{message_json}" --silent --fail')
            
            # Execute curl
            log_path = (f"/tmp/{self.__class__.__name__.lower()}_"
                       f"{job_context['job_id']}.log")
            
            result = log_lib.run_bash_command_with_log(
                bash_command=curl_cmd,
                log_path=log_path,
                env_vars={}
            )
            
            if result != 0:
                logger.error(f"{self.__class__.__name__} notification failed "
                           f"with exit code: {result}")
                return False
            
            logger.info(f"{self.__class__.__name__} notification sent successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send {self.__class__.__name__} "
                        f"notification: {e}")
            return False


class SlackNotificationHandler(NotificationHandler):
    """Slack notification handler using webhooks."""
    
    def _build_message(self, status: str, job_context: Dict[str, Any], 
                      config: Dict[str, Any]) -> Dict[str, Any]:
        """Build Slack-specific message payload."""
        username = config.get('username', 'SkyPilot Bot')
        channel = config.get('channel', '')
        
        emoji = _STATUS_EMOJI.get(status, 'ℹ️')
        color = _STATUS_COLORS.get(status, '#0099cc')
        
        # Check for custom message template
        custom_message = config.get('message')
        if custom_message:
            # Use custom message format
            text = self._format_custom_text(custom_message, status, job_context)
            
            message = {
                "username": username,
                "text": text
            }
        else:
            # Use default rich format
            message = {
                "username": username,
                "attachments": [{
                    "color": color,
                    "blocks": [{
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{emoji} SkyPilot Job: {job_context['task_name']}"
                        }
                    }, {
                        "type": "section", 
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Status:* `{status}`"},
                            {"type": "mrkdwn", "text": f"*Job ID:* {job_context['job_id']}"},
                            {"type": "mrkdwn", "text": f"*Task:* {job_context['task_name']}"},
                            {"type": "mrkdwn", "text": f"*Cluster:* {job_context['cluster_name']}"}
                        ]
                    }]
                }]
            }
        
        if channel:
            message["channel"] = channel
            
        return message


class DiscordNotificationHandler(NotificationHandler):
    """Discord notification handler using webhooks."""
    
    def _build_message(self, status: str, job_context: Dict[str, Any], 
                      config: Dict[str, Any]) -> Dict[str, Any]:
        """Build Discord-specific message payload."""
        username = config.get('username', 'SkyPilot Bot')
        
        emoji = _STATUS_EMOJI.get(status, 'ℹ️')
        # Convert hex colors to Discord integer format
        color_hex = _STATUS_COLORS.get(status, '#0099cc')
        color = int(color_hex.replace('#', ''), 16)
        
        # Check for custom message template
        custom_message = config.get('message')
        if custom_message:
            # Use simple text message
            text = self._format_custom_text(custom_message, status, job_context)
            
            return {
                "username": username,
                "content": text
            }
        else:
            # Use default rich embed format
            return {
                "username": username,
                "embeds": [{
                    "title": f"{emoji} SkyPilot Job Update",
                    "color": color,
                    "fields": [
                        {"name": "Status", "value": f"`{status}`", "inline": True},
                        {"name": "Job ID", "value": str(job_context['job_id']), "inline": True},
                        {"name": "Task", "value": job_context['task_name'], "inline": True},
                        {"name": "Cluster", "value": job_context['cluster_name'], "inline": True}
                    ]
                }]
            }


# Registry of built-in notification handlers
NOTIFICATION_HANDLERS = {
    'slack': SlackNotificationHandler(),
    'discord': DiscordNotificationHandler(),
}


def send_notifications(notifications_config: List[Dict[str, Any]],
                      status: str,
                      job_context: Dict[str, Any]) -> None:
    """Send notifications based on configuration.
    
    Args:
        notifications_config: List of notification handler configs
        status: Job status
        job_context: Job context information
    """
    
    # Handle notification list
    for config in notifications_config:
        _send_single_notification(config, status, job_context)


def _send_single_notification(config: Dict[str, Any], 
                             status: str,
                             job_context: Dict[str, Any]) -> None:
    """Send a single notification."""
    for handler_name, handler_config in config.items():
        if handler_name in NOTIFICATION_HANDLERS:
            handler = NOTIFICATION_HANDLERS[handler_name]
            try:
                success = handler.send_notification(status, job_context, handler_config)
                if not success:
                    logger.warning(f"Failed to send {handler_name} notification")
            except Exception as e:
                logger.error(f"Error sending {handler_name} notification: {e}")
        else:
            logger.warning(f"Unknown notification handler: {handler_name}")


