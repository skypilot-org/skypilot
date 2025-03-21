"""
Monitoring service for CLIP vector computation workers.
Aggregates metrics from all workers and serves a dashboard.
"""

import asyncio
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import time
from typing import DefaultDict, Dict, List

import aiofiles
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()


class MonitoringService:

    def __init__(self, metrics_dir: str, history_window: int = 3600):
        self.metrics_dir = Path(metrics_dir)
        self.last_update = {}
        self.worker_metrics = {}
        self.history_window = history_window  # Keep 1 hour of history by default

        # Track historical throughput data
        self.throughput_history: DefaultDict[str,
                                             List[Dict]] = defaultdict(list)
        self.last_processed_count: Dict[str, int] = {}

        # Track token throughput
        self.token_throughput_history: DefaultDict[
            str, List[Dict]] = defaultdict(list)
        self.last_token_count: Dict[str, int] = {}

        # Worker history tracking
        self.worker_history: DefaultDict[str, List[Dict]] = defaultdict(list)
        self.worker_sessions: DefaultDict[str, List[str]] = defaultdict(list)

        self.aggregate_metrics = {
            'total_processed': 0,
            'total_failed': 0,
            'total_workers': 0,
            'active_workers': 0,
            'completed_workers': 0,
            'failed_workers': 0,
            'overall_progress': 0.0,
            'overall_throughput': 0.0,
            'recent_throughput': 0.0,
            'overall_token_throughput': 0.0,
            'recent_token_throughput': 0.0,
            'total_tokens': 0,
            'estimated_time_remaining': None,
            'total_restarts': 0
        }

    def update_throughput_history(self, worker_id: str, metrics: Dict):
        """Update historical throughput data for a worker."""
        current_time = time.time()
        current_count = metrics['processed_count']

        # Get the session ID from metrics, defaulting to a generated one if not present
        session_id = metrics.get('session_id',
                                 f"unknown_{worker_id}_{int(current_time)}")

        # Store both the cumulative progress and throughput data
        if worker_id in self.last_processed_count:
            time_diff = current_time - self.throughput_history[worker_id][-1][
                'timestamp']
            count_diff = current_count - self.last_processed_count[worker_id]

            # Only update if there's a meaningful time difference and count change
            if time_diff > 0 and count_diff >= 0:  # Avoid division by zero and negative counts
                # Calculate throughput from raw metrics data
                throughput = count_diff / time_diff

                # Calculate overall throughput since the start of this session
                session_start_time = None
                session_start_count = 0

                # Find the start of this session
                for point in self.throughput_history[worker_id]:
                    if point.get('session_id', 'unknown') == session_id:
                        if session_start_time is None or point[
                                'timestamp'] < session_start_time:
                            session_start_time = point['timestamp']
                            session_start_count = point.get(
                                'cumulative_count', 0)

                overall_throughput = 0
                if session_start_time is not None and current_time > session_start_time:
                    overall_time = current_time - session_start_time
                    overall_count = current_count - session_start_count
                    overall_throughput = overall_count / overall_time if overall_time > 0 else 0

                # Add new data point with cumulative count and calculated throughputs
                self.throughput_history[worker_id].append({
                    'timestamp': current_time,
                    'recent_throughput': throughput,
                    'overall_throughput': overall_throughput,
                    'cumulative_count': current_count,
                    'session_id': session_id,
                    'interval': time_diff,
                    'count_change': count_diff
                })

                # Remove old data points outside the history window
                cutoff_time = current_time - self.history_window
                self.throughput_history[worker_id] = [
                    point for point in self.throughput_history[worker_id]
                    if point['timestamp'] > cutoff_time
                ]
        else:
            # First data point for this worker - can't calculate throughput yet
            self.throughput_history[worker_id].append({
                'timestamp': current_time,
                'recent_throughput': 0,
                'overall_throughput': 0,
                'cumulative_count': current_count,
                'session_id': session_id,
                'interval': 0,
                'count_change': 0
            })

        self.last_processed_count[worker_id] = current_count

        # Track token throughput if available in metrics
        if 'total_tokens' in metrics:
            current_tokens = metrics['total_tokens']

            if worker_id in self.last_token_count:
                token_diff = current_tokens - self.last_token_count[worker_id]

                # Only update if there's a meaningful time difference and token change
                if time_diff > 0 and token_diff >= 0:
                    # Calculate token throughput
                    token_throughput = token_diff / time_diff

                    # Calculate overall token throughput since the start of this session
                    session_token_start = 0
                    session_start_time = None

                    # Find the start of this session for tokens
                    if worker_id in self.token_throughput_history and self.token_throughput_history[
                            worker_id]:
                        for point in self.token_throughput_history[worker_id]:
                            if point.get('session_id', 'unknown') == session_id:
                                if session_start_time is None or point[
                                        'timestamp'] < session_start_time:
                                    session_start_time = point['timestamp']
                                    session_token_start = point.get(
                                        'cumulative_tokens', 0)

                    overall_token_throughput = 0
                    if session_start_time is not None and current_time > session_start_time:
                        overall_time = current_time - session_start_time
                        overall_tokens = current_tokens - session_token_start
                        overall_token_throughput = overall_tokens / overall_time if overall_time > 0 else 0

                    # Add new data point for token throughput
                    self.token_throughput_history[worker_id].append({
                        'timestamp': current_time,
                        'recent_token_throughput': token_throughput,
                        'overall_token_throughput': overall_token_throughput,
                        'cumulative_tokens': current_tokens,
                        'session_id': session_id,
                        'interval': time_diff,
                        'token_change': token_diff
                    })

                    # Remove old data points outside history window
                    self.token_throughput_history[worker_id] = [
                        point
                        for point in self.token_throughput_history[worker_id]
                        if point['timestamp'] > cutoff_time
                    ]
            else:
                # First token data point
                self.token_throughput_history[worker_id].append({
                    'timestamp': current_time,
                    'recent_token_throughput': 0,
                    'overall_token_throughput': 0,
                    'cumulative_tokens': current_tokens,
                    'session_id': session_id,
                    'interval': 0,
                    'token_change': 0
                })

            self.last_token_count[worker_id] = current_tokens

    async def read_worker_history(self, worker_id: str):
        """Read the complete history for a worker."""
        history_file = self.metrics_dir / f'worker_{worker_id}_history.json'
        try:
            if history_file.exists():
                async with aiofiles.open(history_file, 'r') as f:
                    content = await f.read()
                    history = json.loads(content)

                    # Update worker history
                    self.worker_history[worker_id] = history

                    # Extract unique session IDs
                    sessions = set()
                    for entry in history:
                        # Skip entries without a session_id
                        if 'session_id' in entry:
                            sessions.add(entry['session_id'])
                        elif 'timestamp' in entry:
                            # If session_id is missing but there's a timestamp, create a synthetic session ID
                            synthetic_session_id = f"unknown_{worker_id}_{int(entry['timestamp'])//3600}"
                            entry[
                                'session_id'] = synthetic_session_id  # Add it to the entry
                            sessions.add(synthetic_session_id)
                        else:
                            # If both are missing, use a generic unknown ID
                            entry['session_id'] = f"unknown_{worker_id}"
                            sessions.add(f"unknown_{worker_id}")

                    self.worker_sessions[worker_id] = sorted(list(sessions))

                    return history
            return []
        except Exception as e:
            print(f"Error reading history for worker {worker_id}: {e}")
            return []

    async def update_metrics(self):
        """Read and aggregate metrics from all worker files."""
        try:
            # Read all worker metric files
            worker_files = list(self.metrics_dir.glob('worker_*.json'))

            # Filter out history files
            worker_files = [f for f in worker_files if '_history' not in f.name]

            new_metrics = {}
            total_restarts = 0

            for file in worker_files:
                try:
                    async with aiofiles.open(file, 'r') as f:
                        content = await f.read()
                        metrics = json.loads(content)
                        worker_id = metrics['worker_id']

                        # Ensure session_id exists, generate one if missing
                        if 'session_id' not in metrics and 'timestamp' in metrics:
                            metrics[
                                'session_id'] = f"unknown_{worker_id}_{int(metrics['timestamp'])//3600}"
                        elif 'session_id' not in metrics:
                            metrics['session_id'] = f"unknown_{worker_id}"

                        new_metrics[worker_id] = metrics

                        # Read worker history
                        await self.read_worker_history(worker_id)

                        # Count number of sessions as restarts
                        total_restarts += max(
                            0,
                            len(self.worker_sessions[worker_id]) - 1)

                        self.update_throughput_history(worker_id, metrics)
                except Exception as e:
                    print(f"Error reading metrics from {file}: {e}")
                    continue

            # Update worker metrics
            self.worker_metrics = new_metrics

            # Calculate aggregate metrics
            total_processed = 0
            total_failed = 0
            active_workers = 0
            completed_workers = 0
            failed_workers = 0
            total_progress = 0
            total_items = 0
            total_tokens = 0

            # Calculate throughput metrics by aggregating from throughput history
            total_recent_throughput = 0
            total_overall_throughput = 0
            total_recent_token_throughput = 0
            total_overall_token_throughput = 0
            active_worker_count = 0

            for worker_id, metrics in self.worker_metrics.items():
                total_processed += metrics['processed_count']
                total_failed += metrics.get('failed_count', 0)
                total_tokens += metrics.get('total_tokens', 0)

                if metrics.get('status') == 'running':
                    active_workers += 1
                    active_worker_count += 1
                elif metrics.get('status') == 'completed':
                    completed_workers += 1
                elif metrics.get('status') == 'failed':
                    failed_workers += 1

                if metrics.get('end_idx'):
                    total_items += metrics['end_idx'] - metrics['start_idx']
                    total_progress += metrics['processed_count']

                # Get the most recent throughput data for this worker
                if worker_id in self.throughput_history and self.throughput_history[
                        worker_id]:
                    latest = sorted(self.throughput_history[worker_id],
                                    key=lambda x: x['timestamp'])[-1]
                    total_recent_throughput += latest.get(
                        'recent_throughput', 0)
                    total_overall_throughput += latest.get(
                        'overall_throughput', 0)

                # Get the most recent token throughput data for this worker
                if worker_id in self.token_throughput_history and self.token_throughput_history[
                        worker_id]:
                    latest_token = sorted(
                        self.token_throughput_history[worker_id],
                        key=lambda x: x['timestamp'])[-1]
                    total_recent_token_throughput += latest_token.get(
                        'recent_token_throughput', 0)
                    total_overall_token_throughput += latest_token.get(
                        'overall_token_throughput', 0)

            # Update aggregate metrics
            self.aggregate_metrics.update({
                'total_processed': total_processed,
                'total_failed': total_failed,
                'total_workers': len(self.worker_metrics),
                'active_workers': active_workers,
                'completed_workers': completed_workers,
                'failed_workers': failed_workers,
                'overall_progress': (total_progress / total_items *
                                     100) if total_items > 0 else 0,
                'overall_throughput': total_overall_throughput,
                'recent_throughput': total_recent_throughput,
                'overall_token_throughput': total_overall_token_throughput,
                'recent_token_throughput': total_recent_token_throughput,
                'total_tokens': total_tokens,
                'estimated_time_remaining':
                    (total_items - total_progress) / total_overall_throughput
                    if total_overall_throughput > 0 else None,
                'total_restarts': total_restarts
            })

        except Exception as e:
            print(f"Error updating metrics: {e}")

    def get_throughput_chart_data(self) -> Dict:
        """Prepare cumulative progress data for Chart.js from the worker history files."""
        # We're going to use the worker history data, which already contains the full history
        # of each worker's progress over time, including restarts.

        # First, make sure all worker histories are loaded
        all_histories = []
        for worker_id in self.worker_metrics.keys():
            if worker_id not in self.worker_history or not self.worker_history[
                    worker_id]:
                continue  # Skip workers with no history

            all_histories.extend([
                (worker_id, entry) for entry in self.worker_history[worker_id]
            ])

        # If we have no history data, return an empty dataset
        if not all_histories:
            now = int(time.time())
            empty_dataset = {
                'label': 'No Progress Data',
                'data': [{
                    'x': (now - 3600) * 1000,
                    'y': 0
                }, {
                    'x': now * 1000,
                    'y': 0
                }],
                'borderColor': '#cccccc',
                'backgroundColor': '#cccccc20',
                'borderWidth': 2,
                'fill': 'false'
            }
            return {'datasets': [empty_dataset]}

        # Sort all history entries by timestamp
        all_histories.sort(key=lambda x: x[1].get('timestamp', 0))

        # The master dataset shows the overall progress across all workers
        master_dataset = {
            'label': 'Total Progress',
            'data': [],
            'borderColor': '#000000',
            'backgroundColor': '#00000020',
            'borderWidth': 3,
            'fill': 'false',
            'tension': 0.1,
            'pointRadius': 0
        }

        # Start with 0 at the earliest timestamp
        first_timestamp = all_histories[0][1].get('timestamp', 0)
        master_dataset['data'].append({
            'x': first_timestamp * 1000,  # Convert to milliseconds for Chart.js
            'y': 0
        })

        # Track the last known processed count for each worker/session
        latest_processed = {}

        # Process all history events in chronological order
        for worker_id, entry in all_histories:
            timestamp = entry.get('timestamp', 0)
            session_id = entry.get('session_id', 'unknown')
            key = f"{worker_id}_{session_id}"

            # Update the processed count for this worker session
            if 'processed_count' in entry:
                latest_processed[key] = entry['processed_count']

            # Calculate the total processed count across all worker sessions
            total_processed = sum(latest_processed.values())

            # Add a data point to the master dataset
            master_dataset['data'].append({
                'x': timestamp * 1000,  # Convert to milliseconds for Chart.js
                'y': total_processed
            })

        # Create individual datasets for each worker to show their contribution
        worker_datasets = []
        colors = [
            '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40'
        ]

        # Group history by worker and session
        worker_sessions = {}
        for worker_id, entries in self.worker_history.items():
            # Group entries by session
            sessions = {}
            for entry in entries:
                session_id = entry.get('session_id', 'unknown')
                if session_id not in sessions:
                    sessions[session_id] = []
                sessions[session_id].append(entry)

            # Process each session separately
            for session_id, session_entries in sessions.items():
                key = f"{worker_id}_{session_id}"
                worker_sessions[key] = {
                    'worker_id': worker_id,
                    'session_id': session_id,
                    'entries': sorted(session_entries,
                                      key=lambda e: e.get('timestamp', 0))
                }

        # Create a dataset for each worker session
        for i, (key, session) in enumerate(worker_sessions.items()):
            worker_id = session['worker_id']
            session_id = session['session_id']
            entries = session['entries']

            if not entries:
                continue  # Skip empty sessions

            # Assign a color
            color_idx = sum(ord(c) for c in worker_id) % len(
                colors)  # Deterministic color based on worker ID
            color = colors[color_idx]

            # Adjust brightness for different sessions of the same worker
            session_num = sum(1 for k in worker_sessions.keys()
                              if k.startswith(f"{worker_id}_"))
            session_idx = sum(1 for k in worker_sessions.keys()
                              if k.startswith(f"{worker_id}_") and k <= key)
            brightness_adjust = (session_idx - 1) * (100 // max(1, session_num))
            session_color = self._adjust_color_brightness(
                color, brightness_adjust)

            # Create the dataset for this worker session
            worker_dataset = {
                'label': f"{worker_id} (session {session_id[:8]})",
                'data': [],
                'borderColor': session_color,
                'backgroundColor': session_color + '20',
                'borderWidth': 1,
                'fill': 'false',
                'tension': 0.1,
                'pointRadius': 1
            }

            # Start with 0 at the session's first timestamp
            first_entry_time = entries[0].get('timestamp', 0)
            worker_dataset['data'].append({
                'x': first_entry_time * 1000,
                'y': 0
            })

            # Add each progress data point
            for entry in entries:
                if 'processed_count' in entry:
                    worker_dataset['data'].append({
                        'x': entry.get('timestamp', 0) * 1000,
                        'y': entry['processed_count']
                    })

            worker_datasets.append(worker_dataset)

        # Return all datasets, with the master dataset first
        return {'datasets': [master_dataset] + worker_datasets}

    def _adjust_color_brightness(self, hex_color, percent):
        """Adjust the brightness of a hex color."""
        # Convert hex to RGB
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)

        # Increase brightness
        r = min(255, r + int(percent))
        g = min(255, g + int(percent))
        b = min(255, b + int(percent))

        # Convert back to hex
        return f'#{r:02x}{g:02x}{b:02x}'

    def get_session_history_data(self) -> Dict:
        """Prepare session history data for visualization."""
        session_data = []

        for worker_id, history in self.worker_history.items():
            # Group events by session
            sessions = {}
            for event in history:
                session_id = event.get('session_id', 'unknown')
                if session_id not in sessions:
                    sessions[session_id] = {
                        'worker_id': worker_id,
                        'session_id': session_id,
                        'start_time': None,
                        'end_time': None,
                        'duration': 0,
                        'processed': 0,
                        'failed': 0,
                        'status': 'unknown',
                        'termination_reason': None
                    }

                # Update session data
                timestamp = event.get('timestamp', 0)
                if event.get('event') == 'start' or (
                        not sessions[session_id]['start_time'] and
                        event.get('timestamp')):
                    sessions[session_id]['start_time'] = timestamp

                if event.get('status') in ['completed', 'failed']:
                    sessions[session_id]['end_time'] = timestamp
                    sessions[session_id]['status'] = event.get('status')

                # Track spot VM termination events
                if event.get('event') == 'termination' or event.get(
                        'status') == 'terminated':
                    sessions[session_id]['end_time'] = timestamp
                    sessions[session_id]['status'] = 'terminated'
                    sessions[session_id][
                        'termination_reason'] = 'Spot VM interruption'

                if 'processed_count' in event:
                    sessions[session_id]['processed'] = max(
                        sessions[session_id]['processed'],
                        event['processed_count'])

                if 'failed_count' in event:
                    sessions[session_id]['failed'] = max(
                        sessions[session_id]['failed'], event['failed_count'])

            # Calculate duration and add to data
            for session in sessions.values():
                if session['start_time']:
                    if session['end_time']:
                        session['duration'] = session['end_time'] - session[
                            'start_time']
                    else:
                        # Session might still be running
                        session['duration'] = time.time(
                        ) - session['start_time']
                        session['status'] = 'running'

                session_data.append(session)

        return sorted(session_data, key=lambda x: x.get('start_time', 0))

    def get_token_throughput_chart_data(self) -> Dict:
        """Prepare token throughput data for Chart.js."""
        # Get the earliest and latest timestamps across all workers
        all_timestamps = []
        for history in self.token_throughput_history.values():
            all_timestamps.extend(point['timestamp'] for point in history)

        if not all_timestamps:
            # Create empty datasets with placeholder data
            now = int(time.time())
            empty_bar_dataset = {
                'label': 'No Token Throughput Data',
                'type': 'bar',
                'data': [{
                    'x': (now - 3600) * 1000,
                    'y': 0
                }, {
                    'x': now * 1000,
                    'y': 0
                }],
                'backgroundColor': '#cccccc80',
                'borderColor': '#cccccc',
                'borderWidth': 1
            }
            return {'datasets': [empty_bar_dataset]}

        min_time = min(all_timestamps)
        max_time = max(all_timestamps)

        # Generate datasets for token throughput chart
        token_datasets = []

        # Add a total token throughput dataset that aggregates all workers
        total_token_dataset = {
            'label': 'Total Token Throughput',
            'type': 'bar',  # Use bar chart for histogram-like display
            'data': [],
            'backgroundColor': '#36A2EB80',
            'borderColor': '#36A2EB',
            'borderWidth': 1,
            'barPercentage': 0.8,
            'categoryPercentage': 0.9,
            'order': 1  # Lower order means it's drawn first (behind other datasets)
        }

        # Add a line dataset to show the trend
        total_token_trend_dataset = {
            'label': 'Throughput Trend',
            'type': 'line',
            'data': [],
            'borderColor': '#FF6384',
            'backgroundColor': '#FF638420',
            'borderWidth': 2,
            'pointRadius': 0,
            'fill': 'false',
            'tension': 0.4,
            'order': 0  # Higher priority, drawn on top
        }

        colors = [
            '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40'
        ]

        # First collect all data points from all workers - gather both recent and overall throughput
        all_token_points = []
        for worker_id, history in self.token_throughput_history.items():
            for point in history:
                # Use recent_token_throughput if available, otherwise try overall_token_throughput
                throughput = point.get('recent_token_throughput', 0)
                if throughput == 0:
                    throughput = point.get('overall_token_throughput', 0)

                all_token_points.append({
                    'timestamp': point['timestamp'],
                    'throughput': throughput,
                    'worker_id': worker_id,
                    'session_id': point.get('session_id', 'unknown')
                })

        # Sort all points by timestamp
        all_token_points.sort(key=lambda x: x['timestamp'])

        # If we have no data points at all, create a placeholder dataset
        if not all_token_points:
            # Create empty datasets with placeholder data
            now = int(time.time())
            empty_bar_dataset = {
                'label': 'No Token Throughput Data',
                'type': 'bar',
                'data': [{
                    'x': (now - 3600) * 1000,
                    'y': 0
                }, {
                    'x': now * 1000,
                    'y': 0
                }],
                'backgroundColor': '#cccccc80',
                'borderColor': '#cccccc',
                'borderWidth': 1
            }
            return {'datasets': [empty_bar_dataset]}

        # Group data into time bins (e.g., 1-minute bins) for the histogram
        bin_size = 60  # 1 minute bins
        time_bins = {}

        for point in all_token_points:
            # Create a bin key by rounding the timestamp to the nearest bin
            bin_key = int(point['timestamp'] // bin_size * bin_size)

            if bin_key not in time_bins:
                time_bins[bin_key] = []

            # Only include non-zero values
            if point['throughput'] > 0:
                time_bins[bin_key].append(point['throughput'])

        # Calculate the average throughput for each time bin
        for bin_timestamp, throughputs in sorted(time_bins.items()):
            if throughputs:  # Only process if there are non-zero values
                avg_throughput = sum(throughputs) / len(throughputs)

                # Add to the histogram dataset
                total_token_dataset['data'].append({
                    'x': bin_timestamp *
                         1000,  # Convert to milliseconds for Chart.js
                    'y': avg_throughput
                })

                # Also add to the trend line
                total_token_trend_dataset['data'].append({
                    'x': bin_timestamp * 1000,
                    'y': avg_throughput
                })

        # Ensure there's at least one data point for visualization
        if not total_token_dataset['data']:
            # Create a single data point at the current time with a value of 0
            current_time = int(time.time()) * 1000
            total_token_dataset['data'].append({'x': current_time, 'y': 0})
            total_token_trend_dataset['data'].append({
                'x': current_time,
                'y': 0
            })

        # Add individual worker datasets as lines if there aren't too many workers
        if len(self.token_throughput_history
              ) <= 5:  # Only show individual workers if there aren't too many
            for i, (worker_id, history) in enumerate(
                    self.token_throughput_history.items()):
                # Skip empty history
                if not history:
                    continue

                color = colors[i % len(colors)]

                # Group by session ID
                sessions = {}
                for point in history:
                    session_id = point.get('session_id', 'unknown')
                    if session_id not in sessions:
                        sessions[session_id] = []
                    sessions[session_id].append(point)

                # Create separate datasets for each session
                for j, (session_id,
                        session_points) in enumerate(sessions.items()):
                    # Skip sessions with no data
                    if not session_points:
                        continue

                    # Sort by timestamp
                    session_points.sort(key=lambda x: x['timestamp'])

                    # Create dataset for this session
                    worker_dataset = {
                        'label': f"{worker_id} (session {session_id[:8]})",
                        'type': 'line',
                        'data': [],
                        'borderColor': self._adjust_color_brightness(
                            color, j * 20),
                        'backgroundColor': 'transparent',
                        'borderWidth': 1,
                        'pointRadius': 1,
                        'fill': 'false',
                        'order': 2  # Draw on top of the histogram
                    }

                    # Add data points - try both recent and overall token throughput
                    for point in session_points:
                        timestamp = point['timestamp']
                        # Convert to chart.js time format (milliseconds since epoch)
                        timestamp_ms = timestamp * 1000

                        # Use recent_token_throughput if available, otherwise try overall_token_throughput
                        throughput = point.get('recent_token_throughput', 0)
                        if throughput == 0:
                            throughput = point.get('overall_token_throughput',
                                                   0)

                        worker_dataset['data'].append({
                            'x': timestamp_ms,
                            'y': throughput
                        })

                    # Only add datasets with actual data
                    if worker_dataset['data']:
                        token_datasets.append(worker_dataset)

        # Combine all datasets, with the total histogram first
        all_datasets = [total_token_dataset, total_token_trend_dataset
                       ] + token_datasets

        return {'datasets': all_datasets}

    def get_dashboard_html(self) -> str:
        """Generate HTML dashboard."""
        refresh_rate = 5  # seconds

        # Convert metrics to human-readable format
        metrics = self.aggregate_metrics.copy()
        metrics['overall_progress'] = f"{metrics['overall_progress']:.2f}%"
        metrics[
            'overall_throughput'] = f"{metrics['overall_throughput']:.2f} requests/s"
        metrics[
            'recent_throughput'] = f"{metrics['recent_throughput']:.2f} requests/s"
        metrics[
            'overall_token_throughput'] = f"{metrics['overall_token_throughput']:.2f} tokens/s"
        metrics[
            'recent_token_throughput'] = f"{metrics['recent_token_throughput']:.2f} tokens/s"
        metrics['total_tokens_formatted'] = f"{metrics['total_tokens']:,}"

        if metrics['estimated_time_remaining']:
            hours = metrics['estimated_time_remaining'] / 3600
            metrics['estimated_time_remaining'] = f"{hours:.1f} hours"
        else:
            metrics['estimated_time_remaining'] = "N/A"

        # Generate worker status table
        worker_rows = []
        for worker_id, worker in self.worker_metrics.items():
            progress = (
                (worker.get('current_idx', 0) - worker.get('start_idx', 0)) /
                (worker.get('end_idx', 1) - worker.get('start_idx', 0)) *
                100 if worker.get('end_idx') else 0)

            # Count sessions/restarts for this worker
            session_count = len(self.worker_sessions.get(worker_id, []))
            restart_count = max(0, session_count - 1)

            # Determine status class for styling
            status_class = ''
            if worker.get('status') == 'running':
                status_class = 'status-running'
            elif worker.get('status') == 'completed':
                status_class = 'status-completed'
            elif worker.get('status') == 'failed' or worker.get(
                    'status') == 'terminated':
                status_class = 'status-failed'

            # Get token throughput if available
            token_throughput = "N/A"
            if worker_id in self.token_throughput_history and self.token_throughput_history[
                    worker_id]:
                latest_token = sorted(self.token_throughput_history[worker_id],
                                      key=lambda x: x['timestamp'])[-1]
                token_throughput = f"{latest_token.get('recent_token_throughput', 0):.2f} tokens/s"

            row = f"""
            <tr>
                <td>{worker_id}</td>
                <td class="{status_class}">{worker.get('status', 'unknown')}</td>
                <td>{progress:.2f}%</td>
                <td>{worker.get('processed_count', 0)}</td>
                <td>{worker.get('failed_count', 0)}</td>
                <td>{worker.get('items_per_second', 0):.2f}</td>
                <td>{token_throughput}</td>
                <td>{restart_count}</td>
                <td>{datetime.fromtimestamp(worker.get('timestamp', worker.get('last_update', 0))).strftime('%Y-%m-%d %H:%M:%S')}</td>
            </tr>
            """
            worker_rows.append(row)

        # Generate session history table
        session_data = self.get_session_history_data()
        session_rows = []
        for session in session_data:
            start_time = datetime.fromtimestamp(session.get(
                'start_time', 0)).strftime('%Y-%m-%d %H:%M:%S') if session.get(
                    'start_time') else 'N/A'
            end_time = datetime.fromtimestamp(session.get(
                'end_time', 0)).strftime('%Y-%m-%d %H:%M:%S') if session.get(
                    'end_time') else 'N/A'
            duration_mins = session.get('duration', 0) / 60

            # Determine status class for styling
            status_class = ''
            if session.get('status') == 'running':
                status_class = 'status-running'
            elif session.get('status') == 'completed':
                status_class = 'status-completed'
            elif session.get('status') in ['failed', 'terminated']:
                status_class = 'status-failed'

            # Show termination reason for spot VM interruptions
            status_text = session.get('status', 'unknown')
            if session.get('termination_reason'):
                status_text += f" ({session.get('termination_reason')})"

            row = f"""
            <tr>
                <td>{session.get('worker_id', 'unknown')}</td>
                <td>{session.get('session_id', 'unknown')[-8:]}</td>
                <td class="{status_class}">{status_text}</td>
                <td>{start_time}</td>
                <td>{end_time if session.get('end_time') else 'Running'}</td>
                <td>{duration_mins:.1f} min</td>
                <td>{session.get('processed', 0)}</td>
                <td>{session.get('failed', 0)}</td>
            </tr>
            """
            session_rows.append(row)

        # Get chart data
        chart_data = json.dumps(self.get_throughput_chart_data())

        # Add token throughput chart initialization
        token_throughput_data = self.get_token_throughput_chart_data()
        token_throughput_chart_json = json.dumps(token_throughput_data)

        # Initialize charts
        charts_js = f"""
        // Throughput Chart (Cumulative Progress)
        var throughputCtx = document.getElementById('throughputChart').getContext('2d');
        var throughputChart = new Chart(throughputCtx, {{
            type: 'line',
            data: {chart_data},
            options: {{
                responsive: true,
                maintainAspectRatio: 'false',
                scales: {{
                    x: {{
                        type: 'time',
                        time: {{
                            unit: 'minute',
                            tooltipFormat: 'MMM dd, HH:mm:ss',
                            displayFormats: {{
                                minute: 'HH:mm'
                            }}
                        }},
                        title: {{
                            display: true,
                            text: 'Time'
                        }}
                    }},
                    y: {{
                        title: {{
                            display: true,
                            text: 'Cumulative Items Processed'
                        }},
                        beginAtZero: true,
                        min: 0  // Force the minimum to be exactly 0
                    }}
                }},
                plugins: {{
                    title: {{
                        display: true,
                        text: 'Cumulative Progress Over Time'
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                var label = context.dataset.label || '';
                                if (label) {{
                                    label += ': ';
                                }}
                                if (context.parsed.y !== null) {{
                                    label += context.parsed.y.toFixed(0) + ' items';
                                }}
                                return label;
                            }}
                        }}
                    }}
                }}
            }}
        }});
        
        // Token Throughput Chart (Histogram)
        var tokenThroughputCtx = document.getElementById('tokenThroughputChart').getContext('2d');
        var tokenThroughputData = {token_throughput_chart_json};
        
        // Debug output to check if data is empty
        console.log("Token Throughput Data:", tokenThroughputData);
        
        var tokenThroughputChart = new Chart(tokenThroughputCtx, {{
            type: 'bar',  // Default type is bar (histogram)
            data: tokenThroughputData,
            options: {{
                responsive: true,
                maintainAspectRatio: 'false',
                scales: {{
                    x: {{
                        type: 'time',
                        time: {{
                            unit: 'minute',
                            tooltipFormat: 'MMM dd, HH:mm:ss',
                            displayFormats: {{
                                minute: 'HH:mm'
                            }}
                        }},
                        title: {{
                            display: true,
                            text: 'Time'
                        }}
                    }},
                    y: {{
                        title: {{
                            display: true,
                            text: 'Tokens/second'
                        }},
                        beginAtZero: true,
                        min: 0  // Force the minimum to be exactly 0
                    }}
                }},
                plugins: {{
                    title: {{
                        display: true,
                        text: 'Token Throughput Over Time'
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                var label = context.dataset.label || '';
                                if (label) {{
                                    label += ': ';
                                }}
                                if (context.parsed.y !== null) {{
                                    label += context.parsed.y.toFixed(2) + ' tokens/s';
                                }}
                                return label;
                            }}
                        }}
                    }}
                }}
            }}
        }});
        """

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>CLIP Vector Computation Progress</title>
            <meta id="refresh-meta" http-equiv="refresh" content="{refresh_rate}">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/moment@2.29.1"></script>
            <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-moment@1.0.0"></script>
            <script>
                // Enable mixed chart types (bar + line)
                Chart.defaults.set('plugins.legend', {{
                    position: 'top',
                    labels: {{
                        usePointStyle: true,
                        padding: 15
                    }}
                }});
            </script>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .metrics-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 20px;
                    margin-bottom: 30px;
                }}
                .metric-card {{
                    background: #f5f5f5;
                    padding: 15px;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                .metric-value {{ font-size: 24px; font-weight: bold; margin: 10px 0; }}
                .metric-label {{ color: #666; }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 20px;
                }}
                th, td {{
                    padding: 12px;
                    text-align: left;
                    border-bottom: 1px solid #ddd;
                }}
                th {{ background-color: #f5f5f5; }}
                .chart-container {{
                    width: 100%;
                    height: 400px;
                    margin: 30px 0;
                }}
                .status-running {{ color: green; }}
                .status-completed {{ color: blue; }}
                .status-failed {{ color: red; }}
                .tabs {{
                    margin-top: 30px;
                    border-bottom: 1px solid #ccc;
                    display: flex;
                }}
                .tab {{
                    padding: 10px 15px;
                    cursor: pointer;
                    background: #f5f5f5;
                    margin-right: 5px;
                    border-radius: 5px 5px 0 0;
                }}
                .tab.active {{
                    background: #ddd;
                }}
                .tab-content {{
                    display: none;
                    padding: 20px 0;
                }}
                .tab-content.active {{
                    display: block;
                }}
                .toggle-refresh {{
                    margin-top: 10px;
                    padding: 8px 15px;
                    background-color: #4CAF50;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 14px;
                }}
                .toggle-refresh.paused {{
                    background-color: #f44336;
                }}
                .header-controls {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }}
                .refresh-status {{
                    margin-left: 10px;
                    font-size: 14px;
                    color: #666;
                }}
            </style>
        </head>
        <body>
            <div class="header-controls">
                <h1>CLIP Vector Computation Progress</h1>
                <div>
                    <button id="toggle-refresh" class="toggle-refresh">Pause Auto-Refresh</button>
                    <span id="refresh-status" class="refresh-status">Auto-refreshing every {refresh_rate}s</span>
                </div>
            </div>
            
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-label">Overall Progress</div>
                    <div class="metric-value">{metrics['overall_progress']}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Overall Speed</div>
                    <div class="metric-value">{metrics['overall_throughput']}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Recent Speed</div>
                    <div class="metric-value">{metrics['recent_throughput']}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Estimated Time Remaining</div>
                    <div class="metric-value">{metrics['estimated_time_remaining']}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Total Processed</div>
                    <div class="metric-value">{metrics['total_processed']}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Failed Requests</div>
                    <div class="metric-value">{metrics['total_failed']}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">VM Restarts</div>
                    <div class="metric-value">{metrics['total_restarts']}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Total Tokens</div>
                    <div class="metric-value">{metrics['total_tokens_formatted']}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Token Throughput</div>
                    <div class="metric-value">{metrics['overall_token_throughput']}</div>
                </div>
            </div>

            <h2>Cumulative Progress</h2>
            <div class="chart-container">
                <canvas id="throughputChart"></canvas>
            </div>
            
            <h2>Token Throughput</h2>
            <div class="chart-container">
                <canvas id="tokenThroughputChart"></canvas>
            </div>
            
            <div class="tabs">
                <div class="tab active" onclick="showTab('currentStatus')">Current Status</div>
                <div class="tab" onclick="showTab('sessionHistory')">Session History</div>
            </div>
            
            <div id="currentStatus" class="tab-content active">
                <h2>Worker Status</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Worker ID</th>
                            <th>Status</th>
                            <th>Progress</th>
                            <th>Processed</th>
                            <th>Failed</th>
                            <th>Speed (requests/s)</th>
                            <th>Token Speed</th>
                            <th>Restarts</th>
                            <th>Last Update</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(worker_rows)}
                    </tbody>
                </table>
            </div>
            
            <div id="sessionHistory" class="tab-content">
                <h2>Session History</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Worker ID</th>
                            <th>Session ID</th>
                            <th>Status</th>
                            <th>Start Time</th>
                            <th>End Time</th>
                            <th>Duration</th>
                            <th>Processed</th>
                            <th>Failed</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(session_rows)}
                    </tbody>
                </table>
            </div>

            <script>
                // Chart setup
                const ctx = document.getElementById('throughputChart');
                const chartData = {chart_data};
                
                const chart = new Chart(ctx, {{
                    type: 'line',
                    data: chartData,
                    options: {{
                        responsive: true,
                        maintainAspectRatio: 'false',
                        scales: {{
                            x: {{
                                type: 'time',
                                time: {{
                                    unit: 'minute',
                                    tooltipFormat: 'MMM dd, HH:mm:ss'
                                }},
                                title: {{
                                    display: true,
                                    text: 'Time'
                                }}
                            }},
                            y: {{
                                beginAtZero: true,
                                min: 0,  // Force the minimum to be exactly 0
                                title: {{
                                    display: true,
                                    text: 'Cumulative Items Processed'
                                }}
                            }}
                        }},
                        plugins: {{
                            title: {{
                                display: true,
                                text: 'Cumulative Progress Over Time'
                            }},
                            tooltip: {{
                                callbacks: {{
                                    label: function(context) {{
                                        var label = context.dataset.label || '';
                                        if (label) {{
                                            label += ': ';
                                        }}
                                        if (context.parsed.y !== null) {{
                                            label += context.parsed.y.toFixed(0) + ' items';
                                        }}
                                        return label;
                                    }}
                                }}
                            }}
                        }}
                    }}
                }});
                
                // Tab switching function
                function showTab(tabId) {{
                    // Hide all tab contents
                    document.querySelectorAll('.tab-content').forEach(content => {{
                        content.classList.remove('active');
                    }});
                    
                    // Deactivate all tabs
                    document.querySelectorAll('.tab').forEach(tab => {{
                        tab.classList.remove('active');
                    }});
                    
                    // Show the selected tab content
                    document.getElementById(tabId).classList.add('active');
                    
                    // Activate the clicked tab
                    event.currentTarget.classList.add('active');
                }}
                
                // Auto-refresh toggle
                let autoRefreshEnabled = true;
                const toggleButton = document.getElementById('toggle-refresh');
                const refreshStatus = document.getElementById('refresh-status');
                const refreshMeta = document.getElementById('refresh-meta');
                
                toggleButton.addEventListener('click', function() {{
                    autoRefreshEnabled = !autoRefreshEnabled;
                    
                    if (autoRefreshEnabled) {{
                        // Enable auto-refresh
                        refreshMeta.setAttribute('content', '{refresh_rate}');
                        toggleButton.textContent = 'Pause Auto-Refresh';
                        toggleButton.classList.remove('paused');
                        refreshStatus.textContent = 'Auto-refreshing every {refresh_rate}s';
                    }} else {{
                        // Disable auto-refresh
                        refreshMeta.setAttribute('content', '');
                        toggleButton.textContent = 'Resume Auto-Refresh';
                        toggleButton.classList.add('paused');
                        refreshStatus.textContent = 'Auto-refresh paused';
                    }}
                }});
                
                // Manual refresh button
                document.addEventListener('keydown', function(event) {{
                    // If F5 or Ctrl+R is pressed and auto-refresh is disabled, don't prevent refresh
                    if (!autoRefreshEnabled) {{
                        return;
                    }}
                    
                    // Prevent F5 or Ctrl+R from refreshing when auto-refresh is enabled
                    if (event.key === 'F5' || (event.ctrlKey && event.key === 'r')) {{
                        event.preventDefault();
                        alert('Auto-refresh is enabled. To manually refresh, first click "Pause Auto-Refresh".');
                    }}
                }});
            </script>
        </body>
        </html>
        """


monitoring_service = None


@app.on_event("startup")
async def startup_event():
    global monitoring_service
    metrics_dir = "/output/metrics"  # This should match the directory in compute_vectors.py
    monitoring_service = MonitoringService(metrics_dir)

    # Load initial data immediately
    await monitoring_service.update_metrics()

    # Start background task to update metrics
    asyncio.create_task(periodic_metrics_update())


async def periodic_metrics_update():
    while True:
        await monitoring_service.update_metrics()
        await asyncio.sleep(5)  # Update every 5 seconds


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    if not monitoring_service:
        raise HTTPException(status_code=503,
                            detail="Monitoring service not initialized")
    return monitoring_service.get_dashboard_html()


@app.get("/api/metrics")
async def get_metrics():
    if not monitoring_service:
        raise HTTPException(status_code=503,
                            detail="Monitoring service not initialized")
    return {
        "aggregate_metrics": monitoring_service.aggregate_metrics,
        "worker_metrics": monitoring_service.worker_metrics
    }


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
