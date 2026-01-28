// Package main provides an example Go client for integrating with SkyPilot's REST API.
//
// This example demonstrates:
// - Submitting managed jobs via the REST API
// - Polling for job status
// - Handling preemption and error states
// - Mapping SkyPilot states to orchestrator states
//
// For production use, consider:
// - Adding exponential backoff to polling
// - Implementing proper context cancellation
// - Adding structured logging
// - Using a configuration file for API settings
package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ============================================================================
// Configuration
// ============================================================================

const (
	// DefaultAPIURL is the default SkyPilot API server URL (local)
	DefaultAPIURL = "http://localhost:46580"

	// APIVersion is the SkyPilot API version this client is compatible with
	APIVersion = "31"

	// DefaultPollInterval is the recommended polling interval for job status
	DefaultPollInterval = 15 * time.Second

	// RequestPollInterval is the interval for polling request completion
	RequestPollInterval = 2 * time.Second
)

// ============================================================================
// Job Status Constants
// ============================================================================

// ManagedJobStatus represents the possible states of a SkyPilot managed job.
// These map to sky.jobs.state.ManagedJobStatus in the Python codebase.
type ManagedJobStatus string

const (
	// StatusPending - Job is waiting for a scheduler slot
	StatusPending ManagedJobStatus = "PENDING"

	// StatusSubmitted - Job has been submitted to the scheduler
	StatusSubmitted ManagedJobStatus = "SUBMITTED"

	// StatusStarting - Cluster is being launched for the job
	StatusStarting ManagedJobStatus = "STARTING"

	// StatusRunning - Job is actively executing
	StatusRunning ManagedJobStatus = "RUNNING"

	// StatusRecovering - Job was preempted and is auto-recovering
	// This indicates a spot instance interruption was detected
	StatusRecovering ManagedJobStatus = "RECOVERING"

	// StatusCancelling - Job cancellation is in progress
	StatusCancelling ManagedJobStatus = "CANCELLING"

	// StatusSucceeded - Job completed successfully
	StatusSucceeded ManagedJobStatus = "SUCCEEDED"

	// StatusCancelled - Job was cancelled by user
	StatusCancelled ManagedJobStatus = "CANCELLED"

	// StatusFailed - Job failed (user code returned non-zero exit code)
	StatusFailed ManagedJobStatus = "FAILED"

	// StatusFailedSetup - Job failed during setup phase
	StatusFailedSetup ManagedJobStatus = "FAILED_SETUP"

	// StatusFailedPrechecks - Job failed prechecks (e.g., invalid resources)
	StatusFailedPrechecks ManagedJobStatus = "FAILED_PRECHECKS"

	// StatusFailedDriver - Internal SkyPilot driver error
	StatusFailedDriver ManagedJobStatus = "FAILED_DRIVER"

	// StatusFailedController - Jobs controller encountered an error
	StatusFailedController ManagedJobStatus = "FAILED_CONTROLLER"

	// StatusFailedNoResource - No resources available to run the job
	StatusFailedNoResource ManagedJobStatus = "FAILED_NO_RESOURCE"
)

// IsTerminal returns true if the status represents a final state
func (s ManagedJobStatus) IsTerminal() bool {
	switch s {
	case StatusSucceeded, StatusCancelled, StatusFailed,
		StatusFailedSetup, StatusFailedPrechecks, StatusFailedDriver,
		StatusFailedController, StatusFailedNoResource:
		return true
	}
	return false
}

// IsSuccess returns true if the job completed successfully
func (s ManagedJobStatus) IsSuccess() bool {
	return s == StatusSucceeded
}

// IsRecovering returns true if the job is recovering from preemption
func (s ManagedJobStatus) IsRecovering() bool {
	return s == StatusRecovering
}

// IsRunning returns true if the job is actively running
func (s ManagedJobStatus) IsRunning() bool {
	return s == StatusRunning
}

// ============================================================================
// Request Status (for async API calls)
// ============================================================================

// RequestStatus represents the status of an async API request
type RequestStatus string

const (
	RequestPending   RequestStatus = "PENDING"
	RequestRunning   RequestStatus = "RUNNING"
	RequestSucceeded RequestStatus = "SUCCEEDED"
	RequestFailed    RequestStatus = "FAILED"
)

// ============================================================================
// API Request/Response Types
// ============================================================================

// JobsLaunchBody is the request body for POST /jobs/launch
type JobsLaunchBody struct {
	// Task is the YAML task specification as a string
	Task string `json:"task"`

	// Name is the job name (optional but recommended)
	Name string `json:"name,omitempty"`

	// EnvVars are environment variables to pass to the job
	EnvVars map[string]string `json:"env_vars,omitempty"`

	// Detach if true, returns immediately after submission
	DetachRun bool `json:"detach_run,omitempty"`
}

// JobsQueueBody is the request body for POST /jobs/queue/v2
type JobsQueueBody struct {
	// Refresh forces a status refresh from the cluster
	Refresh bool `json:"refresh"`

	// SkipFinished excludes finished jobs from results
	SkipFinished bool `json:"skip_finished"`

	// AllUsers includes jobs from all users (requires admin)
	AllUsers bool `json:"all_users"`

	// JobIDs filters to specific job IDs (empty = all jobs)
	JobIDs []int64 `json:"job_ids,omitempty"`
}

// JobsCancelBody is the request body for POST /jobs/cancel
type JobsCancelBody struct {
	// Name cancels jobs matching this name pattern
	Name string `json:"name,omitempty"`

	// JobIDs cancels specific job IDs
	JobIDs []int64 `json:"job_ids,omitempty"`

	// All cancels all jobs (use with caution)
	All bool `json:"all,omitempty"`
}

// RequestPayload represents an API request status response
type RequestPayload struct {
	RequestID   string  `json:"request_id"`
	Name        string  `json:"name"`
	Entrypoint  string  `json:"entrypoint"`
	RequestBody string  `json:"request_body"`
	Status      string  `json:"status"`
	CreatedAt   float64 `json:"created_at"`
	FinishedAt  float64 `json:"finished_at,omitempty"`
	UserID      string  `json:"user_id"`
	ReturnValue string  `json:"return_value"` // JSON-encoded result
	Error       string  `json:"error,omitempty"`
	Pid         *int    `json:"pid,omitempty"`
	UserName    string  `json:"user_name,omitempty"`
	ClusterName string  `json:"cluster_name,omitempty"`
	StatusMsg   string  `json:"status_msg,omitempty"`
}

// ManagedJobRecord represents a job's status from /jobs/queue/v2.
// The API returns JSON with these fields.
type ManagedJobRecord struct {
	JobID          int64   `json:"job_id"`
	JobName        string  `json:"job_name"`
	UserName       string  `json:"user_name,omitempty"`
	Status         string  `json:"status"` // String value from ManagedJobStatus
	ScheduleState  string  `json:"schedule_state,omitempty"`
	FailureReason  string  `json:"failure_reason,omitempty"`
	RecoveryCount  int     `json:"recovery_count,omitempty"`
	SubmittedAt    float64 `json:"submitted_at,omitempty"`
	StartAt        float64 `json:"start_at,omitempty"`
	EndAt          float64 `json:"end_at,omitempty"`
	LastRecoveryAt float64 `json:"last_recovered_at,omitempty"`
	JobDuration    float64 `json:"job_duration,omitempty"`
	Resources      string  `json:"resources,omitempty"`
	Cloud          string  `json:"cloud,omitempty"`
	Region         string  `json:"region,omitempty"`
	Zone           string  `json:"zone,omitempty"`
}

// GetStatus returns the status as a ManagedJobStatus enum
func (r *ManagedJobRecord) GetStatus() ManagedJobStatus {
	return ManagedJobStatus(r.Status)
}

// ============================================================================
// SkyPilot Client
// ============================================================================

// Client provides methods to interact with the SkyPilot REST API
type Client struct {
	// BaseURL is the SkyPilot API server URL
	BaseURL string

	// HTTPClient is the underlying HTTP client
	HTTPClient *http.Client

	// authHeader is the Authorization header value (if authentication is enabled)
	authHeader string
}

// ClientOption is a function that configures a Client
type ClientOption func(*Client)

// WithBasicAuth configures basic authentication
func WithBasicAuth(username, password string) ClientOption {
	return func(c *Client) {
		credentials := base64.StdEncoding.EncodeToString(
			[]byte(username + ":" + password))
		c.authHeader = "Basic " + credentials
	}
}

// WithBearerToken configures bearer token authentication
func WithBearerToken(token string) ClientOption {
	return func(c *Client) {
		c.authHeader = "Bearer " + token
	}
}

// WithHTTPClient sets a custom HTTP client
func WithHTTPClient(httpClient *http.Client) ClientOption {
	return func(c *Client) {
		c.HTTPClient = httpClient
	}
}

// NewClient creates a new SkyPilot API client
func NewClient(baseURL string, opts ...ClientOption) *Client {
	c := &Client{
		BaseURL: strings.TrimSuffix(baseURL, "/"),
		HTTPClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}

	for _, opt := range opts {
		opt(c)
	}

	return c
}

// doRequest performs an HTTP request with proper headers
func (c *Client) doRequest(method, path string, body interface{}) (*http.Response, error) {
	var bodyReader io.Reader
	if body != nil {
		jsonBody, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(jsonBody)
	}

	req, err := http.NewRequest(method, c.BaseURL+path, bodyReader)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-SkyPilot-API-Version", APIVersion)
	if c.authHeader != "" {
		req.Header.Set("Authorization", c.authHeader)
	}

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("execute request: %w", err)
	}

	return resp, nil
}

// ============================================================================
// Health Check
// ============================================================================

// HealthCheck verifies the API server is reachable
func (c *Client) HealthCheck() error {
	resp, err := c.doRequest("GET", "/api/health", nil)
	if err != nil {
		return fmt.Errorf("health check failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("health check returned status %d", resp.StatusCode)
	}

	return nil
}

// ============================================================================
// Job Launch
// ============================================================================

// LaunchJobResult contains the result of a job launch
type LaunchJobResult struct {
	// RequestID is the async request ID for tracking submission progress
	RequestID string

	// JobID is the managed job ID (available after submission completes)
	JobID int64
}

// LaunchResultPayload is the return value from jobs.launch
type LaunchResultPayload struct {
	JobID  *int64 `json:"job_id"`
	Handle string `json:"handle"` // Base64-encoded pickle (we don't need to parse this)
}

// LaunchJob submits a managed job and returns the request ID.
// The caller should then poll WaitForRequest to wait for submission to complete.
func (c *Client) LaunchJob(taskYAML, name string, envVars map[string]string) (string, error) {
	body := JobsLaunchBody{
		Task:      taskYAML,
		Name:      name,
		EnvVars:   envVars,
		DetachRun: true, // Always detach so we can poll
	}

	resp, err := c.doRequest("POST", "/jobs/launch", body)
	if err != nil {
		return "", fmt.Errorf("launch request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("launch failed (status %d): %s",
			resp.StatusCode, string(bodyBytes))
	}

	// Request ID is returned in response header
	requestID := resp.Header.Get("X-Skypilot-Request-Id")
	if requestID == "" {
		return "", fmt.Errorf("no request ID in response headers")
	}

	return requestID, nil
}

// LaunchJobAndGetID submits a managed job and waits for submission to complete,
// returning the job ID. This is the recommended way to launch jobs when you need
// to track them by ID.
func (c *Client) LaunchJobAndGetID(taskYAML, name string, envVars map[string]string) (int64, error) {
	requestID, err := c.LaunchJob(taskYAML, name, envVars)
	if err != nil {
		return 0, err
	}

	// Wait for submission and get the result with job ID
	result, err := c.GetRequestResult(requestID)
	if err != nil {
		return 0, fmt.Errorf("wait for launch: %w", err)
	}

	// Parse the return value to get the job ID
	var launchResult LaunchResultPayload
	if err := json.Unmarshal([]byte(result.ReturnValue), &launchResult); err != nil {
		return 0, fmt.Errorf("parse launch result: %w", err)
	}

	if launchResult.JobID == nil {
		return 0, fmt.Errorf("launch did not return a job ID")
	}

	return *launchResult.JobID, nil
}

// ============================================================================
// Request Status Polling
// ============================================================================

// GetRequestStatus retrieves the status of an async API request
func (c *Client) GetRequestStatus(requestID string) (*RequestPayload, error) {
	path := "/api/status?request_ids=" + url.QueryEscape(requestID)

	resp, err := c.doRequest("GET", path, nil)
	if err != nil {
		return nil, fmt.Errorf("status request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("status request failed (status %d): %s",
			resp.StatusCode, string(bodyBytes))
	}

	var statuses []RequestPayload
	if err := json.NewDecoder(resp.Body).Decode(&statuses); err != nil {
		return nil, fmt.Errorf("decode status response: %w", err)
	}

	if len(statuses) == 0 {
		return nil, fmt.Errorf("no status found for request %s", requestID)
	}

	return &statuses[0], nil
}

// WaitForRequest polls until an async request completes
func (c *Client) WaitForRequest(requestID string) error {
	for {
		status, err := c.GetRequestStatus(requestID)
		if err != nil {
			return fmt.Errorf("get request status: %w", err)
		}

		switch RequestStatus(status.Status) {
		case RequestSucceeded:
			return nil
		case RequestFailed:
			// The error field may be "null" (pickled None) - suggest viewing logs
			errMsg := status.Error
			if errMsg == "" || errMsg == "null" {
				errMsg = fmt.Sprintf("request failed (view logs with: sky api logs %s)", requestID)
			}
			return fmt.Errorf("%s", errMsg)
		default:
			// PENDING or RUNNING - continue polling
			time.Sleep(RequestPollInterval)
		}
	}
}

// ============================================================================
// Job Queue / Status
// ============================================================================

// JobsQueueV2Body is the request body for POST /jobs/queue/v2
type JobsQueueV2Body struct {
	// Refresh forces a status refresh from the cluster
	Refresh bool `json:"refresh"`

	// SkipFinished excludes finished jobs from results
	SkipFinished bool `json:"skip_finished"`

	// AllUsers includes jobs from all users (requires admin)
	AllUsers bool `json:"all_users"`

	// JobIDs filters to specific job IDs (empty = all jobs)
	JobIDs []int64 `json:"job_ids,omitempty"`

	// NameMatch filters by job name pattern
	NameMatch string `json:"name_match,omitempty"`

	// Fields specifies which fields to return (reduces response size)
	Fields []string `json:"fields,omitempty"`

	// Pagination
	Page  int `json:"page,omitempty"`
	Limit int `json:"limit,omitempty"`
}

// JobsQueueV2Response is the response from /jobs/queue/v2
type JobsQueueV2Response struct {
	Jobs          []ManagedJobRecord `json:"jobs"`
	Total         int                `json:"total"`
	TotalNoFilter int                `json:"total_no_filter"`
	StatusCounts  map[string]int     `json:"status_counts"`
}

// GetJobsQueueRequestID initiates a job queue request and returns the request ID.
// The actual job data is retrieved separately via GetRequestResult.
func (c *Client) GetJobsQueueRequestID(jobIDs []int64, skipFinished bool) (string, error) {
	body := JobsQueueV2Body{
		Refresh:      false,
		SkipFinished: skipFinished,
		AllUsers:     false,
		JobIDs:       jobIDs,
	}

	resp, err := c.doRequest("POST", "/jobs/queue/v2", body)
	if err != nil {
		return "", fmt.Errorf("queue request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("queue request failed (status %d): %s",
			resp.StatusCode, string(bodyBytes))
	}

	requestID := resp.Header.Get("X-Skypilot-Request-Id")
	if requestID == "" {
		return "", fmt.Errorf("no request ID in response headers")
	}

	return requestID, nil
}

// GetRequestResult waits for a request to complete and returns the result.
// This is the main method for retrieving async API results.
func (c *Client) GetRequestResult(requestID string) (*RequestPayload, error) {
	path := "/api/get?request_id=" + url.QueryEscape(requestID)

	resp, err := c.doRequest("GET", path, nil)
	if err != nil {
		return nil, fmt.Errorf("get request result: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("request %s not found", requestID)
	}

	if resp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("get result failed (status %d): %s",
			resp.StatusCode, string(bodyBytes))
	}

	var payload RequestPayload
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return nil, fmt.Errorf("decode result: %w", err)
	}

	return &payload, nil
}

// GetJobStatus fetches the status of specific jobs by their IDs.
// Returns the jobs matching the given IDs.
func (c *Client) GetJobStatus(jobIDs []int64) ([]ManagedJobRecord, error) {
	// Request only the fields we need to minimize response size
	body := JobsQueueV2Body{
		Refresh:      false,
		SkipFinished: false,
		AllUsers:     false,
		JobIDs:       jobIDs,
		Fields: []string{
			"job_id", "job_name", "status", "failure_reason",
			"recovery_count", "submitted_at", "start_at", "end_at",
			"job_duration",
		},
	}

	resp, err := c.doRequest("POST", "/jobs/queue/v2", body)
	if err != nil {
		return nil, fmt.Errorf("queue request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("queue request failed (status %d): %s",
			resp.StatusCode, string(bodyBytes))
	}

	requestID := resp.Header.Get("X-Skypilot-Request-Id")
	if requestID == "" {
		return nil, fmt.Errorf("no request ID in response headers")
	}

	// Wait for the request to complete and get the result
	result, err := c.GetRequestResult(requestID)
	if err != nil {
		return nil, fmt.Errorf("get job status result: %w", err)
	}

	// Parse the return value (JSON string containing jobs)
	var queueResp JobsQueueV2Response
	if err := json.Unmarshal([]byte(result.ReturnValue), &queueResp); err != nil {
		// Try parsing as a plain array (for backwards compatibility)
		var jobs []ManagedJobRecord
		if err2 := json.Unmarshal([]byte(result.ReturnValue), &jobs); err2 != nil {
			return nil, fmt.Errorf("decode jobs response: %w (original: %v)", err2, err)
		}
		return jobs, nil
	}

	return queueResp.Jobs, nil
}

// GetJobsByName fetches jobs matching a name pattern.
func (c *Client) GetJobsByName(namePattern string) ([]ManagedJobRecord, error) {
	body := JobsQueueV2Body{
		Refresh:      false,
		SkipFinished: false,
		AllUsers:     false,
		NameMatch:    namePattern,
		Fields: []string{
			"job_id", "job_name", "status", "failure_reason",
			"recovery_count", "submitted_at", "start_at", "end_at",
		},
	}

	resp, err := c.doRequest("POST", "/jobs/queue/v2", body)
	if err != nil {
		return nil, fmt.Errorf("queue request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("queue request failed (status %d): %s",
			resp.StatusCode, string(bodyBytes))
	}

	requestID := resp.Header.Get("X-Skypilot-Request-Id")
	if requestID == "" {
		return nil, fmt.Errorf("no request ID in response headers")
	}

	result, err := c.GetRequestResult(requestID)
	if err != nil {
		return nil, fmt.Errorf("get jobs by name result: %w", err)
	}

	var queueResp JobsQueueV2Response
	if err := json.Unmarshal([]byte(result.ReturnValue), &queueResp); err != nil {
		var jobs []ManagedJobRecord
		if err2 := json.Unmarshal([]byte(result.ReturnValue), &jobs); err2 != nil {
			return nil, fmt.Errorf("decode jobs response: %w (original: %v)", err2, err)
		}
		return jobs, nil
	}

	return queueResp.Jobs, nil
}

// ============================================================================
// Job Cancellation
// ============================================================================

// CancelJobs cancels jobs by ID
func (c *Client) CancelJobs(jobIDs []int64) (string, error) {
	body := JobsCancelBody{
		JobIDs: jobIDs,
	}

	resp, err := c.doRequest("POST", "/jobs/cancel", body)
	if err != nil {
		return "", fmt.Errorf("cancel request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("cancel request failed (status %d): %s",
			resp.StatusCode, string(bodyBytes))
	}

	requestID := resp.Header.Get("X-Skypilot-Request-Id")
	return requestID, nil
}

// CancelJobsByName cancels jobs matching a name pattern
func (c *Client) CancelJobsByName(namePattern string) (string, error) {
	body := JobsCancelBody{
		Name: namePattern,
	}

	resp, err := c.doRequest("POST", "/jobs/cancel", body)
	if err != nil {
		return "", fmt.Errorf("cancel request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("cancel request failed (status %d): %s",
			resp.StatusCode, string(bodyBytes))
	}

	requestID := resp.Header.Get("X-Skypilot-Request-Id")
	return requestID, nil
}

// ============================================================================
// Orchestrator Integration Example
// ============================================================================

// OrchestratorJobState represents the simplified state for an external orchestrator
type OrchestratorJobState string

const (
	OrchestratorQueued     OrchestratorJobState = "queued"
	OrchestratorRunning    OrchestratorJobState = "running"
	OrchestratorRecovering OrchestratorJobState = "recovering"
	OrchestratorSucceeded  OrchestratorJobState = "succeeded"
	OrchestratorFailed     OrchestratorJobState = "failed"
	OrchestratorCancelled  OrchestratorJobState = "cancelled"
)

// MapToOrchestratorState converts SkyPilot status to orchestrator state
func MapToOrchestratorState(status ManagedJobStatus) OrchestratorJobState {
	switch status {
	case StatusPending, StatusSubmitted, StatusStarting:
		return OrchestratorQueued
	case StatusRunning:
		return OrchestratorRunning
	case StatusRecovering:
		return OrchestratorRecovering
	case StatusSucceeded:
		return OrchestratorSucceeded
	case StatusCancelled, StatusCancelling:
		return OrchestratorCancelled
	default:
		// All FAILED_* states
		return OrchestratorFailed
	}
}

// ============================================================================
// Job Monitoring
// ============================================================================

// WaitForJobCompletionByID polls until a job reaches a terminal state.
// Returns the final job record. This is the recommended method for tracking jobs.
func (c *Client) WaitForJobCompletionByID(jobID int64, pollInterval time.Duration) (*ManagedJobRecord, error) {
	fmt.Printf("Monitoring job ID %d for completion...\n", jobID)

	for {
		jobs, err := c.GetJobStatus([]int64{jobID})
		if err != nil {
			return nil, fmt.Errorf("get job status: %w", err)
		}

		if len(jobs) == 0 {
			// Job might not be visible yet, keep polling
			fmt.Println("  Job not yet visible in queue, waiting...")
			time.Sleep(pollInterval)
			continue
		}

		job := jobs[0]
		status := job.GetStatus()

		fmt.Printf("  Status: %s", status)
		if job.RecoveryCount > 0 {
			fmt.Printf(" (recovered %d times)", job.RecoveryCount)
		}
		if job.FailureReason != "" {
			fmt.Printf(" - %s", job.FailureReason)
		}
		fmt.Println()

		if status.IsTerminal() {
			return &job, nil
		}

		time.Sleep(pollInterval)
	}
}

// WaitForJobCompletion polls until a job reaches a terminal state by name.
// Returns the final job record.
// Note: Prefer WaitForJobCompletionByID when you have the job ID.
func (c *Client) WaitForJobCompletion(jobName string, pollInterval time.Duration) (*ManagedJobRecord, error) {
	fmt.Printf("Monitoring job '%s' for completion...\n", jobName)

	for {
		jobs, err := c.GetJobsByName(jobName)
		if err != nil {
			return nil, fmt.Errorf("get job status: %w", err)
		}

		if len(jobs) == 0 {
			// Job might not be visible yet, keep polling
			fmt.Println("  Job not yet visible in queue, waiting...")
			time.Sleep(pollInterval)
			continue
		}

		// Get the most recent job with this name
		job := jobs[0]
		status := job.GetStatus()

		fmt.Printf("  Status: %s", status)
		if job.RecoveryCount > 0 {
			fmt.Printf(" (recovered %d times)", job.RecoveryCount)
		}
		if job.FailureReason != "" {
			fmt.Printf(" - %s", job.FailureReason)
		}
		fmt.Println()

		if status.IsTerminal() {
			return &job, nil
		}

		time.Sleep(pollInterval)
	}
}

// ============================================================================
// Example Usage
// ============================================================================

func main() {
	// Example task YAML - a simple job that completes quickly
	// Note: Don't specify 'name' in YAML; pass it via the API to track correctly
	taskYAML := `
resources:
  cpus: 1+

run: |
  echo "Hello from Go integration!"
  echo "Job started at: $(date)"
  sleep 5
  echo "Job completed at: $(date)"
`

	// Create client (no auth for local API server)
	client := NewClient(DefaultAPIURL)

	// Verify API server is reachable
	fmt.Println("Checking API server health...")
	if err := client.HealthCheck(); err != nil {
		fmt.Printf("Error: API server not reachable: %v\n", err)
		fmt.Println("\nMake sure the SkyPilot API server is running:")
		fmt.Println("  sky api start")
		return
	}
	fmt.Println("API server is healthy")

	// Generate a unique job name for this run
	jobName := fmt.Sprintf("go-example-%d", time.Now().Unix())

	// Submit the job and get the job ID
	fmt.Printf("\nSubmitting job '%s'...\n", jobName)
	jobID, err := client.LaunchJobAndGetID(taskYAML, jobName, nil)
	if err != nil {
		fmt.Printf("Error submitting job: %v\n", err)
		return
	}
	fmt.Printf("Job submitted! Job ID: %d\n", jobID)

	// Poll for job status until completion using the job ID
	fmt.Println("\n" + strings.Repeat("=", 60))
	fmt.Println("MONITORING JOB STATUS")
	fmt.Println(strings.Repeat("=", 60))

	finalJob, err := client.WaitForJobCompletionByID(jobID, DefaultPollInterval)
	if err != nil {
		fmt.Printf("Error monitoring job: %v\n", err)
		return
	}

	// Report final status
	fmt.Println("\n" + strings.Repeat("=", 60))
	fmt.Println("JOB COMPLETED")
	fmt.Println(strings.Repeat("=", 60))

	status := finalJob.GetStatus()
	orchestratorState := MapToOrchestratorState(status)

	fmt.Printf("Job ID:           %d\n", finalJob.JobID)
	fmt.Printf("Job Name:         %s\n", finalJob.JobName)
	fmt.Printf("Final Status:     %s\n", status)
	fmt.Printf("Orchestrator Map: %s\n", orchestratorState)

	if finalJob.RecoveryCount > 0 {
		fmt.Printf("Recovery Count:   %d (job was preempted and auto-recovered)\n", finalJob.RecoveryCount)
	}

	if finalJob.FailureReason != "" {
		fmt.Printf("Failure Reason:   %s\n", finalJob.FailureReason)
	}

	if finalJob.JobDuration > 0 {
		fmt.Printf("Duration:         %.1f seconds\n", finalJob.JobDuration)
	}

	// Show success/failure message
	if status.IsSuccess() {
		fmt.Println("\n✓ Job completed successfully!")
	} else {
		fmt.Printf("\n✗ Job ended with status: %s\n", status)
	}

	// Demonstrate cancellation (commented out by default)
	fmt.Println("\n" + strings.Repeat("=", 60))
	fmt.Println("ADDITIONAL OPERATIONS")
	fmt.Println(strings.Repeat("=", 60))
	fmt.Println(`
To cancel a running job programmatically:

    requestID, _ := client.CancelJobsByName("job-name")
    client.WaitForRequest(requestID)

To cancel by job ID:

    requestID, _ := client.CancelJobs([]int64{123})
    client.WaitForRequest(requestID)

To view logs via CLI:

    sky jobs logs <job_id>
`)

	// Print state mapping reference
	fmt.Println(strings.Repeat("=", 60))
	fmt.Println("STATE MAPPING REFERENCE")
	fmt.Println(strings.Repeat("=", 60))
	fmt.Println(`
SkyPilot Status        → Orchestrator State
─────────────────────────────────────────────
PENDING                → queued
SUBMITTED              → queued
STARTING               → queued
RUNNING                → running
RECOVERING             → recovering (spot preemption, auto-recovery)
CANCELLING             → cancelled
SUCCEEDED              → succeeded
CANCELLED              → cancelled
FAILED                 → failed
FAILED_SETUP           → failed
FAILED_PRECHECKS       → failed
FAILED_DRIVER          → failed
FAILED_CONTROLLER      → failed
FAILED_NO_RESOURCE     → failed

Key observations:
- RECOVERING indicates spot instance preemption; SkyPilot handles recovery
- failure_reason contains error details for FAILED_* states
- recovery_count tracks total preemptions during job lifetime
`)
}
